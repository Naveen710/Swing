"""Seven research dimensions, kept separate on purpose — there is no combined score, so a serious
risk can never be averaged away by strong growth.

Each dimension: score 0–100 (None if no inputs), coverage % of its inputs that had data, and
reasons. Scores are transparent straight-line rules (lin), not fitted weights; they rank
companies for research, they do not predict returns.

Status (spec §4.4):
  BLOCKED                clear disqualifier or essential data missing
  REVIEW_REQUIRED        concerns that need human judgement
  ELIGIBLE_FOR_RESEARCH  can be investigated further — NOT a buy signal
"""
from __future__ import annotations

from app.research.metrics import Metric
from app.services.multibagger_rules import lin

DIMENSIONS = [
    ("business_quality", "Business quality"),
    ("growth_inflection", "Growth / inflection"),
    ("financial_strength", "Financial strength & accounting"),
    ("valuation", "Valuation vs expectations"),
    ("governance_risk", "Governance & risk"),
    ("data_confidence", "Data confidence"),
    ("recognition_gap", "Market recognition gap"),
]


def _dim(key: str, label: str, parts: list[tuple[str, float, float | None, str]]) -> dict:
    """parts: (metric key, weight, score or None, reason)."""
    have = [(w, s) for _, w, s, _ in parts if s is not None]
    total_w = sum(w for _, w, _, _ in parts) or 1
    score = round(sum(w * s for w, s in have) / sum(w for w, _ in have), 1) if have else None
    return {"key": key, "label": label, "score": score, "coverage_pct": round(sum(w for w, _ in have) / total_w * 100),
            "parts": [{"metric": k, "weight": w, "score": None if s is None else round(s, 1), "reason": r} for k, w, s, r in parts]}


def _p(m: Metric, lo: float, hi: float, fmt: str) -> tuple[float | None, str]:
    if m.value is None:
        return None, m.reason or "no data"
    return lin(m.value, lo, hi), fmt.format(v=m.value)


