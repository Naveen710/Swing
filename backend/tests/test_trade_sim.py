"""Known-answer tests for the shared trade simulator (single source of truth for every backtest)."""
import pandas as pd
import pytest

from app.services.trade_sim import TradePlan, plan_is_tradeable, plan_trade, simulate_trade
from tests.conftest import bars

PLAN = TradePlan(entry=100.0, stop=95.0, target=110.0)


def run(rows, **kw):
    kw.setdefault("cost_pct", 0.0)
    return simulate_trade(bars(rows), PLAN, fill_window=3, max_hold=5, **kw)


def test_target_hit():
    out = run([(99, 101, 98, 100), (100, 111, 99, 110)])
    assert out.status == "target" and out.exit_price == 110 and out.r_multiple_net == pytest.approx(2.0)


def test_stop_checked_before_target_on_wide_bar():
    out = run([(99, 101, 98, 100), (100, 112, 94, 105)])
    assert out.status == "stop" and out.exit_price == 95


def test_gap_through_stop_exits_at_open():
    out = run([(99, 101, 98, 100), (90, 92, 89, 91)])
    assert out.status == "stop" and out.exit_price == 90


def test_gap_up_fill_is_worse_than_plan():
    out = run([(102, 104, 101, 103), (103, 111, 102, 110)])
    assert out.fill_price == 102


def test_gap_above_target_is_not_a_trade():
    assert run([(115, 116, 114, 115)]).status == "expired"


def test_never_triggers_expires():
    assert run([(90, 95, 89, 94)] * 3).status == "expired"


def test_not_enough_bars_is_open():
    assert run([(90, 95, 89, 94)]).status == "open"


def test_time_stop():
    out = run([(99, 101, 98, 100)] + [(100, 102, 98, 101)] * 6)
    assert out.status == "time" and out.sessions_held == 5


def test_costs_reduce_return():
    gross = run([(99, 101, 98, 100), (100, 111, 99, 110)])
    net = run([(99, 101, 98, 100), (100, 111, 99, 110)], cost_pct=0.25)
    assert net.return_pct_net == pytest.approx(gross.return_pct_net - 0.25)


def test_plan_trade_and_max_stop():
    latest = pd.Series({"Close": 100.0, "atr14": 2.0})
    plan = plan_trade(latest, trigger_price=101.0, support_price=97.0, reward_multiple=2.0)
    assert plan.entry == 101.0 and plan.stop < 97.0 and plan.reward_multiple == pytest.approx(2.0, abs=0.01)
    assert plan_is_tradeable(plan)
    assert not plan_is_tradeable(TradePlan(entry=100, stop=80, target=140))
