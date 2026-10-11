"""Multibagger framework: pure scoring rules (no network, no database).

Spec: docs/features/multibagger-framework/spec.md. Everything here works on plain inputs
so it can be unit-tested with known answers:

  annual    list of dicts, oldest FY first: year, revenue, ebit, net_income, interest_expense,
            total_assets, current_liabilities, total_debt, equity, cfo, capex, fcf,
            shares_outstanding, shares_diluted (any value may be None)
  quarterly list of dicts, oldest first: period, revenue, net_income
  info      subset of Yahoo `info`: marketCap, netIncomeToCommon, trailingPE, debtToEquity,
            revenueGrowth, sector, industry, sharesOutstanding, currentPrice
  frame     daily OHLCV DataFrame (tz-naive index) · bench: benchmark close Series

Missing data is never a silent 0: each metric says whether it is scored neutral (50) or
penalised (25), and records a data-gap code.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.services import valuation as val
from app.services.trend_overlays import build_rs_line
from app.services.vcp_scanner import scan_stock, trend_template

# ── constants ────────────────────────────────────────────────────────────────
WEIGHTS = {"growth": 30, "quality": 25, "valuation": 15, "tailwind": 15, "price": 15}
LAYER_LABELS = {"growth": "Growth & inflection", "quality": "Quality", "valuation": "Valuation sanity",
                "tailwind": "Tailwind & leadership", "price": "Price confirmation"}
NEUTRAL, PENALISED = 50.0, 25.0
MCAP_MIN_CR, MCAP_MAX_CR, MCAP_LATE_CR = 500.0, 20_000.0, 15_000.0
MAX_DE = 1.0
MAX_DILUTION = 0.25
MIN_COVERAGE = 60.0
WEAK_LAYER = 30.0
TIERS = [(75.0, "A", "High-conviction candidate"), (60.0, "B", "Candidate"), (45.0, "C", "Watchlist"), (0.0, "D", "Not now")]
TERMINAL_GROWTH = 0.05

GAP_TEXT = {
    "short_history": "Only 2 years of annual results — CAGRs use a 2-year span",
    "turnaround_base": "Profit was ≤ 0 in the base year — profit CAGR scored neutral (50)",
    "quarterly_unknown": "No usable quarterly results — acceleration scored neutral (50)",
    "ce_proxy": "No current liabilities — capital employed estimated as equity + debt",
    "debt_unknown": "Debt/equity not reported — debt metric penalised (25)",
    "cfo_unknown": "Operating cash flow not reported — cash conversion penalised (25)",
    "dilution_unknown": "Share count history missing — dilution not checked",
    "no_shares": "No share count — reverse DCF scored neutral (50)",
    "no_pe": "No P/E available — PEG and earnings yield scored neutral (50)",
    "unknown_sector": "Sector unknown or too small — sector metrics scored neutral (50)",
    "no_reference_run": "No universe run yet — tailwind layer scored neutral (50)",
    "margin_unknown": "Operating profit (EBIT) missing — margin and ROCE metrics scored neutral",
    "interest_unknown": "No interest line while the company has debt — interest cover penalised (25)",
}


def lin(x: float, lo: float, hi: float) -> float:
    """0 at `lo`, 100 at `hi`, clipped, linear in between (lo > hi: lower is better)."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        raise ValueError("lin() needs a number")
    if hi == lo:
        return 100.0 if x >= hi else 0.0
    return float(min(100.0, max(0.0, (x - lo) / (hi - lo) * 100.0)))


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _series(annual: list[dict], key: str) -> list[tuple[int, float]]:
    return [(a["year"], float(a[key])) for a in annual if _num(a.get(key)) is not None]


def _fmt_pct(v: float | None, dp: int = 1, signed: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v:+.{dp}f}%" if signed else f"{v:.{dp}f}%"


def _fmt_pp(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f} pp"


def metric(key: str, label: str, weight: float, value: float | None, score: float | None,
           display: str, missing: str = "neutral", note: str = "") -> dict:
    """Build one metric row. score=None → filled per `missing` ("neutral" 50 / "penalised" 25)."""
    if score is None:
        status = missing
        score = NEUTRAL if missing == "neutral" else PENALISED
        display = display if display not in ("", None) else "—"
    else:
        status = "ok"
    return {"key": key, "label": label, "weight": weight, "value": None if value is None else round(float(value), 4),
            "display": display, "score": round(float(score), 1), "status": status, "note": note}


def layer_score(metrics: list[dict], weight: float) -> dict:
    total = sum(m["weight"] for m in metrics)
    score = sum(m["weight"] * m["score"] for m in metrics) / total if total else NEUTRAL
    real = sum(m["weight"] for m in metrics if m["status"] == "ok")
    return {"score": round(score, 1), "weight": weight, "coverage_pct": round(real / total * 100, 1) if total else 0.0,
            "metrics": metrics}


