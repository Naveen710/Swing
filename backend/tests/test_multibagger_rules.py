"""Acceptance criteria 1–9 of docs/features/multibagger-framework/spec.md."""
import copy

import numpy as np
import pandas as pd
import pytest

from app.services import multibagger_data as mbd
from app.services import multibagger_rules as mb
from tests.conftest import make_bars


def annual_good(**over):
    rows = []
    for i, y in enumerate([2022, 2023, 2024, 2025]):
        rev = 1000e7 * 1.2 ** i
        row = {"year": y, "revenue": rev, "ebit": rev * (0.15 + 0.01 * i), "net_income": rev * 0.10 * 1.05 ** i,
               "interest_expense": 5e7, "total_assets": rev * 0.9, "current_liabilities": rev * 0.2,
               "total_debt": rev * 0.1, "equity": rev * 0.5, "cfo": rev * 0.11, "capex": -rev * 0.04, "fcf": rev * 0.07,
               "shares_outstanding": 1e8, "shares_diluted": 1e8}
        row.update({k: (v(i) if callable(v) else v) for k, v in over.items()})
        rows.append(row)
    return rows


INFO = {"marketCap": 5000e7, "netIncomeToCommon": 150e7, "trailingPE": 30.0, "debtToEquity": 20.0}


def uptrend():
    return make_bars(np.linspace(100, 300, 520)).set_axis(pd.bdate_range("2023-01-02", periods=520))


# AC1
@pytest.mark.parametrize("x,lo,hi,out", [(15, 5, 25, 50), (0, 5, 25, 0), (40, 5, 25, 100), (0.5, 1.0, 0.0, 50)])
def test_lin_known_answers(x, lo, hi, out):
    assert mb.lin(x, lo, hi) == pytest.approx(out)


# AC2 — each kill-switch passes and fails
def codes(annual=None, info=None, mcap=5000.0, financial=False):
    _, fails, _, _ = mb.kill_switches(annual or annual_good(), info if info is not None else INFO, mcap, financial)
    return [c for c, _ in fails]


def test_clean_company_passes_all_kill_switches():
    assert codes() == []


def test_k1_size_band():
    assert "size_band" in codes(mcap=400) and "size_band" in codes(mcap=25000)
    assert "size_unknown" in codes(mcap=None)


def test_k2_financial():
    assert codes(financial=True) == ["financial"]


def test_k3_loss_making():
    assert "loss_making" in codes(info={**INFO, "netIncomeToCommon": -1})
    assert "loss_making" in codes(annual=annual_good(net_income=lambda i: -1 if i == 3 else 1e8))


def test_k4_debt():
    assert "high_debt" in codes(info={**INFO, "debtToEquity": 140.0})
    insufficient, fails, gaps, _ = mb.kill_switches(annual_good(total_debt=None, equity=None), {**INFO, "debtToEquity": None}, 5000, False)
    assert not fails and "debt_unknown" in gaps


def test_k5_negative_cfo():
    assert "negative_cfo" in codes(annual=annual_good(cfo=lambda i: -1e7 if i >= 2 else 1e8))
    _, fails, gaps, near = mb.kill_switches(annual_good(cfo=lambda i: -1e7 if i == 3 else 5e8), INFO, 5000, False)
    assert not fails and any("1 of the last 3" in n for n in near)


def test_k6_dilution():
    assert "dilution" in codes(annual=annual_good(shares_outstanding=lambda i: 1e8 * (1 + 0.1 * i)))
    _, _, gaps, _ = mb.kill_switches(annual_good(shares_outstanding=None, shares_diluted=None), INFO, 5000, False)
    assert "dilution_unknown" in gaps


def test_k0_routes_to_insufficient_not_excluded():
    deep = {"annual": annual_good()[:2], "quarterly": [], "info": INFO, "valuation_data": None}
    r = mb.evaluate_stock(symbol="X.NS", company_name="X", listing_sector="Industrials", frame=uptrend(), bench=None,
                          deep=deep, sector_rank=(1, 5), rs_pctl=0.9, peer_pctl=0.9)
    assert r["status"] == "insufficient"


