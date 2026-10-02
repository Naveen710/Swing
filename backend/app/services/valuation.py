"""DCF and reverse-DCF valuation.

Model (10 years + terminal value):
  * Years 1-5 grow the base cash flow at the stage-1 growth rate.
  * Years 6-10 fade growth linearly from stage-1 down to the terminal rate.
  * Terminal value = CF_10 × (1 + g_t) / (r − g_t), discounted from year 10.
  * Equity value = enterprise value + cash − debt; ÷ shares = intrinsic value per share.

Reverse DCF solves for the stage-1 growth rate that makes the intrinsic value equal the
current share price — i.e. the growth the market is already paying for.

Cash-flow DCFs don't work for banks/NBFCs/insurers (debt is raw material, not financing),
so those default to an earnings-based model and carry a warning.
"""
from __future__ import annotations

import importlib
import logging
import math
import threading
import time
import zlib

import pandas as pd

from app.config import settings

logger = logging.getLogger(__name__)

YEARS = 10
STAGE1_YEARS = 5
FAIR_BAND = 0.15            # within ±15% of intrinsic value = fairly valued
RISK_FREE = 7.0             # India 10-year G-sec, approx. (editable in the UI)
EQUITY_RISK_PREMIUM = 5.5
FINANCIAL_WORDS = ("bank", "financ", "insurance", "nbfc", "lending", "credit", "capital markets", "asset management")


# ── pure maths ───────────────────────────────────────────────────────────────

def growth_path(stage1: float, terminal: float) -> list[float]:
    path = [stage1] * STAGE1_YEARS
    fade_years = YEARS - STAGE1_YEARS
    for k in range(1, fade_years + 1):
        path.append(stage1 + (terminal - stage1) * k / fade_years)
    return path


def run_dcf(base_cf: float, stage1: float, terminal: float, discount: float,
            cash: float, debt: float, shares: float) -> dict:
    """Rates are decimals (0.12 = 12%). Money in rupees."""
    if discount <= terminal:
        raise ValueError("Discount rate must be higher than terminal growth.")
    rows = []
    cf = base_cf
    pv_sum = 0.0
    for year, g in enumerate(growth_path(stage1, terminal), start=1):
        cf = cf * (1 + g)
        factor = 1 / (1 + discount) ** year
        pv = cf * factor
        pv_sum += pv
        rows.append({"year": year, "growth_pct": round(g * 100, 2), "cash_flow": cf, "discount_factor": factor, "present_value": pv})
    terminal_value = cf * (1 + terminal) / (discount - terminal)
    pv_terminal = terminal_value / (1 + discount) ** YEARS
    enterprise = pv_sum + pv_terminal
    equity = enterprise + cash - debt
    per_share = equity / shares if shares > 0 else float("nan")
    return {
        "rows": rows,
        "pv_cash_flows": pv_sum,
        "terminal_value": terminal_value,
        "pv_terminal": pv_terminal,
        "enterprise_value": enterprise,
        "equity_value": equity,
        "per_share": per_share,
        "terminal_share_pct": pv_terminal / enterprise * 100 if enterprise > 0 else None,
    }


def implied_growth(price: float, base_cf: float, terminal: float, discount: float,
                   cash: float, debt: float, shares: float) -> float | None:
    """Stage-1 growth that makes intrinsic value == price (bisection). None if out of range."""
    if base_cf <= 0 or shares <= 0:
        return None

    def gap(g: float) -> float:
        return run_dcf(base_cf, g, terminal, discount, cash, debt, shares)["per_share"] - price

    lo, hi = -0.5, 1.5
    if gap(lo) > 0:
        return lo        # even shrinking 50%/yr justifies the price
    if gap(hi) < 0:
        return None      # would need > 150%/yr — price not explainable by this model
    for _ in range(80):
        mid = (lo + hi) / 2
        if gap(mid) > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def verdict(price: float, intrinsic: float) -> tuple[str, float]:
    upside = (intrinsic / price - 1) * 100
    if intrinsic <= 0:
        return "Overvalued", upside
    if price < intrinsic * (1 - FAIR_BAND):
        return "Undervalued", upside
    if price > intrinsic * (1 + FAIR_BAND):
        return "Overvalued", upside
    return "Fairly valued", upside


def cagr(first: float, last: float, years: int) -> float | None:
    if years <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / years) - 1