# ── helpers on statements ────────────────────────────────────────────────────

def span_cagr(points: list[tuple[int, float]]) -> tuple[float | None, int, bool]:
    """CAGR from the oldest to the newest point. Returns (cagr, years, base_non_positive)."""
    if len(points) < 2:
        return None, 0, False
    (y0, v0), (y1, v1) = points[0], points[-1]
    years = y1 - y0
    if v0 <= 0:
        return None, years, True
    return val.cagr(v0, v1, years), years, False


def debt_to_equity(annual: list[dict], info: dict) -> float | None:
    de = _num(info.get("debtToEquity"))
    if de is not None and de >= 0:
        return de / 100.0
    for a in reversed(annual):
        debt, eq = _num(a.get("total_debt")), _num(a.get("equity"))
        if eq is not None and eq > 0 and debt is not None:
            return debt / eq
    return None


def roce_for(a: dict) -> tuple[float | None, bool]:
    """(ROCE, used_proxy). CE = total assets − current liabilities, else equity + debt."""
    ebit = _num(a.get("ebit"))
    if ebit is None:
        return None, False
    ta, cl = _num(a.get("total_assets")), _num(a.get("current_liabilities"))
    if ta is not None and cl is not None and ta - cl > 0:
        return ebit / (ta - cl), False
    eq, debt = _num(a.get("equity")), _num(a.get("total_debt")) or 0.0
    if eq is not None and eq + debt > 0:
        return ebit / (eq + debt), True
    return None, False


def quarter_yoy(quarterly: list[dict], key: str, back: int = 0) -> float | None:
    """YoY growth of the (back)-th most recent quarter vs the same quarter a year earlier.
    Quarters are matched by date (350–380 days apart), so a missing quarter can't shift the
    comparison. None when there is no matching quarter or the base is ≤ 0."""
    pts = []
    for q in quarterly:
        v = _num(q.get(key))
        if v is None or not q.get("period"):
            continue
        try:
            pts.append((pd.Timestamp(q["period"]), v))
        except (TypeError, ValueError):
            continue
    pts.sort()
    if len(pts) <= back:
        return None
    when, cur = pts[-1 - back]
    for other, base in pts:
        if 350 <= (when - other).days <= 380:
            return None if base <= 0 else cur / base - 1
    return None


def market_cap_cr(info: dict, price: float | None, shares: float | None) -> float | None:
    mc = _num(info.get("marketCap"))
    if mc and mc > 0:
        return mc / 1e7
    if price and shares:
        return price * shares / 1e7
    return None


def share_series(annual: list[dict]) -> list[tuple[int, float]]:
    s = [p for p in _series(annual, "shares_outstanding") if p[1] > 0]
    if len(s) >= 2:
        return s
    return [p for p in _series(annual, "shares_diluted") if p[1] > 0]


# ── kill-switches ────────────────────────────────────────────────────────────

def kill_switches(annual: list[dict], info: dict, mcap_cr: float | None, financial: bool) -> tuple[str | None, list[tuple[str, str]], list[str], list[str]]:
    """Returns (insufficient_reason, failures[(code, detail)], gaps, near_misses)."""
    gaps: list[str] = []
    near: list[str] = []
    revs, nis = _series(annual, "revenue"), _series(annual, "net_income")
    insufficient = None
    if len(revs) < 3 or len(nis) < 3:
        insufficient = f"Only {min(len(revs), len(nis))} years of revenue/profit (need 3)"
    fails: list[tuple[str, str]] = []

    if mcap_cr is None:
        fails.append(("size_unknown", "Market cap not available"))
    elif not MCAP_MIN_CR <= mcap_cr <= MCAP_MAX_CR:
        fails.append(("size_band", f"Market cap ₹{mcap_cr:,.0f} Cr is outside ₹{MCAP_MIN_CR:,.0f}–{MCAP_MAX_CR:,.0f} Cr"))
    elif mcap_cr > MCAP_LATE_CR:
        near.append(f"Market cap ₹{mcap_cr:,.0f} Cr — late, already a mid-cap")

    if financial:
        fails.append(("financial", "Bank / NBFC / insurer — not covered by this framework (v1)"))

    ttm = _num(info.get("netIncomeToCommon"))
    latest_ni = nis[-1][1] if nis else None
    if (ttm is not None and ttm <= 0) or (latest_ni is not None and latest_ni <= 0):
        fails.append(("loss_making", "Loss-making (TTM or latest FY net profit ≤ 0)"))

    de = debt_to_equity(annual, info)
    if de is None:
        gaps.append("debt_unknown")
    elif de > MAX_DE:
        fails.append(("high_debt", f"Debt/equity {de:.2f} > {MAX_DE:.1f}"))
    elif de >= 0.7:
        near.append(f"Debt/equity {de:.2f} — close to the 1.0 limit")

    cfo = [v for _, v in _series(annual, "cfo")][-3:]
    if len(cfo) < 2:
        gaps.append("cfo_unknown")
    else:
        negatives = sum(1 for v in cfo if v < 0)
        if negatives >= 2 or sum(cfo) <= 0:
            fails.append(("negative_cfo", f"Operating cash flow negative in {negatives} of the last {len(cfo)} years"))
        elif negatives == 1:
            near.append("Operating cash flow was negative in 1 of the last 3 years")

    shares = share_series(annual)
    if len(shares) < 2:
        gaps.append("dilution_unknown")
    else:
        growth = shares[-1][1] / shares[0][1] - 1
        if growth > MAX_DILUTION:
            fails.append(("dilution", f"Share count up {growth * 100:.0f}% since FY{shares[0][0]}"))
        elif growth >= 0.15:
            near.append(f"Share count up {growth * 100:.0f}% since FY{shares[0][0]} — dilution creeping up")
    return insufficient, fails, gaps, near