# AC3 — missing-data policy
def test_missing_cfo_is_penalised_and_missing_quarters_neutral():
    annual = annual_good(cfo=None)
    q, q_gaps, _ = mb.quality_metrics(annual, INFO)
    g, g_gaps, _ = mb.growth_metrics(annual, [], {})
    cfo = next(m for m in q if m["key"] == "cfo_pat")
    accel = next(m for m in g if m["key"] == "accel")
    assert cfo["score"] == 25 and cfo["status"] == "penalised"
    assert accel["score"] == 50 and accel["status"] == "neutral"
    assert "cfo_unknown" in q_gaps and "quarterly_unknown" in g_gaps


def test_missing_inputs_never_score_zero():
    empty = [{"year": y, "revenue": None, "net_income": None} for y in (2023, 2024, 2025)]
    for ms in (mb.growth_metrics(empty, [], {})[0], mb.quality_metrics(empty, {})[0],
               mb.valuation_metrics(None, None, {}, None, None)[0], mb.tailwind_metrics(None, None, None)[0]):
        for m in ms:
            assert m["status"] in ("neutral", "penalised") and m["score"] in (25, 50), m


def test_unexplainable_price_and_shrinking_profit_score_zero():
    _, _, _ = mb.valuation_metrics(None, None, {}, None, None)
    m, _, _ = mb.valuation_metrics(None, None, {"trailingPE": 40}, -5.0, 1000)
    assert next(x for x in m if x["key"] == "peg")["score"] == 0


# AC4 — layer and system score
def test_layer_and_system_score_by_hand():
    metrics = [mb.metric("a", "a", 25, 1, 80, ""), mb.metric("b", "b", 75, None, None, "", missing="penalised")]
    lay = mb.layer_score(metrics, 30)
    assert lay["score"] == pytest.approx((25 * 80 + 75 * 25) / 100, abs=0.05) and lay["coverage_pct"] == 25.0
    layers = {k: {"score": s} for k, s in zip(mb.WEIGHTS, [80, 70, 60, 50, 40])}
    assert mb.system_score(layers) == pytest.approx((30 * 80 + 25 * 70 + 15 * 60 + 15 * 50 + 15 * 40) / 100, abs=0.1)
    assert sum(mb.WEIGHTS.values()) == 100


# AC5 — tiers and weak-link cap
def test_weak_layer_caps_tier():
    layers = {"growth": {"score": 95}, "quality": {"score": 95}, "valuation": {"score": 90}, "tailwind": {"score": 90}, "price": {"score": 20}}
    t = mb.tier_for(80, layers)
    assert t["tier"] == "C" and t["capped_by_weak_layer"] and t["weak_layer"] == "price"
    ok = {k: {"score": 70} for k in mb.WEIGHTS}
    assert [mb.tier_for(s, ok)["tier"] for s in (75, 74.9, 60, 45, 44.9)] == ["A", "B", "B", "C", "D"]


# AC6 — timing flag
def test_timing_flags():
    down = make_bars(np.linspace(300, 100, 400))
    assert mb.timing_flag(down, None)[0] == "downtrend"
    rocket = make_bars(np.r_[np.linspace(100, 150, 300), np.linspace(150, 260, 30)])
    assert mb.timing_flag(rocket, None)[0] == "extended"
    steady = make_bars(np.r_[np.geomspace(30, 180, 300), np.linspace(180, 200, 100)])
    assert mb.timing_flag(steady, None)[0] == "entry_zone"
    # Linear uptrend (never doubled off its 52w low) with a 9% pullback: trend template fails → watch
    pull = make_bars(np.r_[np.linspace(50, 200, 380), np.linspace(200, 182, 20)])
    assert mb.timing_flag(pull, None)[0] == "watch"


