"""Bear / base / bull valuation scenarios with every assumption visible. These are ESTIMATES.

Non-financial companies: 10-year DCF (valuation.run_dcf) on average free cash flow (or net profit
when FCF is negative/missing), growth anchored to what the company has delivered.
Commodity businesses: base cash flow uses the average EBIT margin, not the peak.
Banks/NBFCs/insurers: justified P/B = (ROE − g) / (r − g) × book value per share.
"""
from __future__ import annotations

from app.research.metrics import FactSet, Metric
from app.services.valuation import implied_growth, run_dcf

SCENARIOS = {
    "bear": {"growth_delta": -0.06, "discount_delta": 0.01, "terminal": 0.04},
    "base": {"growth_delta": 0.0, "discount_delta": 0.0, "terminal": 0.05},
    "bull": {"growth_delta": 0.05, "discount_delta": -0.005, "terminal": 0.055},
}
BASE_DISCOUNT = 0.13          # ~7% G-sec + ~6% equity risk premium for a mid/small cap; editable later
FIN_SCEN = {"bear": {"roe_delta": -0.03, "g": 0.08, "r": 0.135}, "base": {"roe_delta": 0.0, "g": 0.10, "r": 0.125}, "bull": {"roe_delta": 0.02, "g": 0.11, "r": 0.12}}


def _pct(x: float) -> float:
    return round(x * 100, 1)


def build_scenarios(fs: FactSet, mt: dict[str, Metric], financial: bool, cyclical: bool, price: float | None,
                    shares: float | None) -> dict:
    out = {"label": "Estimates — not forecasts. Change the assumptions on the Valuation page.", "scenarios": {}, "reverse_dcf": None,
           "method": None, "unavailable_reason": None, "inputs": []}
    if price is None or not shares:
        out["unavailable_reason"] = "needs a price and a share count"
        return out
    if financial:
        roe = mt["roe"]
        eq = fs.latest("equity")
        if roe.value is None or eq is None or eq.inr <= 0:
            out["unavailable_reason"] = "needs ROE and positive book value"
            return out
        bvps = eq.inr / shares
        out["method"] = "Justified P/B = (ROE − g) / (r − g) × book value per share"
        out["inputs"] = roe.inputs + [eq.id]
        for name, s in FIN_SCEN.items():
            r_o_e = roe.value / 100 + s["roe_delta"]
            g, r = s["g"], s["r"]
            if r <= g or r_o_e <= g:
                out["scenarios"][name] = {"value_per_share": None, "reason": "ROE at or below growth — model breaks down",
                                          "assumptions": {"roe_pct": _pct(r_o_e), "growth_pct": _pct(g), "cost_of_equity_pct": _pct(r)}}
                continue
            pb = (r_o_e - g) / (r - g)
            v = pb * bvps
            out["scenarios"][name] = {"value_per_share": round(v, 2), "upside_pct": round((v / price - 1) * 100, 1), "justified_pb": round(pb, 2),
                                      "assumptions": {"roe_pct": _pct(r_o_e), "growth_pct": _pct(g), "cost_of_equity_pct": _pct(r),
                                                      "book_value_per_share": round(bvps, 2)}}
        return out

    fcf = [f.inr for f in fs.series("fcf")[-3:]]
    ni = fs.latest("net_income")
    ids: list[int] = [f.id for f in fs.series("fcf")[-3:]]
    if fcf and sum(fcf) / len(fcf) > 0 and not cyclical:
        base_cf, basis = sum(fcf) / len(fcf), f"average free cash flow of the last {len(fcf)} years"
    elif cyclical:
        rev = fs.latest("revenue")
        margins = [f.inr / r.inr for f in fs.series("ebit") if (r := fs.at("revenue", f.period_end)) and r.inr > 0]
        if not rev or not margins:
            out["unavailable_reason"] = "cyclical business without revenue/EBIT history"
            return out
        base_cf, basis = rev.inr * (sum(margins) / len(margins)) * 0.75, "latest revenue × average EBIT margin × (1 − 25% tax) — mid-cycle"
        ids = [rev.id] + [f.id for f in fs.series("ebit")]
    elif ni and ni.inr > 0:
        base_cf, basis = ni.inr, "latest net profit (free cash flow negative or missing)"
        ids = [ni.id]
    else:
        out["unavailable_reason"] = "no positive cash flow or profit to value — DCF not meaningful for a loss-maker"
        return out
    growth_candidates = [m.value / 100 for m in (mt["revenue_cagr"], mt["profit_cagr"]) if m.value is not None]
    delivered = min(growth_candidates) if growth_candidates else 0.08
    base_g = max(0.0, min(delivered, 0.25))
    debt = fs.latest("total_debt")
    cash = fs.latest("cash")
    d, c = (debt.inr if debt else 0.0), (cash.inr if cash else 0.0)
    ids += [x.id for x in (debt, cash) if x]
    out["method"] = f"10-year DCF; base cash flow = {basis}"
    out["inputs"] = sorted(set(ids))
    for name, s in SCENARIOS.items():
        g = max(0.0, base_g + s["growth_delta"])
        r = BASE_DISCOUNT + s["discount_delta"]
        res = run_dcf(base_cf, g, s["terminal"], r, c, d, shares)
        v = res["per_share"]
        out["scenarios"][name] = {"value_per_share": round(v, 2), "upside_pct": round((v / price - 1) * 100, 1),
                                  "terminal_share_pct": round(res["terminal_share_pct"], 1) if res["terminal_share_pct"] else None,
                                  "assumptions": {"growth_years_1_5_pct": _pct(g), "discount_rate_pct": _pct(r), "terminal_growth_pct": _pct(s["terminal"]),
                                                  "base_cash_flow_cr": round(base_cf / 1e7, 1), "net_debt_cr": round((d - c) / 1e7, 1)}}
    ig = implied_growth(price, base_cf, 0.05, BASE_DISCOUNT, c, d, shares)
    out["reverse_dcf"] = {"implied_growth_pct": None if ig is None else _pct(ig), "delivered_growth_pct": _pct(delivered) if growth_candidates else None,
                          "note": "Growth (years 1–5) today's price assumes, at a 13% discount rate and 5% terminal growth" if ig is not None
                          else "Price cannot be explained even at 150% a year growth"}
    return out