# ── layer metrics ────────────────────────────────────────────────────────────

def growth_metrics(annual: list[dict], quarterly: list[dict], info: dict) -> tuple[list[dict], list[str], dict]:
    gaps: list[str] = []
    revs, nis = _series(annual, "revenue"), _series(annual, "net_income")
    rev_c, rev_years, _ = span_cagr(revs)
    pat_c, pat_years, pat_base_neg = span_cagr(nis)
    if 0 < rev_years < 3:
        gaps.append("short_history")
    if pat_base_neg:
        gaps.append("turnaround_base")
    rev_pct = None if rev_c is None else rev_c * 100
    pat_pct = None if pat_c is None else pat_c * 100

    m = [
        metric("rev_cagr", f"Revenue CAGR ({rev_years or 3}y)", 25, rev_pct,
               None if rev_pct is None else lin(rev_pct, 5, 25), _fmt_pct(rev_pct)),
        metric("pat_cagr", f"Profit CAGR ({pat_years or 3}y)", 25, pat_pct,
               None if pat_pct is None else lin(pat_pct, 5, 30), _fmt_pct(pat_pct),
               note="turnaround — base-year profit ≤ 0" if pat_base_neg else ""),
    ]
    q_yoy = quarter_yoy(quarterly, "revenue")
    source = "latest quarter YoY"
    if q_yoy is None:
        q_yoy = _num(info.get("revenueGrowth"))
        source = "Yahoo revenue growth (TTM)"
    accel = None if (q_yoy is None or rev_pct is None) else q_yoy * 100 - rev_pct
    if q_yoy is None:
        gaps.append("quarterly_unknown")
    m.append(metric("accel", "Growth acceleration", 20, accel, None if accel is None else lin(accel, -10, 10),
                    _fmt_pp(accel), note=f"{source} {_fmt_pct(None if q_yoy is None else q_yoy * 100, signed=True)} vs CAGR" if q_yoy is not None else ""))

    margins = []
    for a in annual:
        ebit, rev = _num(a.get("ebit")), _num(a.get("revenue"))
        if ebit is not None and rev:
            margins.append((a["year"], ebit / rev * 100))
    exp = margins[-1][1] - margins[0][1] if len(margins) >= 2 else None
    if not margins:
        gaps.append("margin_unknown")
    m.append(metric("margin_exp", "EBIT margin change", 15, exp, None if exp is None else lin(exp, -3, 5), _fmt_pp(exp),
                    note=f"{margins[0][1]:.1f}% → {margins[-1][1]:.1f}%" if len(margins) >= 2 else ""))
    lev = pat_pct - rev_pct if (pat_pct is not None and rev_pct is not None) else None
    m.append(metric("op_leverage", "Operating leverage (profit − revenue CAGR)", 15, lev,
                    None if lev is None else lin(lev, -5, 10), _fmt_pp(lev)))
    return m, gaps, {"rev_cagr_pct": rev_pct, "pat_cagr_pct": pat_pct}


