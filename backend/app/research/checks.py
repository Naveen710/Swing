"""Accounting-quality and risk checks. Each check returns pass | warn | fail | unavailable |
not_applicable with a plain-English detail and the metric keys it relied on."""
from __future__ import annotations

from app.research.metrics import Metric

COMMODITY_SECTORS = ("metals", "mining", "oil", "gas", "steel", "cement", "construction materials", "sugar", "fertil", "chemicals", "paper")


def _check(key, label, status, detail, metrics=(), severity=None):
    return {"key": key, "label": label, "status": status, "detail": detail, "metrics": list(metrics),
            "severity": severity or {"fail": "high", "warn": "medium"}.get(status, "none")}


def _band(m: Metric, key: str, label: str, warn, fail, higher_is_bad: bool, fmt) -> dict:
    if m.status == "not_applicable":
        return _check(key, label, "not_applicable", m.reason or "not applicable", [m.key])
    if m.value is None:
        return _check(key, label, "unavailable", m.reason or "input missing", [m.key])
    v = m.value
    bad = (lambda x, t: x > t) if higher_is_bad else (lambda x, t: x < t)
    status = "fail" if bad(v, fail) else "warn" if bad(v, warn) else "pass"
    return _check(key, label, status, fmt(v), [m.key])


def run_checks(mt: dict[str, Metric], financial: bool, sector: str | None, restated_count: int,
               governance_events: list[dict], margins_history: list[float]) -> list[dict]:
    out = []
    out.append(_band(mt["cfo_pat"], "cash_conversion", "Profits backed by operating cash", 0.8, 0.5, False,
                     lambda v: f"Operating cash flow was {v:.2f}× reported profit over 3 years" + (" — profits not turning into cash" if v < 0.5 else "")))
    out.append(_band(mt["fcf_pat"], "free_cash", "Free cash flow after capex", 0.0, -0.5, False,
                     lambda v: f"Free cash flow was {v:.2f}× profit over 3 years" + (" — growth is consuming more cash than it earns" if v < 0 else "")))
    rg, sg = mt["receivables_growth"], mt["revenue_growth"]
    if rg.value is None or sg.value is None:
        out.append(_check("receivables_vs_sales", "Receivables growing in line with sales", "unavailable", rg.reason or sg.reason or "input missing", [rg.key, sg.key]))
    else:
        gap = rg.value - sg.value
        st = "fail" if gap > 30 else "warn" if gap > 15 else "pass"
        out.append(_check("receivables_vs_sales", "Receivables growing in line with sales", st,
                          f"Receivables grew {rg.value:.0f}% vs sales {sg.value:.0f}% ({gap:+.0f} pp)", [rg.key, sg.key]))
    out.append(_band(mt["receivable_days_change"], "receivable_days", "Collection period stable", 20, 45, True,
                     lambda v: f"Receivable days changed by {v:+.0f} over 2 years"))
    out.append(_band(mt["inventory_days_change"], "inventory_days", "Inventory build-up", 30, 60, True,
                     lambda v: f"Inventory days changed by {v:+.0f} over 2 years"))
    out.append(_band(mt["other_income_share"], "other_income", "Profit from operations, not other income", 30, 50, True,
                     lambda v: f"Other income is {v:.0f}% of profit before tax"))
    out.append(_band(mt["debt_equity"], "leverage", "Leverage", 1.0, 2.0, True, lambda v: f"Debt / equity {v:.2f}"))
    out.append(_band(mt["interest_cover"], "interest_cover", "Interest comfortably covered", 3.0, 1.5, False,
                     lambda v: "Operating profit (EBIT) is negative — interest is not covered by operations" if v < 0 else f"EBIT covers interest {v:.1f}×"))
    out.append(_band(mt["share_change"], "dilution", "Shareholders not diluted", 10, 25, True, lambda v: f"Share count changed {v:+.0f}% over 3 years"))
    ni = mt["net_margin"]
    if ni.value is not None:
        out.append(_check("profitable", "Profitable", "warn" if ni.value < 0 else "pass",
                          "Loss-making in the latest year" if ni.value < 0 else f"Net margin {ni.value:.1f}%", [ni.key]))
    out.append(_check("restatements", "No recent restatements", "warn" if restated_count else "pass",
                      f"{restated_count} reported figure(s) were later changed by the source — check which is right" if restated_count else "No restated figures recorded", []))
    if governance_events:
        out.append(_check("governance_events", "No governance red-flag filings", "fail",
                          "; ".join(f"{e['date']}: {e['category']}" for e in governance_events[:3]), [], severity="high"))
    else:
        out.append(_check("governance_events", "No governance red-flag filings", "pass", "No auditor-resignation, SEBI, default or pledge filings found in the announcements retrieved", []))
    commodity = any(w in (sector or "").lower() for w in COMMODITY_SECTORS)
    if commodity and len(margins_history) >= 4:
        avg = sum(margins_history) / len(margins_history)
        peak = max(margins_history)
        latest = margins_history[-1]
        status = "warn" if peak > avg * 1.25 else "pass"
        out.append(_check("cycle", "Cyclical earnings", status,
                          f"Commodity business: EBIT margin {latest:.1f}% vs {len(margins_history)}-year average {avg:.1f}% (peak {peak:.1f}%). "
                          "P/E on peak earnings understates risk — valuation uses the average margin.", ["ebit_margin"]))
    if financial:
        out.append(_check("sector_metrics", "Sector-appropriate metrics", "warn",
                          "Bank/NBFC/insurer: ROCE, debt/equity, FCF and EV/EBITDA are not used; asset quality (NPA), capital adequacy and "
                          "net interest margin are not available from free sources — check filings.", [], severity="medium"))
    return out
