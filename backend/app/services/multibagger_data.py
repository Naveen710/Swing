"""Deep fundamental data for the multibagger framework: fetch, demo and cache.

One deep fetch = 5 Yahoo calls (info, income_stmt, balance_sheet, cashflow,
quarterly_income_stmt). Results are cached as artifacts `mb_fin:{SYMBOL}` for
MULTIBAGGER_FUND_TTL_DAYS so a repeat run costs seconds, not minutes.
"""
from __future__ import annotations

import importlib
import logging
import math
import zlib
from datetime import UTC, datetime, timedelta

import pandas as pd

from app.config import settings
from app.services import valuation as val
from app.services.db import load_artifact, save_artifact

logger = logging.getLogger(__name__)

INFO_KEYS = ("marketCap", "netIncomeToCommon", "trailingPE", "debtToEquity", "revenueGrowth", "sector",
             "industry", "sharesOutstanding", "impliedSharesOutstanding", "currentPrice", "regularMarketPrice",
             "previousClose", "trailingEps", "totalCash", "totalDebt", "freeCashflow", "beta", "longName",
             "shortName", "financialCurrency")

ANNUAL_ROWS = {
    "revenue": (("income", "Total Revenue"), ("income", "Operating Revenue")),
    "ebit": (("income", "EBIT"), ("income", "Operating Income")),
    "net_income": (("income", "Net Income"), ("income", "Net Income Common Stockholders")),
    "interest_expense": (("income", "Interest Expense"), ("income", "Interest Expense Non Operating")),
    "shares_diluted": (("income", "Diluted Average Shares"), ("income", "Basic Average Shares")),
    "total_assets": (("balance", "Total Assets"),),
    "current_liabilities": (("balance", "Current Liabilities"), ("balance", "Total Current Liabilities")),
    "total_debt": (("balance", "Total Debt"),),
    "equity": (("balance", "Stockholders Equity"), ("balance", "Common Stock Equity")),
    "shares_outstanding": (("balance", "Ordinary Shares Number"), ("balance", "Share Issued")),
    "cfo": (("cash", "Operating Cash Flow"), ("cash", "Cash Flow From Continuing Operating Activities")),
    "capex": (("cash", "Capital Expenditure"),),
    "fcf": (("cash", "Free Cash Flow"),),
}


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def annual_from_statements(income, balance, cash) -> list[dict]:
    frames = {"income": income, "balance": balance, "cash": cash}
    values: dict[str, dict[int, float]] = {}
    years: set[int] = set()
    for field, options in ANNUAL_ROWS.items():
        for frame_name, row in options:
            pts = val._row(frames[frame_name], row)
            if pts:
                values[field] = dict(pts)
                years |= {y for y, _ in pts}
                break
    out = []
    for y in sorted(years):
        row = {"year": y}
        for field in ANNUAL_ROWS:
            row[field] = _clean(values.get(field, {}).get(y))
        if row["fcf"] is None and row["cfo"] is not None and row["capex"] is not None:
            row["fcf"] = row["cfo"] + row["capex"]
        out.append(row)
    return out


def quarterly_from_statement(q) -> list[dict]:
    if q is None or getattr(q, "empty", True):
        return []
    rev = next((r for r in ("Total Revenue", "Operating Revenue") if r in q.index), None)
    ni = next((r for r in ("Net Income", "Net Income Common Stockholders") if r in q.index), None)
    cols = sorted(q.columns, key=lambda c: pd.Timestamp(c))
    out = []
    for c in cols:
        r = _clean(float(q.at[rev, c])) if rev and pd.notna(q.at[rev, c]) else None
        n = _clean(float(q.at[ni, c])) if ni and pd.notna(q.at[ni, c]) else None
        if r is None and n is None:
            continue
        out.append({"period": pd.Timestamp(c).date().isoformat(), "revenue": r, "net_income": n})
    return out


def fetch_deep_financials(symbol: str, fundamentals) -> dict:
    """Live Yahoo fetch. Network errors propagate to the caller (→ insufficient data)."""
    info = fundamentals.get_info(symbol) or {}
    ticker = importlib.import_module("yfinance").Ticker(symbol)
    income = ticker.income_stmt
    balance = ticker.balance_sheet
    cash = ticker.cashflow
    try:
        quarterly = ticker.quarterly_income_stmt
    except Exception as exc:  # noqa: BLE001
        logger.info("Quarterly results unavailable for %s. %s", symbol, exc)
        quarterly = None
    return {
        "source": "yahoo",
        "info": {k: _clean(info.get(k)) for k in INFO_KEYS if info.get(k) is not None},
        "annual": annual_from_statements(income, balance, cash),
        "quarterly": quarterly_from_statement(quarterly),
        "valuation_data": val.assemble(info, cash, income, source="yahoo", balance=balance),
    }