def quality_metrics(annual: list[dict], info: dict) -> tuple[list[dict], list[str], dict]:
    gaps: list[str] = []
    rows = [a for a in annual if _num(a.get("ebit")) is not None]
    roce_latest = roce_old = None
    proxy = False
    if rows:
        roce_latest, proxy = roce_for(rows[-1])
        if len(rows) >= 3:
            roce_old, p2 = roce_for(rows[-3])
            proxy = proxy or p2
    if proxy:
        gaps.append("ce_proxy")
    rl = None if roce_latest is None else roce_latest * 100
    ro = None if roce_old is None else roce_old * 100
    trend = rl - ro if (rl is not None and ro is not None) else None
    m = [
        metric("roce", "ROCE (latest FY)", 30, rl, None if rl is None else lin(rl, 10, 25), _fmt_pct(rl)),
        metric("roce_trend", "ROCE change vs 2 years ago", 15, trend, None if trend is None else lin(trend, -5, 5), _fmt_pp(trend)),
    ]
    pairs = [(_num(a.get("cfo")), _num(a.get("net_income"))) for a in annual]
    pairs = [(c, n) for c, n in pairs if c is not None and n is not None][-3:]
    ratio = None
    if pairs:
        s_ni = sum(n for _, n in pairs)
        ratio = sum(c for c, _ in pairs) / s_ni if s_ni > 0 else None
    if not pairs and "cfo_unknown" not in gaps:
        gaps.append("cfo_unknown")
    m.append(metric("cfo_pat", "Cash conversion (CFO ÷ profit, 3y)", 25, ratio,
                    None if ratio is None else lin(ratio, 0.5, 1.0), "—" if ratio is None else f"{ratio:.2f}×", missing="penalised"))
    de = debt_to_equity(annual, info)
    if de is None:
        gaps.append("debt_unknown")
    m.append(metric("de", "Debt / equity", 15, de, None if de is None else lin(de, 1.0, 0.0),
                    "—" if de is None else f"{de:.2f}", missing="penalised"))

    latest = annual[-1] if annual else {}
    ebit, interest = _num(latest.get("ebit")), _num(latest.get("interest_expense"))
    debt = _num(latest.get("total_debt"))
    cover_score = cover = None
    cover_display = "—"
    if (debt is not None and debt < 1e7) or (interest in (None, 0) and de is not None and de < 0.1):
        cover_score, cover_display = 100.0, "debt-free"
    elif interest and ebit is not None:
        cover = ebit / abs(interest)
        cover_score, cover_display = lin(cover, 2, 10), f"{cover:.1f}×"
    else:
        gaps.append("interest_unknown")
    m.append(metric("int_cover", "Interest cover (EBIT ÷ interest)", 10, cover, cover_score, cover_display, missing="penalised"))

    turns = [(a["year"], _num(a.get("revenue")) / _num(a.get("total_assets"))) for a in annual
             if _num(a.get("revenue")) and _num(a.get("total_assets"))]
    tt = turns[-1][1] - turns[-3][1] if len(turns) >= 3 else None
    m.append(metric("asset_turn_trend", "Asset turnover change vs 2 years ago", 5, tt,
                    None if tt is None else lin(tt, -0.2, 0.2), "—" if tt is None else f"{tt:+.2f}"))
    return m, gaps, {"roce_pct": rl, "roce_old_pct": ro, "de": de}


def reverse_dcf(valuation_data: dict | None, price: float | None) -> dict:
    """Implied vs delivered growth using the valuation page's own model."""
    out = {"implied_growth_pct": None, "delivered_growth_pct": None, "status": "no_data"}
    if not valuation_data or not price:
        return out
    try:
        a = val.default_assumptions(valuation_data)
    except Exception:  # noqa: BLE001
        return out
    out["delivered_growth_pct"] = a.get("historical_growth_pct")
    shares = valuation_data.get("shares")
    base = a.get("base_cash_flow") or 0
    if not shares or base <= 0:
        out["status"] = "no_shares" if not shares else "no_base"
        return out
    g = val.implied_growth(price, base, TERMINAL_GROWTH, a["discount_rate_pct"] / 100,
                           valuation_data.get("cash") or 0.0, valuation_data.get("debt") or 0.0, shares)
    if g is None:
        out["status"] = "unexplainable"
        return out
    out["implied_growth_pct"] = round(g * 100, 1)
    out["status"] = "ok"
    return out


def valuation_metrics(valuation_data: dict | None, price: float | None, info: dict, pat_cagr_pct: float | None,
                      mcap_cr: float | None) -> tuple[list[dict], list[str], dict]:
    gaps: list[str] = []
    r = reverse_dcf(valuation_data, price)
    implied, delivered = r["implied_growth_pct"], r["delivered_growth_pct"]
    gap_pp = None
    if r["status"] == "unexplainable":
        score, display = 0.0, "price needs > 150%/yr growth"
    elif implied is not None and delivered is not None:
        gap_pp = delivered - implied
        score, display = lin(gap_pp, -10, 10), f"prices {implied:.0f}% growth vs {delivered:.0f}% delivered"
    else:
        score, display = None, "—"
        if r["status"] == "no_shares":
            gaps.append("no_shares")
    m = [metric("rdcf_gap", "Delivered − implied growth (reverse DCF)", 50, gap_pp, score, display)]

    pe = _num(info.get("trailingPE"))
    ttm = _num(info.get("netIncomeToCommon"))
    if (pe is None or pe <= 0) and mcap_cr and ttm and ttm > 0:
        pe = mcap_cr * 1e7 / ttm
    if pe is not None and pe <= 0:
        pe = None
    if pe is None:
        gaps.append("no_pe")
        m.append(metric("peg", "PEG (P/E ÷ profit CAGR)", 30, None, None, "—"))
        m.append(metric("ey_spread", "Earnings yield − G-sec", 20, None, None, "—"))
    else:
        if pat_cagr_pct is None:
            m.append(metric("peg", "PEG (P/E ÷ profit CAGR)", 30, None, None, f"P/E {pe:.1f}, no growth figure"))
        elif pat_cagr_pct <= 0:
            m.append(metric("peg", "PEG (P/E ÷ profit CAGR)", 30, None, 0.0, f"P/E {pe:.1f}, profit not growing"))
        else:
            peg = pe / pat_cagr_pct
            m.append(metric("peg", "PEG (P/E ÷ profit CAGR)", 30, peg, lin(peg, 2.5, 0.8), f"{peg:.2f} (P/E {pe:.1f})"))
        spread = 100.0 / pe - val.RISK_FREE
        m.append(metric("ey_spread", "Earnings yield − G-sec", 20, spread, lin(spread, -5, 2), _fmt_pp(spread)))
    return m, gaps, {"reverse_dcf": {"implied_growth_pct": implied, "delivered_growth_pct": delivered, "status": r["status"]}, "pe": pe}


