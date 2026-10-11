"""Acceptance criteria 10, 12, 13, 14: pre-filter, price backtest and forward ledger."""
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from app.config import settings
from app.schemas import MarketCapBucket
from app.services.curated_algo import Panel
from app.services.db import get_engine, multibagger_snapshots_table as snaps
from app.services.multibagger import MultibaggerService, prefilter
from app.services.multibagger_backtest import run_backtest
from app.services.scanner import scanner_service


def wide(n_days, cols, seed=1, start=100.0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2014-01-01", periods=n_days)
    rets = rng.normal(0.0003, 0.015, size=(n_days, len(cols)))
    close = pd.DataFrame(start * np.exp(np.cumsum(rets, axis=0)), index=idx, columns=cols)
    return close


def ohlcv(close, volume=1_000_000):
    high, low = close * 1.01, close * 0.99
    vol = pd.DataFrame(volume, index=close.index, columns=close.columns, dtype="float64")
    return close.shift(1).fillna(close), high, low, vol


# AC10 — pre-filter
def test_prefilter_rules_each_counted():
    cols = ["BIG.NS", "PENNY.NS", "THIN.NS", "NEW.NS", "CIRC.NS", "ASM.NS", "GOOD.NS"]
    close = wide(520, cols)
    close["PENNY.NS"] = 10.0
    close.loc[close.index[:100], "NEW.NS"] = np.nan          # only 420 sessions of history
    c = np.full(len(close), 100.0)
    for k in (-50, -30, -10):                                # three upper-circuit days
        c[k:] = c[k:] * 1.05
    close["CIRC.NS"] = c
    o, h, l, v = ohlcv(close)
    h["CIRC.NS"] = close["CIRC.NS"]                          # pinned at the high
    v["THIN.NS"] = 1000.0                                    # ₹1 lakh a day
    buckets = {"BIG.NS": MarketCapBucket.LARGE}
    survivors, elim, after_bucket = prefilter(close, h, l, v, buckets, {"ASM"}, None)
    assert survivors == ["GOOD.NS"]
    assert elim == {"large_cap": 1, "penny": 1, "illiquid": 1, "short_history": 1, "circuit": 1, "asm_gsm": 1}
    assert after_bucket == 6




# AC12/13 — backtest
def synthetic_panel(n_days=1700, n=60):
    cols = [f"S{i:02d}.NS" for i in range(n)]
    close = wide(n_days, cols, seed=7)
    up = 100 * np.exp(np.arange(n_days) * 0.0015)
    close["S00.NS"] = up                                     # always up: should lead every cohort
    jump = up.copy()
    jump[900:] *= 1.6                                        # a 60% one-day jump (bad print)
    close["S01.NS"] = jump
    o, h, l, v = ohlcv(close)
    sectors = {c: f"Sector{i % 12}" for i, c in enumerate(cols)}
    index_close = close.mean(axis=1) * 0 + pd.Series(100 * np.exp(np.arange(n_days) * 0.0003), index=close.index)
    return Panel(open=o.astype("float32"), high=h.astype("float32"), low=l.astype("float32"),
                 close=close.astype("float32"), volume=v.astype("float32"), sectors=sectors,
                 names={c: c for c in cols}, index_close=index_close, index_name="Test index", vix=None)


@pytest.fixture(scope="module")
def bt():
    p = synthetic_panel()
    return p, run_backtest(p, "nifty_smallcap_250", start="2015-01-01"), run_backtest(p, "nifty_smallcap_250", start="2015-01-01", cost_pct=0.0)


def test_always_up_stock_in_top_cohort(bt):
    _, res, _ = bt
    assert all("S00.NS" in c["members"] for c in res["recent_cohorts"])


def test_entry_is_the_session_after_the_signal(bt):
    p, res, _ = bt
    idx = list(p.close.index)
    for c in res["cohorts"]:
        signal = idx.index(pd.Timestamp(c["date"]))
        assert pd.Timestamp(c["entry_date"]) == idx[signal + 1]


def test_costs_reduce_returns_by_exactly_the_setting(bt):
    _, with_cost, no_cost = bt
    for a, b in zip(with_cost["cohorts"], no_cost["cohorts"]):
        if a["1y"] and b["1y"]:
            assert b["1y"]["cohort"] - a["1y"]["cohort"] == pytest.approx(settings.trade_round_trip_cost_pct, abs=0.011)


def test_data_break_dropped_and_counted(bt):
    _, res, _ = bt
    assert res["data_breaks_dropped"] > 0


def test_result_contract(bt):
    _, res, _ = bt
    assert res["verdict"] in {"PASS", "FAIL", "INCONCLUSIVE"}
    assert res["survivorship_warning"]
    assert all({"name", "threshold", "value", "pass"} <= set(c) for c in res["criteria"])
    assert len(res["criteria"]) == 5 and res["hindsight"]["label"].startswith("Hindsight")


def test_too_few_cohorts_is_inconclusive():
    res = run_backtest(synthetic_panel(n_days=1100), "nifty_smallcap_250", start="2015-01-01")
    assert res["verdict"] == "INCONCLUSIVE"


# AC14 — ledger
def test_ledger_grades_from_first_close_after_snapshot():
    svc = MultibaggerService(scanner_service)
    snap_date = date(2025, 1, 31)
    universe = "ledger_test"
    with get_engine().begin() as conn:
        conn.execute(snaps.delete().where(snaps.c.universe == universe))
        conn.execute(snaps.insert(), [{"snapshot_month": "2025-01", "snapshot_date": snap_date, "universe": universe,
                                       "symbol": "RELIANCE.NS", "tier": "A", "score": 80.0, "rank": 1,
                                       "recorded_at": datetime.now(UTC)}])
    svc.evaluate_ledger()
    with get_engine().connect() as conn:
        row = conn.execute(snaps.select().where(snaps.c.universe == universe)).mappings().one()
    assert row["entry_date"] > snap_date
    assert row["ret_6m"] is not None and row["ret_12m"] is not None
    assert row["ret_36m"] is None                          # not reached yet


def test_snapshot_idempotent():
    svc = MultibaggerService(scanner_service)
    result = {"as_of": "2024-05-31", "universe": "idem_test", "excluded": [], "insufficient": [],
              "results": [{"symbol": "A.NS", "company_name": "A", "sector": "IT", "rank": 1, "score": 70.0, "tier": "B",
                           "timing": "watch", "price": 100.0, "coverage_pct": 100.0,
                           "layers": {k: {"score": 50.0} for k in ("growth", "quality", "valuation", "tailwind", "price")}}]}
    first = svc.record_snapshot(result)
    second = svc.record_snapshot(result)
    assert second["recorded"] is False
    with get_engine().connect() as conn:
        n = len(conn.execute(snaps.select().where(snaps.c.universe == "idem_test")).all())
    assert n == 1 and (first["recorded"] or n == 1)


def test_scores_ignore_the_future():
    """Scrambling every bar after a signal date must not change that date's scores (no look-ahead)."""
    from app.services.multibagger_backtest import month_end_positions, price_scores

    p = synthetic_panel(n_days=1300)
    positions = month_end_positions(p.close.index, "2015-01-01", len(p.close) - 2)
    cut = positions[len(positions) // 2]
    base_scores, _ = price_scores(p, [cut])
    rng = np.random.default_rng(3)
    scrambled = Panel(**{**p.__dict__})
    for name in ("open", "high", "low", "close", "volume"):
        frame = getattr(p, name).astype("float64")
        frame.iloc[cut + 1:] = frame.iloc[cut + 1:].to_numpy() * rng.uniform(0.5, 1.5, size=frame.iloc[cut + 1:].shape)
        setattr(scrambled, name, frame.astype("float32"))
    idx = p.index_close.copy()
    idx.iloc[cut + 1:] = idx.iloc[cut + 1:] * 3
    scrambled.index_close = idx
    new_scores, _ = price_scores(scrambled, [cut])
    pd.testing.assert_frame_equal(base_scores, new_scores)


def test_small_sectors_are_neutral_in_backtest():
    from app.services.multibagger_backtest import month_end_positions, price_scores

    p = synthetic_panel(n_days=600, n=24)
    p.sectors = {c: ("Solo" if i == 0 else f"Sector{i % 4}") for i, c in enumerate(p.close.columns)}
    pos = month_end_positions(p.close.index, "2014-01-01", len(p.close) - 2)[-1:]
    a, _ = price_scores(p, pos)
    p.sectors["S00.NS"] = "Unknown"
    b, _ = price_scores(p, pos)
    # A one-member sector and an Unknown sector must score identically (both neutral)
    assert a["S00.NS"].iloc[0] == pytest.approx(b["S00.NS"].iloc[0])


def test_demo_runs_are_not_recorded_in_the_ledger():
    svc = MultibaggerService(scanner_service)
    out = svc.run(__import__("app.schemas", fromlist=["ScanUniverse"]).ScanUniverse.NIFTY_SMALLCAP_250)
    assert out["snapshot"]["recorded"] is False and "demo" in out["snapshot"]["reason"]


def test_tier_d_two_months_running_is_flagged():
    svc = MultibaggerService(scanner_service)
    with get_engine().begin() as conn:
        conn.execute(snaps.delete().where(snaps.c.universe == "dtest"))
        conn.execute(snaps.insert(), [{"snapshot_month": "2026-08", "snapshot_date": date(2026, 8, 31), "universe": "dtest",
                                       "symbol": "Z.NS", "tier": "D", "recorded_at": datetime.now(UTC)}])
    assert svc.previous_tiers("dtest", "2026-09") == {"Z.NS": "D"}
    assert svc.previous_tiers("dtest", "2026-08") == {}