def demo_deep_financials(symbol: str) -> dict:
    """Deterministic fake statements (seeded by the symbol) so the page works offline.
    Most symbols land inside the size band; a few trip each kill-switch."""
    seed = zlib.crc32(symbol.upper().encode())
    rev0 = 3e9 + (seed % 700) * 1e8
    g = 0.04 + (seed % 23) / 100              # 4%–26% revenue growth
    margin0 = 0.08 + (seed % 7) / 100
    margin_step = ((seed >> 3) % 5 - 1) / 200  # −0.5 … +1.5 pp a year
    debt_ratio = ((seed >> 5) % 12) / 10       # D/E 0.0 … 1.1
    years = [2022, 2023, 2024, 2025]
    shares0 = 5e7 + (seed % 40) * 1e6
    dilution = 0.30 if seed % 29 == 0 else 0.0
    loss = seed % 31 == 0
    annual = []
    for i, y in enumerate(years):
        rev = rev0 * (1 + g) ** i
        ebit = rev * (margin0 + margin_step * i)
        ni = ebit * 0.72 if not (loss and i == 3) else -rev * 0.02
        equity = rev * 0.45 * (1 + 0.08 * i)
        debt = equity * debt_ratio
        cfo = ni * (0.6 + (seed % 6) / 10) if ni > 0 else ni
        annual.append({
            "year": y, "revenue": rev, "ebit": ebit, "net_income": ni,
            "interest_expense": debt * 0.09 if debt > 1e7 else None,
            "shares_diluted": shares0 * (1 + dilution * i / 3), "total_assets": equity + debt + rev * 0.25,
            "current_liabilities": rev * 0.22, "total_debt": debt, "equity": equity,
            "shares_outstanding": shares0 * (1 + dilution * i / 3), "cfo": cfo, "capex": -rev * 0.05,
            "fcf": cfo - rev * 0.05,
        })
    quarterly = []
    q_rev = annual[-2]["revenue"] / 4
    for k in range(6):
        r = q_rev * (1 + g) ** (k / 4) * (1 + (0.04 if k >= 4 and seed % 3 == 0 else 0))
        period = (pd.Timestamp("2024-03-31") + pd.offsets.QuarterEnd(k)).date().isoformat()
        quarterly.append({"period": period,
                          "revenue": r, "net_income": r * (margin0 + margin_step * 3) * 0.72})
    mcap = (300 + (seed % 2000) * 10) * 1e7            # ₹300 … ₹20,290 Cr
    shares = annual[-1]["shares_outstanding"]
    info = {
        "marketCap": mcap, "netIncomeToCommon": annual[-1]["net_income"],
        "trailingPE": mcap / annual[-1]["net_income"] if annual[-1]["net_income"] > 0 else None,
        "debtToEquity": debt_ratio * 100, "revenueGrowth": g, "sector": "Industrials", "industry": "Demo",
        "sharesOutstanding": shares, "currentPrice": mcap / shares,
    }
    cash_df = pd.DataFrame({pd.Timestamp(f"{a['year']}-03-31"): {"Operating Cash Flow": a["cfo"], "Capital Expenditure": a["capex"]} for a in annual})
    inc_df = pd.DataFrame({pd.Timestamp(f"{a['year']}-03-31"): {"Total Revenue": a["revenue"], "Net Income": a["net_income"]} for a in annual})
    vinfo = {**info, "totalCash": annual[-1]["revenue"] * 0.08, "totalDebt": annual[-1]["total_debt"], "beta": 1.0,
             "longName": f"{symbol.removesuffix('.NS')} (demo data)"}
    return {"source": "demo", "info": {k: v for k, v in info.items() if v is not None}, "annual": annual,
            "quarterly": quarterly, "valuation_data": val.assemble(vinfo, cash_df, inc_df, source="demo")}


def get_deep_financials(symbol: str, *, demo: bool, refresh: bool = False, fundamentals=None, fetcher=None) -> dict:
    """Cached deep data. Never raises: a failed fetch returns {"error": ...}."""
    key = f"mb_fin:{symbol.upper()}"
    if demo:
        return demo_deep_financials(symbol)
    if not refresh:
        try:
            hit = load_artifact(key)
        except Exception:  # noqa: BLE001
            hit = None
        if hit and hit.get("fetched_at"):
            age = datetime.now(UTC) - datetime.fromisoformat(hit["fetched_at"])
            if age < timedelta(days=settings.multibagger_fund_ttl_days):
                return hit
    try:
        data = (fetcher or fetch_deep_financials)(symbol, fundamentals)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Multibagger fundamentals failed for %s. %s", symbol, exc)
        return {"error": str(exc)[:120] or "fetch failed", "annual": [], "quarterly": [], "info": {}}
    data["fetched_at"] = datetime.now(UTC).isoformat()
    try:
        save_artifact(key, data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not cache %s. %s", key, exc)
    return data