def tailwind_metrics(sector_rank: tuple[int, int] | None, rs_pctl: float | None, peer_pctl: float | None,
                     no_reference: bool = False) -> tuple[list[dict], list[str]]:
    gaps: list[str] = []
    if no_reference:
        gaps.append("no_reference_run")
    if sector_rank is None and not no_reference:
        gaps.append("unknown_sector")
    s_score = None
    s_disp = "—"
    if sector_rank:
        rank, count = sector_rank
        s_score = 100.0 if count <= 1 else 100.0 * (1 - (rank - 1) / (count - 1))
        s_disp = f"#{rank} of {count}"
    m = [
        metric("sector_rank", "Sector strength rank", 40, None if not sector_rank else sector_rank[0], s_score, s_disp),
        metric("rs_pctl", "Relative strength percentile", 40, rs_pctl,
               None if rs_pctl is None else rs_pctl * 100, "—" if rs_pctl is None else f"{rs_pctl * 100:.0f}th"),
        metric("peer_rank", "Rank within sector", 20, peer_pctl,
               None if peer_pctl is None else peer_pctl * 100, "—" if peer_pctl is None else f"{peer_pctl * 100:.0f}th pctl"),
    ]
    return m, gaps


def price_metrics(frame: pd.DataFrame, bench: pd.Series | None) -> tuple[list[dict], dict]:
    tt = trend_template(frame)
    checks = tt["checks"]
    passed = sum(1 for v in checks.values() if v)
    high52 = float(frame["High"].tail(252).max())
    close = float(frame["Close"].iloc[-1])
    dist = max(0.0, (1 - close / high52) * 100) if high52 > 0 else None
    rs = build_rs_line(frame, bench)
    scan = scan_stock(frame) if len(frame.dropna(subset=["Close"])) >= 260 else None
    vcp_valid = bool(scan and scan.get("vcp") and scan["vcp"].get("valid"))
    if vcp_valid:
        vcp_score, vcp_disp = 100.0, f"valid VCP — {scan['status']}"
    elif tt["pass"]:
        vcp_score, vcp_disp = 40.0, "trend OK, no valid VCP"
    else:
        vcp_score, vcp_disp = 0.0, "fails trend template"
    if rs is None:
        rs_score, rs_disp = None, "—"
    elif rs.rs_line_new_high:
        rs_score, rs_disp = 100.0, "RS line at a new high"
    else:
        rs_score, rs_disp = lin(rs.distance_from_rs_high_pct, 20, 0), f"{rs.distance_from_rs_high_pct:.1f}% below RS high"
    m = [
        metric("stage2", "Stage-2 trend checks", 35, passed, passed / 6 * 100, f"{passed}/6"),
        metric("near_high", "Below 52-week high", 25, dist, None if dist is None else lin(dist, 40, 10), _fmt_pct(dist)),
        metric("rs_line", "RS line vs Nifty 500", 20, None if rs is None else rs.distance_from_rs_high_pct, rs_score, rs_disp),
        metric("vcp", "Volatility contraction (VCP)", 20, None, vcp_score, vcp_disp),
    ]
    return m, {"trend": tt, "scan": scan, "dist_52w_high_pct": dist, "rs": rs}


# ── tier, timing, thesis ─────────────────────────────────────────────────────

def tier_for(score: float, layers: dict[str, dict]) -> dict:
    tier, label = next((t, l) for cut, t, l in TIERS if score >= cut)
    weak = min(layers.items(), key=lambda kv: kv[1]["score"])
    capped = False
    if weak[1]["score"] < WEAK_LAYER and tier in ("A", "B"):
        tier, label = "C", "Watchlist"
        capped = True
    return {"tier": tier, "tier_label": label, "capped_by_weak_layer": capped,
            "weak_layer": weak[0] if weak[1]["score"] < WEAK_LAYER else None}


