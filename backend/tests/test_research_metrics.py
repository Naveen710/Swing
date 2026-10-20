"""Research engine formulas: known answers and the edge cases from the spec (§4.5):
missing, zero, negative, inconsistent-unit and corrected/restated inputs."""
from datetime import date

import pytest

from app.research import metrics as mx
from app.research.checks import run_checks
from app.research.dimensions import decide_status, score_dimensions
from app.research.metrics import Fact, FactSet
from app.research.scenarios import build_scenarios

FY = [date(2022, 3, 31), date(2023, 3, 31), date(2024, 3, 31), date(2025, 3, 31), date(2026, 3, 31)]
_id = iter(range(1, 100000))


def fact(metric, pe, value, unit="INR", ptype="FY", scope="consolidated", provider="yahoo", version=1):
    return Fact(next(_id), metric, ptype, pe, value, unit, scope, provider, version)


def series(metric, values, unit="INR", **kw):
    return [fact(metric, d, v, unit, **kw) for d, v in zip(FY, values) if v is not None]


def test_unit_conversion():
    assert mx.to_inr(1, "INR_cr") == 1e7 and mx.to_inr(2, "INR_lakh") == 2e5 and mx.to_inr(3, "INR_mn") == 3e6
    assert mx.to_inr(5, "USD") is None and mx.to_inr(None, "INR") is None


def test_yoy_and_cagr_known_answers():
    fs = FactSet(series("revenue", [100, 110, 121, 133.1, 146.41]))
    g = mx.yoy(fs, "revenue", "g", "g")
    assert g.value == pytest.approx(10.0) and g.status == "ok" and len(g.inputs) == 2
    c = mx.cagr(fs, "revenue", "c", "c")
    assert c.value == pytest.approx(10.0, abs=1e-6) and "n = 3" in c.formula


def test_growth_missing_zero_negative_inputs():
    assert mx.yoy(FactSet(series("revenue", [100])), "revenue", "g", "g").status == "unavailable"
    zero = mx.yoy(FactSet(series("revenue", [100, 100, 100, 0, 50])), "revenue", "g", "g")
    assert zero.value is None and "zero" in zero.reason
    neg = mx.cagr(FactSet(series("net_income", [1, -10, 8, 12, 20])), "net_income", "c", "c")
    assert neg.value is None and "≤ 0" in neg.reason


def test_non_consecutive_years_rejected():
    fs = FactSet([fact("revenue", FY[0], 100), fact("revenue", FY[2], 120)])
    m = mx.yoy(fs, "revenue", "g", "g")
    assert m.value is None and "not consecutive" in m.reason


def test_mixed_units_are_normalised():
    fs = FactSet([fact("revenue", FY[3], 1000, "INR_cr"), fact("revenue", FY[4], 1.2e10, "INR")])
    assert mx.yoy(fs, "revenue", "g", "g").value == pytest.approx(20.0)


def test_unknown_unit_makes_metric_unavailable():
    fs = FactSet([fact("revenue", FY[3], 100, "USD"), fact("revenue", FY[4], 120, "USD")])
    m = mx.yoy(fs, "revenue", "g", "g")
    assert m.value is None and "USD" in m.reason


def test_restated_value_wins_only_when_current():
    # Only current versions reach the engine; the FactSet picks one value per period.
    old = fact("revenue", FY[4], 100, version=1)
    new = fact("revenue", FY[4], 97, version=2)
    fs = FactSet([fact("revenue", FY[3], 90), new])
    assert mx.yoy(fs, "revenue", "g", "g").value == pytest.approx((97 / 90 - 1) * 100)
    assert old.value == 100   # the superseded version still exists as history


def test_provider_priority_and_scope_consistency():
    fs = FactSet([fact("revenue", FY[4], 120, provider="yahoo"), fact("revenue", FY[4], 125, provider="manual"),
                  fact("revenue", FY[3], 100, provider="yahoo"), fact("revenue", FY[3], 80, scope="standalone")])
    assert mx.yoy(fs, "revenue", "g", "g").value == pytest.approx(25.0)      # manual beats yahoo; standalone ignored
    assert any("mixed scopes" in i for i in fs.issues)


def test_quarter_yoy_by_date():
    q = [fact("revenue", d, v, ptype="Q") for d, v in [(date(2025, 6, 30), 100), (date(2025, 12, 31), 130), (date(2026, 6, 30), 125)]]
    m = mx.quarter_yoy(FactSet(q), "revenue", "q", "q")
    assert m.value == pytest.approx(25.0)


def test_roe_roce_and_sector_rules():
    facts = (series("net_income", [None, None, None, 80, 100]) + series("equity", [None, None, None, 400, 600]) +
             series("ebit", [None, None, None, None, 150]) + series("total_assets", [None, None, None, None, 1000]) +
             series("current_liabilities", [None, None, None, None, 250]))
    fs = FactSet(facts)
    assert mx.roe(fs).value == pytest.approx(20.0)
    assert mx.roce(fs, financial=False).value == pytest.approx(20.0)
    assert mx.roce(fs, financial=True).status == "not_applicable"
    assert mx.debt_equity(fs, financial=True).status == "not_applicable"