def score_dimensions(mt: dict[str, Metric], checks: list[dict], scen: dict, financial: bool, data_state: dict) -> dict[str, dict]:
    d: dict[str, dict] = {}
    q = []
    if financial:
        q.append(("roe", 3, *_p(mt["roe"], 10, 20, "ROE {v:.1f}%")))
    else:
        q.append(("roce", 3, *_p(mt["roce"], 8, 25, "ROCE {v:.1f}%")))
        q.append(("ebit_margin", 2, *_p(mt["ebit_margin"], 5, 20, "EBIT margin {v:.1f}%")))
        q.append(("roce_change", 1, *_p(mt["roce_change"], -5, 5, "ROCE change {v:+.1f} pp")))
    q.append(("net_margin", 1, *_p(mt["net_margin"], 2, 15, "Net margin {v:.1f}%")))
    d["business_quality"] = _dim("business_quality", "Business quality", q)

    g = [("revenue_cagr", 2, *_p(mt["revenue_cagr"], 5, 25, "Revenue CAGR {v:.1f}%")),
         ("profit_cagr", 2, *_p(mt["profit_cagr"], 5, 30, "Profit CAGR {v:.1f}%")),
         ("revenue_acceleration", 2, *_p(mt["revenue_acceleration"], -5, 10, "Revenue growth changed {v:+.1f} pp vs prior year")),
         ("q_acceleration", 1, *_p(mt["q_acceleration"], -10, 10, "Latest quarter vs annual growth {v:+.1f} pp"))]
    if not financial:
        g.append(("ebit_margin_change", 1, *_p(mt["ebit_margin_change"], -3, 4, "EBIT margin {v:+.1f} pp over 2 years")))
    d["growth_inflection"] = _dim("growth_inflection", "Growth / inflection", g)

    fsd = []
    if not financial:
        fsd += [("debt_equity", 2, *_p(mt["debt_equity"], 1.5, 0.0, "Debt/equity {v:.2f}")),
                ("interest_cover", 1, *(_p(mt["interest_cover"], 2, 10, "Interest cover {v:.1f}×") if mt["interest_cover"].status != "not_applicable"
                                        else (100.0, "Effectively debt-free"))),
                ("cfo_pat", 2, *_p(mt["cfo_pat"], 0.5, 1.0, "CFO/PAT {v:.2f}")),
                ("fcf_pat", 1, *_p(mt["fcf_pat"], -0.2, 0.8, "FCF/PAT {v:.2f}"))]
    acct = {c["key"]: c for c in checks}
    for key, w in (("receivables_vs_sales", 1), ("other_income", 1), ("dilution", 1)):
        c = acct.get(key)
        s = None if c is None or c["status"] in ("unavailable", "not_applicable") else {"pass": 100.0, "warn": 40.0, "fail": 0.0}[c["status"]]
        fsd.append((key, w, s, c["detail"] if c else "not checked"))
    d["financial_strength"] = _dim("financial_strength", "Financial strength & accounting", fsd)

    v = []
    rd = scen.get("reverse_dcf") or {}
    if rd.get("implied_growth_pct") is not None and rd.get("delivered_growth_pct") is not None:
        gap = rd["delivered_growth_pct"] - rd["implied_growth_pct"]
        v.append(("reverse_dcf", 3, lin(gap, -10, 10), f"Price implies {rd['implied_growth_pct']:.0f}% growth vs {rd['delivered_growth_pct']:.0f}% delivered"))
    elif rd and rd.get("implied_growth_pct") is None and scen.get("method"):
        v.append(("reverse_dcf", 3, 0.0, "Price cannot be explained even at 150%/yr growth"))
    else:
        v.append(("reverse_dcf", 3, None, scen.get("unavailable_reason") or "reverse DCF not applicable"))
    base = (scen.get("scenarios") or {}).get("base") or {}
    v.append(("base_scenario", 2, None if base.get("upside_pct") is None else lin(base["upside_pct"], -50, 30),
              f"Base scenario {base['upside_pct']:+.0f}% vs price" if base.get("upside_pct") is not None else "no base scenario"))
    v.append(("earnings_yield", 1, *_p(mt["earnings_yield"], 1, 8, "Earnings yield {v:.1f}%")))
    if financial:
        v.append(("pb", 1, *_p(mt["pb"], 5, 1, "P/B {v:.1f}×")))
    d["valuation"] = _dim("valuation", "Valuation vs expectations", v)

    gr = []
    gov = acct.get("governance_events")
    gr.append(("governance_events", 3, 0.0 if gov and gov["status"] == "fail" else 100.0 if gov else None, gov["detail"] if gov else "no filings checked"))
    for key, w in (("dilution", 1), ("restatements", 1)):
        c = acct.get(key)
        s = None if c is None or c["status"] in ("unavailable", "not_applicable") else {"pass": 100.0, "warn": 40.0, "fail": 0.0}[c["status"]]
        gr.append((key, w, s, c["detail"] if c else "not checked"))
    gr.append(("volatility", 1, *_p(mt["volatility"], 60, 20, "Volatility {v:.0f}%/yr")))
    gr.append(("max_drawdown", 1, *_p(mt["max_drawdown"], -50, -10, "Max drawdown {v:.0f}% in a year")))
    gr.append(("pledge", 0, None, "Promoter pledge / holding not available from free sources — check the shareholding pattern"))
    d["governance_risk"] = _dim("governance_risk", "Governance & risk (higher = lower risk)", gr)

    required = ["revenue_cagr", "profit_cagr", "roe", "cfo_pat", "pe", "ret_1y"] + ([] if financial else ["roce", "debt_equity"])
    applicable = [k for k in required if mt[k].status != "not_applicable"]
    have = sum(1 for k in applicable if mt[k].value is not None)
    coverage = have / len(applicable) * 100 if applicable else 0
    dc = [("coverage", 3, coverage, f"{have} of {len(applicable)} core metrics available"),
          ("issues", 1, max(0.0, 100 - 25 * data_state.get("open_errors", 0) - 8 * data_state.get("open_warnings", 0)),
           f"{data_state.get('open_errors', 0)} open data errors, {data_state.get('open_warnings', 0)} warnings"),
          ("history", 1, lin(data_state.get("fy_count", 0), 1, 5), f"{data_state.get('fy_count', 0)} years of annual statements"),
          ("verification", 1, 100.0 if data_state.get("verified_facts") else 50.0,
           f"{data_state.get('verified_facts', 0)} facts verified against source documents")]
    d["data_confidence"] = _dim("data_confidence", "Data confidence", dc)

    growth = d["growth_inflection"]["score"]
    rel = mt["rel_1y"].value if mt["rel_1y"].value is not None else mt["ret_1y"].value
    rel_label = "vs Nifty 500" if mt["rel_1y"].value is not None else "absolute (index unavailable)"
    if growth is None or rel is None:
        d["recognition_gap"] = {"key": "recognition_gap", "label": "Market recognition gap (inference)", "score": None, "coverage_pct": 0,
                                "parts": [{"metric": "growth_vs_price", "weight": 1, "score": None, "reason": "needs growth and 1-year price data"}]}
    else:
        price_strength = lin(rel, -20, 40)
        val_ok = d["valuation"]["score"] if d["valuation"]["score"] is not None else 50
        gap = max(0.0, min(100.0, 50 + (growth - price_strength) / 2)) * (0.5 + val_ok / 200)
        d["recognition_gap"] = {"key": "recognition_gap", "label": "Market recognition gap (inference)", "score": round(gap, 1), "coverage_pct": 100,
                                "parts": [{"metric": "growth_vs_price", "weight": 1, "score": round(gap, 1),
                                           "reason": f"Growth score {growth:.0f} vs 1-year price strength {price_strength:.0f} ({rel:+.0f}% {rel_label}), "
                                                     f"scaled by valuation {val_ok:.0f}. High = fundamentals improving faster than the price has moved."}]}
    return d