def system_score(layers: dict[str, dict]) -> float:
    return round(sum(WEIGHTS[k] * layers[k]["score"] for k in WEIGHTS) / 100.0, 1)


TIMING_LABELS = {"downtrend": "Downtrend — wait", "extended": "Extended — wait for a base",
                 "entry_zone": "Entry zone", "watch": "Watch"}


def timing_flag(frame: pd.DataFrame, scan: dict | None) -> tuple[str, str]:
    c = frame["Close"]
    close = float(c.iloc[-1])
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    s200, s200_prev = sma200.iloc[-1], sma200.iloc[-22] if len(sma200) > 22 else np.nan
    s50 = float(sma50.iloc[-1]) if pd.notna(sma50.iloc[-1]) else None
    status = (scan or {}).get("status")
    if pd.isna(s200) or close < s200 or (pd.notna(s200_prev) and s200 <= s200_prev):
        return "downtrend", "Price below a flat or falling 200-day average"
    if (s50 and close > 1.25 * s50) or status == "extended":
        return "extended", "More than 25% above the 50-day average, or extended past its VCP pivot"
    tt_pass = bool(scan and scan["trend_template"]["pass"]) if scan else trend_template(frame)["pass"]
    high52 = float(frame["High"].tail(252).max())
    dist = (1 - close / high52) * 100
    if tt_pass and (status in ("near pivot", "breakout", "breakout today") or (s50 and close <= 1.10 * s50 and dist <= 15)):
        reason = f"VCP {status}" if status in ("near pivot", "breakout", "breakout today") else "Stage-2 trend, close to the 50-day average and within 15% of the high"
        return "entry_zone", reason
    return "watch", "Trend not confirmed or no low-risk entry yet"


def thesis_check(annual: list[dict], quarterly: list[dict], frame: pd.DataFrame, bench: pd.Series | None,
                 de: float | None, rdcf: dict) -> list[dict]:
    out = []

    def add(code, status, text):
        out.append({"code": code, "status": status, "text": text})

    y1, y2 = quarter_yoy(quarterly, "revenue", 0), quarter_yoy(quarterly, "revenue", 1)
    if y1 is None or y2 is None:
        add("TB1", "na", "Revenue falling 2 quarters in a row — needs 6 quarters of results")
    elif y1 < 0 and y2 < 0:
        add("TB1", "break", f"Revenue down YoY two quarters running ({y2 * 100:+.0f}%, {y1 * 100:+.0f}%)")
    else:
        add("TB1", "ok", "Quarterly revenue not shrinking two quarters in a row")

    p1, p2 = quarter_yoy(quarterly, "net_income", 0), quarter_yoy(quarterly, "net_income", 1)
    if p1 is None:
        add("TB2", "na", "Quarterly profit collapse check — needs 5 quarters with a positive base")
    elif p1 < -0.20:
        add("TB2", "break" if (p2 is not None and p2 < -0.20) else "warning",
            f"Latest quarter profit {p1 * 100:+.0f}% YoY" + (" — second quarter in a row" if (p2 is not None and p2 < -0.20) else ""))
    else:
        add("TB2", "ok", f"Latest quarter profit {p1 * 100:+.0f}% YoY")

    rows = [a for a in annual if _num(a.get("ebit")) is not None]
    if len(rows) >= 3:
        r_now, _ = roce_for(rows[-1])
        r_old, _ = roce_for(rows[-3])
        if r_now is None or r_old is None:
            add("TB3", "na", "ROCE collapse check — capital employed missing")
        elif r_now < 0.12 and r_old >= 0.18:
            add("TB3", "break", f"ROCE fell from {r_old * 100:.0f}% to {r_now * 100:.0f}%")
        else:
            add("TB3", "ok", f"ROCE {r_old * 100:.0f}% → {r_now * 100:.0f}%")
    else:
        add("TB3", "na", "ROCE collapse check — needs 3 years of EBIT")

    cfo = _series(annual, "cfo")
    if not cfo:
        add("TB4", "na", "Operating cash flow not reported")
    elif cfo[-1][1] < 0:
        add("TB4", "warning", f"Operating cash flow negative in FY{cfo[-1][0]}")
    else:
        add("TB4", "ok", "Latest operating cash flow positive")

    if de is None:
        add("TB5", "na", "Debt/equity not reported")
    elif de > MAX_DE:
        add("TB5", "break", f"Debt/equity {de:.2f} > {MAX_DE:.1f}")
    else:
        add("TB5", "ok", f"Debt/equity {de:.2f}")

    c = frame["Close"]
    sma200 = c.rolling(200).mean()
    weekly = pd.DataFrame({"c": c, "s": sma200}).resample("W-FRI").last().dropna()
    if len(weekly) < 4 or bench is None:
        add("TB6", "na", "Trend-break check — not enough history or no benchmark")
    else:
        below = bool((weekly["c"].tail(4) < weekly["s"].tail(4)).all())
        rs = (c / bench.reindex(c.index).ffill()).dropna().tail(252)
        at_low = len(rs) > 20 and float(rs.iloc[-1]) <= float(rs.min()) * 1.001
        if below and at_low:
            add("TB6", "warning", "4 weekly closes below the 200-day average and RS line at a 52-week low — review; framework cuts to starter size")
        else:
            add("TB6", "ok", "Long-term trend intact")

    imp, dlv = rdcf.get("implied_growth_pct"), rdcf.get("delivered_growth_pct")
    if rdcf.get("status") == "unexplainable":
        add("TB7", "warning", "Price implies > 150%/yr growth — priced for perfection; framework trims to half")
    elif imp is None or dlv is None:
        add("TB7", "na", "Valuation stretch check — reverse DCF unavailable")
    elif imp - dlv > 15:
        add("TB7", "warning", f"Market prices {imp:.0f}% growth vs {dlv:.0f}% delivered — priced for perfection; framework trims to half")
    else:
        add("TB7", "ok", f"Market prices {imp:.0f}% growth vs {dlv:.0f}% delivered")
    return out