def test_negative_equity():
    fs = FactSet(series("net_income", [None, None, None, None, 10]) + series("equity", [None, None, None, None, -50]))
    r = mx.roe(fs)
    assert r.value is None and "equity ≤ 0" in r.reason


def test_cash_conversion_and_losses():
    fs = FactSet(series("cfo", [None, None, 90, 100, 110]) + series("net_income", [None, None, 100, 100, 100]))
    assert mx.sum_ratio(fs, "cfo", "net_income", "x", "x").value == pytest.approx(1.0)
    loss = FactSet(series("cfo", [None, None, 10, 10, 10]) + series("net_income", [None, None, -50, -40, 30]))
    assert mx.sum_ratio(loss, "cfo", "net_income", "x", "x").value is None


def test_interest_cover_debt_free():
    fs = FactSet(series("ebit", [None, None, None, None, 100]) + series("total_debt", [None, None, None, None, 1e5]))
    assert mx.interest_cover(fs, financial=False).status == "not_applicable"


def test_valuation_ratios_need_price_and_shares():
    fs = FactSet(series("net_income", [None, None, None, None, 1e9]) + series("equity", [None, None, None, None, 5e9]) +
                 series("shares", [None, None, None, None, 1e8], unit="shares"))
    r = mx.valuation_ratios(fs, 200.0, False, None)
    assert r["pe"].value == pytest.approx(20.0) and r["pb"].value == pytest.approx(4.0)
    assert mx.valuation_ratios(fs, None, False, None)["pe"].reason == "no price"
    loss = FactSet(series("net_income", [None, None, None, None, -1e8]) + series("shares", [None, None, None, None, 1e8], unit="shares"))
    assert "loss-making" in mx.valuation_ratios(loss, 50.0, False, None)["pe"].reason


def _company_metrics(financial=False, **over):
    rev = over.get("rev", [1000, 1100, 1250, 1450, 1750])
    ni = over.get("ni", [80, 90, 105, 125, 160])
    facts = (series("revenue", [v * 1e7 for v in rev]) + series("net_income", [v * 1e7 for v in ni]) +
             series("ebit", [v * 1.5e7 for v in ni]) + series("equity", [v * 6e7 for v in ni]) + series("total_debt", [v * 1e7 for v in ni]) +
             series("cfo", [v * 0.9e7 for v in ni]) + series("fcf", [v * 0.5e7 for v in ni]) +
             series("total_assets", [v * 9e7 for v in ni]) + series("current_liabilities", [v * 2e7 for v in ni]) +
             series("shares", [1e8] * 5, unit="shares") + series("interest_expense", [v * 0.1e7 for v in ni]))
    fs = FactSet(facts)
    closes = [(str(i), 100 + i * 0.1) for i in range(300)]
    return fs, mx.compute_all(fs, financial, 150.0, None, closes, {"ret_1y": 10.0})


def test_scenarios_dcf_and_financial():
    fs, mt = _company_metrics()
    sc = build_scenarios(fs, mt, False, False, 150.0, 1e8)
    b, base, bull = (sc["scenarios"][k]["value_per_share"] for k in ("bear", "base", "bull"))
    assert b < base < bull and sc["reverse_dcf"]["implied_growth_pct"] is not None
    fs2, mt2 = _company_metrics(financial=True)
    fin = build_scenarios(fs2, mt2, True, False, 150.0, 1e8)
    assert "P/B" in fin["method"]
    loss_fs, loss_mt = _company_metrics(ni=[-10, -5, -8, -4, -2])
    assert build_scenarios(loss_fs, loss_mt, False, False, 150.0, 1e8)["unavailable_reason"]


def test_checks_status_and_no_combined_score():
    fs, mt = _company_metrics()
    checks = run_checks(mt, False, "Capital Goods", 0, [], [12, 12, 13, 13, 14])
    dims = score_dimensions(mt, checks, build_scenarios(fs, mt, False, False, 150.0, 1e8), False,
                            {"open_errors": 0, "open_warnings": 0, "fy_count": 5, "stale": [], "verified_facts": 0})
    assert len(dims) == 7 and "overall" not in dims
    status, flags = decide_status(mt, checks, dims, {"fy_count": 5, "stale": []}, False, "listed")
    assert status == "ELIGIBLE_FOR_RESEARCH"
    gov = run_checks(mt, False, "Capital Goods", 0, [{"date": "2026-10-01", "category": "Resignation of Statutory Auditors"}], [])
    status, flags = decide_status(mt, gov, dims, {"fy_count": 5, "stale": []}, False, "listed")
    assert status == "REVIEW_REQUIRED" and any("governance" in f["source"] for f in flags)
    status, _ = decide_status(mt, checks, dims, {"fy_count": 1, "stale": []}, False, "listed")
    assert status == "BLOCKED"
