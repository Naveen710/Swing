from __future__ import annotations

import math

import pandas as pd

from app.config import settings
from app.schemas import BacktestStats, PatternType
from app.services.patterns import detect_best_pattern
from app.services.relative_strength import (
    RelativeStrengthContext,
    build_relative_strength_snapshot,
)
from app.services.trade_sim import plan_is_tradeable, plan_trade, simulate_trade

MIN_HISTORY = 140


def backtest_pattern(
    frame: pd.DataFrame,
    pattern: PatternType,
    benchmark_context: RelativeStrengthContext | None = None,
) -> BacktestStats:
    """Replay this stock's history, taking every occurrence of `pattern` with the
    exact entry / stop / target rules the live scanner uses (see trade_sim)."""
    signals = 0
    returns: list[float] = []
    r_multiples: list[float] = []
    holding: list[int] = []
    target_sessions: list[int] = []
    target_hits = 0

    index = MIN_HISTORY
    last = len(frame) - 1
    while index < last:
        snapshot = frame.iloc[: index + 1]
        snapshot_rs = _rs_at(snapshot, benchmark_context)
        match = detect_best_pattern(snapshot, snapshot_rs)
        if match is None or match.pattern != pattern:
            index += 1
            continue

        plan = plan_trade(snapshot.iloc[-1], match.trigger_price, match.support_price, match.reward_multiple)
        if not plan_is_tradeable(plan):
            index += 1
            continue
        outcome = simulate_trade(frame.iloc[index + 1 :], plan)
        if outcome.status == "open":
            break  # the most recent setups can't be judged yet
        signals += 1
        if outcome.status == "expired":
            index += settings.trade_fill_window_sessions
            continue

        returns.append(outcome.return_pct_net)
        r_multiples.append(outcome.r_multiple_net)
        holding.append(outcome.sessions_held)
        if outcome.status == "target":
            target_hits += 1
            target_sessions.append(outcome.sessions_held)
        # One position at a time: the next signal can only come after this trade exits.
        index += (outcome.exit_index or 0) + 2

    trades = len(returns)
    if trades == 0:
        return BacktestStats(
            pattern=pattern, total_trades=0, win_rate=0.0, average_return_pct=0.0,
            max_drawdown_pct=0.0, profit_factor=0.0, target_hit_rate=0.0,
            average_holding_sessions=0.0, average_target_sessions=None,
            signals=signals, fill_rate=0.0, average_r=0.0,
            cost_pct=settings.trade_round_trip_cost_pct,
        )

    gains = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r <= 0)
    profit_factor = gains / losses if losses else (99.0 if gains else 0.0)

    return BacktestStats(
        pattern=pattern,
        total_trades=trades,
        win_rate=round(sum(1 for r in returns if r > 0) / trades, 3),
        average_return_pct=round(sum(returns) / trades, 2),
        max_drawdown_pct=round(_max_drawdown(returns), 2),
        profit_factor=round(min(profit_factor, 99.0), 2) if not math.isinf(profit_factor) else 99.0,
        target_hit_rate=round(target_hits / trades, 3),
        average_holding_sessions=round(sum(holding) / trades, 1),
        average_target_sessions=round(sum(target_sessions) / len(target_sessions), 1) if target_sessions else None,
        signals=signals,
        fill_rate=round(trades / signals, 3) if signals else 0.0,
        average_r=round(sum(r_multiples) / trades, 3),
        cost_pct=settings.trade_round_trip_cost_pct,
    )


def _rs_at(snapshot: pd.DataFrame, benchmark_context: RelativeStrengthContext | None):
    if benchmark_context is None:
        return None
    bench = benchmark_context.benchmark_frame.loc[: snapshot.index[-1]]
    if bench.empty:
        return None
    return build_relative_strength_snapshot(
        snapshot,
        RelativeStrengthContext(benchmark_listing=benchmark_context.benchmark_listing, benchmark_frame=bench),
    )


def _max_drawdown(returns_pct: list[float]) -> float:
    """Worst peak-to-trough fall of the equity curve from taking these trades in sequence."""
    equity = peak = 1.0
    worst = 0.0
    for r in returns_pct:
        equity *= 1 + r / 100
        peak = max(peak, equity)
        worst = min(worst, (equity / peak - 1) * 100)
    return worst