QUALIFY_TEXT = {
    "rev_cagr": "Revenue compounding {display} a year",
    "pat_cagr": "Profit compounding {display} a year",
    "accel": "Growth accelerating ({display} vs its CAGR)",
    "margin_exp": "Margins expanding ({display})",
    "op_leverage": "Operating leverage — profit outgrowing sales by {display}",
    "roce": "High return on capital: ROCE {display}",
    "roce_trend": "ROCE improving ({display})",
    "cfo_pat": "Profits backed by cash ({display} CFO/profit)",
    "de": "Low debt (D/E {display})",
    "int_cover": "Comfortable interest cover ({display})",
    "asset_turn_trend": "Sweating assets harder ({display})",
    "rdcf_gap": "Market {display}",
    "peg": "Reasonable PEG {display}",
    "ey_spread": "Earnings yield above G-sec ({display})",
    "sector_rank": "Sector in the lead ({display})",
    "rs_pctl": "Strong relative strength ({display} percentile)",
    "peer_rank": "Leader within its sector ({display})",
    "stage2": "Stage-2 uptrend ({display} checks)",
    "near_high": "Close to its 52-week high ({display} below)",
    "rs_line": "{display}",
    "vcp": "{display}",
}


def explain(layers: dict[str, dict], near_misses: list[str], rdcf: dict, gaps: list[str]) -> dict:
    qualifies, flags = [], []
    for name in WEIGHTS:
        for m in layers[name]["metrics"]:
            if m["status"] != "ok":
                continue
            if m["score"] >= 75 and len(qualifies) < 6:
                qualifies.append(QUALIFY_TEXT.get(m["key"], "{label}: {display}").format(**m))
            elif m["score"] <= 25 and len(flags) < 6:
                flags.append(f"Expensive: market {m['display']}" if m["key"] == "rdcf_gap"
                             else f"Weak: {m['label']} {m['display']}")
    if rdcf.get("status") == "unexplainable":
        flags.insert(0, "Price can't be explained even at 150%/yr growth (reverse DCF)")
    flags = (near_misses + flags)[:6]
    seen, gap_text = set(), []
    for g in gaps:
        if g not in seen:
            seen.add(g)
            gap_text.append(GAP_TEXT.get(g, g))
    return {"qualifies": qualifies[:6], "red_flags": flags, "data_gaps": gap_text[:6], "gap_codes": list(seen)}


# ── one stock, end to end ────────────────────────────────────────────────────