# AC7 — coverage gate
def test_low_growth_coverage_goes_to_insufficient():
    annual = annual_good(ebit=None)
    for row in annual:   # revenue present only in 3 years, profit CAGR not computable
        row["net_income"] = 1e8
    annual[0]["net_income"] = -1e7   # turnaround base → pat_cagr neutral
    annual[0]["revenue"] = None
    deep = {"annual": annual, "quarterly": [], "info": {**INFO, "netIncomeToCommon": 1e8}, "valuation_data": None}
    r = mb.evaluate_stock(symbol="X.NS", company_name="X", listing_sector="Industrials", frame=uptrend(), bench=None,
                          deep=deep, sector_rank=(1, 5), rs_pctl=0.9, peer_pctl=0.9)
    assert r["status"] == "insufficient" and "coverage" in r["reason"]


def test_full_stock_scores_with_reasons():
    deep = {"annual": annual_good(), "quarterly": [], "info": INFO, "valuation_data": None}
    r = mb.evaluate_stock(symbol="X.NS", company_name="X", listing_sector="Industrials", frame=uptrend(),
                          bench=pd.Series(100.0, index=uptrend().index), deep=deep, sector_rank=(1, 5), rs_pctl=0.9, peer_pctl=0.9)
    assert r["status"] == "scored" and 0 <= r["score"] <= 100 and r["tier"] in "ABCD"
    assert r["qualifies"] and {t["code"] for t in r["thesis_check"]} == {f"TB{i}" for i in range(1, 8)}


# AC8 — demo data deterministic, no network
def test_demo_deep_financials_deterministic(monkeypatch):
    monkeypatch.setattr(mbd, "fetch_deep_financials", lambda *a, **k: pytest.fail("network called"))
    a = mbd.get_deep_financials("ABC.NS", demo=True)
    b = mbd.get_deep_financials("ABC.NS", demo=True)
    assert a == b and len(a["annual"]) == 4 and a["source"] == "demo"


# AC9 — cache reuse and refresh
def test_cache_reused_until_refresh():
    calls = []

    def fake(symbol, fundamentals):
        calls.append(symbol)
        return {"source": "yahoo", "annual": [], "quarterly": [], "info": {}, "valuation_data": None}

    sym = f"CACHE{np.random.randint(1e9)}.NS"
    mbd.get_deep_financials(sym, demo=False, fetcher=fake)
    mbd.get_deep_financials(sym, demo=False, fetcher=fake)
    assert len(calls) == 1
    mbd.get_deep_financials(sym, demo=False, refresh=True, fetcher=fake)
    assert len(calls) == 2


def test_failed_fetch_becomes_error_not_exception():
    def boom(symbol, fundamentals):
        raise RuntimeError("blocked")

    out = mbd.get_deep_financials(f"FAIL{np.random.randint(1e9)}.NS", demo=False, fetcher=boom)
    assert out["error"] == "blocked"


def test_statement_parsing():
    cols = [pd.Timestamp("2024-03-31"), pd.Timestamp("2025-03-31")]
    inc = pd.DataFrame({"Total Revenue": [100.0, 120.0], "EBIT": [10.0, 15.0], "Net Income": [7.0, 9.0]}, index=cols).T
    bal = pd.DataFrame({"Total Assets": [200.0, 220.0], "Total Debt": [20.0, 10.0]}, index=cols).T
    cf = pd.DataFrame({"Operating Cash Flow": [8.0, 11.0], "Capital Expenditure": [-3.0, -4.0]}, index=cols).T
    rows = mbd.annual_from_statements(inc, bal, cf)
    assert [r["year"] for r in rows] == [2024, 2025] and rows[1]["fcf"] == 7.0 and rows[0]["total_debt"] == 20.0


def test_quarter_yoy_matches_by_date_not_position():
    q = [{"period": p, "revenue": r} for p, r in [
        ("2024-03-31", 100.0), ("2024-06-30", 110.0), ("2024-09-30", 120.0),
        # 2024-12-31 missing
        ("2025-03-31", 150.0), ("2025-06-30", 121.0)]]
    assert mb.quarter_yoy(q, "revenue") == pytest.approx(121 / 110 - 1)      # Jun-25 vs Jun-24
    assert mb.quarter_yoy(q, "revenue", back=1) == pytest.approx(150 / 100 - 1)
    assert mb.quarter_yoy(q, "revenue", back=2) is None                      # Sep-24 has no year-ago quarter