def decide_status(mt: dict[str, Metric], checks: list[dict], dims: dict, data_state: dict, financial: bool, company_status: str) -> tuple[str, list[dict]]:
    flags: list[dict] = []

    def flag(level, text, source):
        flags.append({"level": level, "text": text, "source": source})

    if company_status in ("suspended", "delisted"):
        flag("blocker", f"Security is {company_status}", "master")
    if data_state.get("fy_count", 0) < 2:
        flag("blocker", f"Only {data_state.get('fy_count', 0)} year(s) of annual statements — too little to research", "data")
    eq = mt["roe"]
    if eq.reason and "equity ≤ 0" in eq.reason:
        flag("blocker", "Negative or zero equity", "metrics")
    dc = dims["data_confidence"]["score"]
    if dc is not None and dc < 25:
        flag("blocker", f"Data confidence {dc:.0f}/100 — essential data missing", "data")
    for c in checks:
        if c["status"] == "fail":
            flag("review", f"{c['label']}: {c['detail']}", f"check:{c['key']}")
        elif c["status"] == "warn" and c["severity"] in ("medium", "high"):
            flag("watch", f"{c['label']}: {c['detail']}", f"check:{c['key']}")
    if financial:
        flag("review", "Lender/insurer — generic metrics limited; needs asset-quality data from filings", "sector")
    if data_state.get("stale"):
        flag("review", "Stale data: " + "; ".join(data_state["stale"]), "data")
    if mt["net_margin"].value is not None and mt["net_margin"].value < 0:
        flag("review", "Loss-making in the latest year", "metrics")
    if any(f["level"] == "blocker" for f in flags):
        status = "BLOCKED"
    elif any(f["level"] == "review" for f in flags):
        status = "REVIEW_REQUIRED"
    else:
        status = "ELIGIBLE_FOR_RESEARCH"
    order = {"blocker": 0, "review": 1, "watch": 2}
    flags.sort(key=lambda f: order[f["level"]])
    return status, flags