# ── data ─────────────────────────────────────────────────────────────────────

def _row(frame, *names) -> list[tuple[int, float]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    for name in names:
        if name in frame.index:
            series = frame.loc[name].dropna()
            out = [(pd.Timestamp(col).year, float(v)) for col, v in series.items()]
            return sorted(out)
    return []


class FinancialsProvider:
    def __init__(self, fundamentals) -> None:
        self.fundamentals = fundamentals
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def get(self, symbol: str, demo: bool) -> dict:
        key = symbol.upper()
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < settings.fundamentals_cache_ttl_minutes * 60:
                return hit[1]
        data = demo_financials(key) if demo else self._fetch(key)
        with self._lock:
            self._cache[key] = (time.time(), data)
        return data

    def _fetch(self, symbol: str) -> dict:
        info = self.fundamentals.get_info(symbol) or {}
        cashflow = income = balance = None
        try:
            ticker = importlib.import_module("yfinance").Ticker(symbol)
            cashflow = ticker.cashflow
            income = ticker.income_stmt
        except Exception as exc:  # noqa: BLE001
            logger.warning("Statements unavailable for %s. %s", symbol, exc)
        if not info.get("sharesOutstanding"):
            try:
                balance = ticker.balance_sheet
            except Exception as exc:  # noqa: BLE001
                logger.warning("Balance sheet unavailable for %s. %s", symbol, exc)
        return assemble(info, cashflow, income, source="yahoo", balance=balance)


def resolve_shares(info: dict, income=None, balance=None) -> tuple[float | None, str | None]:
    """Yahoo often omits sharesOutstanding for smaller/newer NSE listings. Try every other
    published figure, most reliable first, and report which one was used."""
    def num(key):
        try:
            v = float(info.get(key))
            return v if v > 0 and not math.isnan(v) else None
        except (TypeError, ValueError):
            return None

    if num("sharesOutstanding"):
        return num("sharesOutstanding"), "reported shares outstanding"
    if num("impliedSharesOutstanding"):
        return num("impliedSharesOutstanding"), "implied shares outstanding"
    price = num("currentPrice") or num("regularMarketPrice") or num("previousClose")
    if num("marketCap") and price:
        return num("marketCap") / price, "market cap ÷ share price"
    for row in ("Ordinary Shares Number", "Share Issued"):
        latest = _row(balance, row)
        if latest and latest[-1][1] > 0:
            return latest[-1][1], f"balance sheet ({row.lower()}, FY{str(latest[-1][0])[-2:]})"
    for row in ("Diluted Average Shares", "Basic Average Shares"):
        latest = _row(income, row)
        if latest and latest[-1][1] > 0:
            return latest[-1][1], f"income statement ({row.lower()}, FY{str(latest[-1][0])[-2:]})"
    eps, ni = num("trailingEps"), num("netIncomeToCommon")
    if eps and ni:
        return ni / eps, "net profit ÷ EPS (estimate)"
    return None, None


def assemble(info: dict, cashflow, income, source: str, balance=None) -> dict:
    fcf = _row(cashflow, "Free Cash Flow")
    ocf = _row(cashflow, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
    capex = _row(cashflow, "Capital Expenditure")
    if not fcf and ocf and capex:
        cap = dict(capex)
        fcf = [(y, v + cap[y]) for y, v in ocf if y in cap]   # capex is reported negative
    net_income = _row(income, "Net Income", "Net Income Common Stockholders")
    revenue = _row(income, "Total Revenue", "Operating Revenue")

    def num(key):
        try:
            v = float(info.get(key))
            return None if math.isnan(v) else v
        except (TypeError, ValueError):
            return None

    shares, shares_source = resolve_shares(info, income, balance)
    years = sorted({y for y, _ in fcf + net_income + revenue})
    lookup = lambda pairs: dict(pairs)  # noqa: E731
    history = [
        {"year": y, "revenue": lookup(revenue).get(y), "net_income": lookup(net_income).get(y),
         "operating_cash_flow": lookup(ocf).get(y), "capex": lookup(capex).get(y), "free_cash_flow": lookup(fcf).get(y)}
        for y in years
    ]
    return {
        "source": source,
        "name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "currency": info.get("financialCurrency") or "INR",
        "price": num("currentPrice") or num("regularMarketPrice"),
        "shares": shares,
        "shares_source": shares_source,
        "cash": num("totalCash") or 0.0,
        "debt": num("totalDebt") or 0.0,
        "beta": num("beta"),
        "ttm_fcf": num("freeCashflow"),
        "ttm_net_income": num("netIncomeToCommon"),
        "history": history,
    }


def demo_financials(symbol: str) -> dict:
    """Deterministic, clearly-labelled fake statements so the page works in demo mode."""
    seed = zlib.crc32(symbol.encode())
    base_rev = 5e9 + (seed % 900) * 1e8
    growth = 0.06 + (seed % 13) / 100
    margin = 0.08 + (seed % 9) / 100
    years = [2022, 2023, 2024, 2025]
    hist = []
    for i, y in enumerate(years):
        rev = base_rev * (1 + growth) ** i
        ni = rev * margin
        ocf = ni * 1.15
        capex = -rev * 0.04
        hist.append({"year": y, "revenue": rev, "net_income": ni, "operating_cash_flow": ocf, "capex": capex, "free_cash_flow": ocf + capex})
    shares = 1e8 + (seed % 50) * 1e7
    price = hist[-1]["net_income"] / shares * (18 + seed % 30)
    return {
        "source": "demo", "name": f"{symbol.removesuffix('.NS')} (demo data)", "sector": "Industrials",
        "industry": "Demo", "currency": "INR", "price": round(price, 2), "shares": shares,
        "shares_source": "demo",
        "cash": base_rev * 0.1, "debt": base_rev * 0.15, "beta": 0.9 + (seed % 7) / 10,
        "ttm_fcf": hist[-1]["free_cash_flow"], "ttm_net_income": hist[-1]["net_income"], "history": hist,
    }


# ── orchestration ────────────────────────────────────────────────────────────

def is_financial(data: dict) -> bool:
    text = f"{data.get('sector') or ''} {data.get('industry') or ''}".lower()
    return any(word in text for word in FINANCIAL_WORDS)


def default_assumptions(data: dict) -> dict:
    hist = data["history"]
    notes = []
    financial = is_financial(data)
    method = "earnings" if financial else "fcf"
    fcfs = [h["free_cash_flow"] for h in hist if h["free_cash_flow"] is not None]
    nis = [h["net_income"] for h in hist if h["net_income"] is not None]
    recent_fcf = fcfs[-3:]
    if method == "fcf" and (not recent_fcf or sum(recent_fcf) / len(recent_fcf) <= 0):
        method = "earnings"
        notes.append("Free cash flow has been negative or missing, so the model uses net profit instead.")
    if financial:
        notes.append("Banks, NBFCs and insurers don't have meaningful free cash flow — valued on net profit instead. Treat with extra caution; a P/B or excess-return model suits them better.")

    if method == "fcf":
        base = sum(recent_fcf) / len(recent_fcf)
        notes.append(f"Base cash flow = average free cash flow of the last {len(recent_fcf)} years (smooths lumpy capex).")
    else:
        recent = nis[-3:] or [data.get("ttm_net_income") or 0]
        base = data.get("ttm_net_income") or recent[-1]
        notes.append("Base cash flow = latest net profit.")

    revs = [(h["year"], h["revenue"]) for h in hist if h["revenue"]]
    rev_cagr = cagr(revs[0][1], revs[-1][1], revs[-1][0] - revs[0][0]) if len(revs) >= 2 else None
    profit_series = [(h["year"], h["net_income"]) for h in hist if h["net_income"]]
    ni_cagr = cagr(profit_series[0][1], profit_series[-1][1], profit_series[-1][0] - profit_series[0][0]) if len(profit_series) >= 2 else None
    candidates = [g for g in (rev_cagr, ni_cagr) if g is not None]
    hist_growth = min(candidates) if candidates else None
    stage1 = min(max(hist_growth if hist_growth is not None else 0.10, 0.03), 0.20)
    notes.append(
        f"Growth (years 1–5) = the lower of revenue and profit CAGR ({_p(hist_growth)}), kept between 3% and 20%."
        if hist_growth is not None else "No growth history — growth defaults to 10%."
    )

    beta = data.get("beta")
    beta_used = min(max(beta, 0.6), 2.0) if beta else 1.0
    discount = min(max((RISK_FREE + beta_used * EQUITY_RISK_PREMIUM) / 100, 0.10), 0.16)
    notes.append(f"Discount rate = {RISK_FREE}% risk-free + beta {beta_used:.2f} × {EQUITY_RISK_PREMIUM}% equity premium, kept between 10% and 16%.")
    return {
        "method": method, "base_cash_flow": base, "growth_pct": round(stage1 * 100, 1),
        "terminal_growth_pct": 5.0, "discount_rate_pct": round(discount * 100, 1),
        "margin_of_safety_pct": 25.0, "notes": notes,
        "historical_growth_pct": None if hist_growth is None else round(hist_growth * 100, 1),
        "revenue_cagr_pct": None if rev_cagr is None else round(rev_cagr * 100, 1),
        "profit_cagr_pct": None if ni_cagr is None else round(ni_cagr * 100, 1),
    }


def _p(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def value_company(data: dict, price: float, overrides: dict) -> dict:
    defaults = default_assumptions(data)
    a = {**defaults, **{k: v for k, v in overrides.items() if v is not None}}
    if overrides.get("method") and overrides.get("base_cash_flow") is None and overrides["method"] != defaults["method"]:
        hist = data["history"]
        if a["method"] == "fcf":
            fcfs = [h["free_cash_flow"] for h in hist if h["free_cash_flow"] is not None][-3:]
            a["base_cash_flow"] = sum(fcfs) / len(fcfs) if fcfs else 0
        else:
            a["base_cash_flow"] = data.get("ttm_net_income") or next((h["net_income"] for h in reversed(hist) if h["net_income"]), 0)

    g, gt, r = a["growth_pct"] / 100, a["terminal_growth_pct"] / 100, a["discount_rate_pct"] / 100
    shares, cash, debt = data["shares"], data["cash"], data["debt"]
    if overrides.get("shares"):
        shares = overrides["shares"]
    base = a["base_cash_flow"]
    warnings = [n for n in defaults["notes"] if n.startswith(("Banks", "Free cash flow has been negative"))]
    if not shares:
        return {"available": False, "needs_shares": True,
                "reason": "The data source doesn't publish this company's share count, so a per-share value can't be computed automatically. Enter the number of shares below — you'll find it on the company's NSE/BSE page or in its latest annual report."}
    if base is None or base <= 0:
        return {"available": False, "reason": "The company's cash flows/profits are negative — a DCF can't value a business that doesn't yet generate cash. Use the reverse DCF only after it turns cash-positive."}
    if r <= gt:
        return {"available": False, "reason": "Discount rate must be higher than terminal growth."}

    base_case = run_dcf(base, g, gt, r, cash, debt, shares)
    intrinsic = base_case["per_share"]
    label, upside = verdict(price, intrinsic)
    scenarios = {
        "bear": run_dcf(base, max(g - 0.05, -0.2), gt, r + 0.01, cash, debt, shares)["per_share"],
        "base": intrinsic,
        "bull": run_dcf(base, g + 0.05, gt, max(r - 0.01, gt + 0.01), cash, debt, shares)["per_share"],
    }
    rates = [r - 0.02, r - 0.01, r, r + 0.01, r + 0.02]
    terminals = [gt - 0.01, gt - 0.005, gt, gt + 0.005, gt + 0.01]
    grid = [[(run_dcf(base, g, t, d, cash, debt, shares)["per_share"] if d > t else None) for t in terminals] for d in rates]

    implied = implied_growth(price, base, gt, r, cash, debt, shares)
    hist_g = defaults["historical_growth_pct"]
    if implied is None:
        reverse_text = "Even 150% annual growth wouldn't justify the current price under these assumptions — the price is driven by something other than near-term cash flows."
    else:
        ip = implied * 100
        if hist_g is None:
            reverse_text = f"The price assumes about {ip:.1f}% annual growth for 5 years, fading to {gt*100:.1f}%."
        elif ip > hist_g + 5:
            reverse_text = f"The market is pricing {ip:.1f}% growth — well above the company's historical {hist_g:.1f}%. The stock needs to accelerate to justify today's price."
        elif ip < hist_g - 5:
            reverse_text = f"The market is pricing only {ip:.1f}% growth versus {hist_g:.1f}% historically — expectations look undemanding if the business keeps its record."
        else:
            reverse_text = f"The market is pricing {ip:.1f}% growth, in line with the historical {hist_g:.1f}% — expectations look reasonable."

    if base_case["terminal_share_pct"] and base_case["terminal_share_pct"] > 75:
        warnings.append(f"{base_case['terminal_share_pct']:.0f}% of the value comes from the terminal value — the result is very sensitive to long-term assumptions.")

    return {
        "available": True,
        "assumptions": {k: a[k] for k in ("method", "base_cash_flow", "growth_pct", "terminal_growth_pct", "discount_rate_pct", "margin_of_safety_pct")},
        "defaults": {k: defaults[k] for k in ("method", "base_cash_flow", "growth_pct", "terminal_growth_pct", "discount_rate_pct", "margin_of_safety_pct")},
        "historical": {k: defaults[k] for k in ("historical_growth_pct", "revenue_cagr_pct", "profit_cagr_pct")},
        "intrinsic_value": round(intrinsic, 2),
        "buy_below": round(intrinsic * (1 - a["margin_of_safety_pct"] / 100), 2),
        "verdict": label,
        "upside_pct": round(upside, 1),
        "fair_band_pct": FAIR_BAND * 100,
        "dcf": {
            "rows": [{**row, "cash_flow": round(row["cash_flow"]), "present_value": round(row["present_value"]), "discount_factor": round(row["discount_factor"], 4)} for row in base_case["rows"]],
            "pv_cash_flows": round(base_case["pv_cash_flows"]),
            "terminal_value": round(base_case["terminal_value"]),
            "pv_terminal": round(base_case["pv_terminal"]),
            "enterprise_value": round(base_case["enterprise_value"]),
            "cash": round(cash), "debt": round(debt),
            "equity_value": round(base_case["equity_value"]),
            "shares": shares,
            "terminal_share_pct": None if base_case["terminal_share_pct"] is None else round(base_case["terminal_share_pct"], 1),
        },
        "scenarios": {k: round(v, 2) for k, v in scenarios.items()},
        "sensitivity": {
            "discount_rates_pct": [round(x * 100, 1) for x in rates],
            "terminal_growth_pct": [round(x * 100, 1) for x in terminals],
            "values": [[None if v is None else round(v, 2) for v in row] for row in grid],
        },
        "reverse_dcf": {
            "implied_growth_pct": None if implied is None else round(implied * 100, 1),
            "historical_growth_pct": hist_g,
            "assumed_growth_pct": a["growth_pct"],
            "interpretation": reverse_text,
        },
        "warnings": warnings,
        "default_notes": [n for n in defaults["notes"] if n not in warnings],
    }


def build_valuation(scanner, provider: FinancialsProvider, symbol: str, overrides: dict) -> dict | None:
    from app.schemas import MarketCapBucket
    from app.services.universe import StockListing, find_listing

    listing = find_listing(symbol)
    sym = listing.symbol if listing else (symbol.upper() if "." in symbol else f"{symbol.upper()}.NS")
    demo = settings.market_data_provider == "demo" or not settings.enable_external_overlays
    data = provider.get(sym, demo)

    price = None
    try:
        frame = scanner.market_data.get_history(
            listing or StockListing(sym, sym, "Unknown", MarketCapBucket.SMALL), lookback_days=30)
        price = float(frame["Close"].iloc[-1])
    except Exception:  # noqa: BLE001
        price = data.get("price")
    if demo:
        price = data.get("price")      # demo prices and demo statements must match
    if not price:
        return None

    shares = overrides.get("shares") or data.get("shares")
    shares_source = "entered by you" if overrides.get("shares") else data.get("shares_source")
    result = {
        "symbol": sym,
        "company_name": data.get("name") or (listing.company_name if listing else sym),
        "sector": data.get("sector") or (listing.sector if listing else None),
        "industry": data.get("industry"),
        "source": data["source"],
        "price": round(price, 2),
        "market_cap_cr": round(price * shares / 1e7) if shares else None,
        "is_financial": is_financial(data),
        "cash": data.get("cash"), "debt": data.get("debt"), "shares": shares, "shares_source": shares_source,
        "history": data["history"],
    }
    if not data["history"] and not data.get("ttm_fcf") and not data.get("ttm_net_income"):
        result["valuation"] = {"available": False, "reason": "No financial statements are published for this stock on the data source."}
        return result
    result["valuation"] = value_company(data, price, overrides)
    return result