def evaluate_stock(*, symbol: str, company_name: str, listing_sector: str, frame: pd.DataFrame,
                   bench: pd.Series | None, deep: dict, sector_rank: tuple[int, int] | None,
                   rs_pctl: float | None, peer_pctl: float | None, no_reference: bool = False) -> dict:
    """Returns {"status": "scored"|"excluded"|"insufficient", ...}."""
    annual, quarterly = deep.get("annual") or [], deep.get("quarterly") or []
    info = deep.get("info") or {}
    vdata = deep.get("valuation_data")
    price = float(frame["Close"].iloc[-1])
    shares = (vdata or {}).get("shares")
    mcap = market_cap_cr(info, price, shares)
    vsector = {"sector": info.get("sector") or (vdata or {}).get("sector"), "industry": info.get("industry") or (vdata or {}).get("industry")}
    financial = val.is_financial(vsector) or listing_sector in ("Financial Services", "Financials")
    base = {"symbol": symbol, "company_name": company_name, "sector": listing_sector,
            "industry": vsector["industry"], "price": round(price, 2),
            "market_cap_cr": None if mcap is None else round(mcap, 0)}

    if deep.get("error"):
        return {**base, "status": "insufficient", "stage": "fetch", "reason": f"Fetch failed: {deep['error']}", "data_gaps": []}
    insufficient, fails, k_gaps, near = kill_switches(annual, info, mcap, financial)
    if insufficient:
        return {**base, "status": "insufficient", "stage": "k0", "reason": insufficient, "data_gaps": []}
    if fails:
        return {**base, "status": "excluded", "code": fails[0][0], "detail": "; ".join(d for _, d in fails),
                "codes": [c for c, _ in fails]}

    g_m, g_gaps, g_x = growth_metrics(annual, quarterly, info)
    q_m, q_gaps, q_x = quality_metrics(annual, info)
    v_m, v_gaps, v_x = valuation_metrics(vdata, price, info, g_x["pat_cagr_pct"], mcap)
    t_m, t_gaps = tailwind_metrics(sector_rank, rs_pctl, peer_pctl, no_reference)
    p_m, p_x = price_metrics(frame, bench)
    layers = {
        "growth": layer_score(g_m, WEIGHTS["growth"]),
        "quality": layer_score(q_m, WEIGHTS["quality"]),
        "valuation": layer_score(v_m, WEIGHTS["valuation"]),
        "tailwind": layer_score(t_m, WEIGHTS["tailwind"]),
        "price": layer_score(p_m, WEIGHTS["price"]),
    }
    gaps = k_gaps + g_gaps + q_gaps + v_gaps + t_gaps
    ex = explain(layers, near, v_x["reverse_dcf"], gaps)
    if layers["growth"]["coverage_pct"] < MIN_COVERAGE or layers["quality"]["coverage_pct"] < MIN_COVERAGE:
        return {**base, "status": "insufficient", "stage": "coverage",
                "reason": f"Too little data to score (growth coverage {layers['growth']['coverage_pct']:.0f}%, quality {layers['quality']['coverage_pct']:.0f}%)",
                "data_gaps": ex["data_gaps"]}

    score = system_score(layers)
    tier = tier_for(score, layers)
    timing, timing_reason = timing_flag(frame, p_x["scan"])
    coverage = sum(lay["weight"] * lay["coverage_pct"] for lay in layers.values()) / 100.0
    scan = p_x["scan"] or {}
    return {
        **base, "status": "scored", "score": score, **tier,
        "timing": timing, "timing_label": TIMING_LABELS[timing], "timing_reason": timing_reason,
        "coverage_pct": round(coverage, 1), "layers": layers,
        "qualifies": ex["qualifies"], "red_flags": ex["red_flags"], "data_gaps": ex["data_gaps"],
        "vcp_status": scan.get("status"), "pivot": scan.get("pivot"),
        "distance_to_52w_high_pct": None if p_x["dist_52w_high_pct"] is None else round(p_x["dist_52w_high_pct"], 1),
        "reverse_dcf": {"implied_growth_pct": v_x["reverse_dcf"]["implied_growth_pct"],
                        "delivered_growth_pct": v_x["reverse_dcf"]["delivered_growth_pct"]},
        "key": {"rev_cagr_pct": g_x["rev_cagr_pct"], "pat_cagr_pct": g_x["pat_cagr_pct"],
                "roce_pct": q_x["roce_pct"], "de": q_x["de"]},
        "thesis_check": thesis_check(annual, quarterly, frame, bench, q_x["de"], v_x["reverse_dcf"]),
    }


def sort_key(row: dict) -> tuple:
    dist = row.get("distance_to_52w_high_pct")
    return (-row["score"], -row["layers"]["quality"]["score"], dist if dist is not None else 999.0, row["symbol"])


RULES_TEXT = [
    "Kill-switches: market cap ₹500–20,000 Cr · not a bank/NBFC/insurer · profitable (TTM and latest FY) · debt/equity ≤ 1.0 · operating cash flow not negative in 2 of 3 years · share count up ≤ 25%.",
    "Score = 30% Growth + 25% Quality + 15% Valuation + 15% Tailwind + 15% Price. Each metric is scored 0–100 on a straight line between two thresholds.",
    "Missing data: scored neutral (50) or penalised (25) — never a silent zero. Listed per stock under Data gaps.",
    "Tiers: A ≥ 75 · B 60–74.9 · C 45–59.9 · D < 45. Any layer below 30 caps the tier at C. Needs ≥ 60% data coverage in Growth and Quality to be tiered.",
    "Timing is separate from the score: Downtrend → Extended → Entry zone → Watch.",
    "Weights are fixed priors, not fitted to a backtest. Only the price half is backtested; the forward ledger is the real test.",
]
