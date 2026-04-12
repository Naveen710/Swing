from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from app.schemas import BacktestStats, PatternType
from app.services.patterns import detect_best_pattern
from app.services.relative_strength import (
    RelativeStrengthContext,
    build_relative_strength_snapshot,
)


@dataclass(frozen=True)
class TradeResolution:
    return_pct: float
    holding_sessions: int
    hit_target: bool


def backtest_pattern(
    frame: pd.DataFrame,
    pattern: PatternType,
    benchmark_context: RelativeStrengthContext | None = None,
) -> BacktestStats:
    total_trades = 0
    wins = 0
    losses = 0
    returns: list[float] = []
    holding_sessions: list[int] = []
    target_sessions: list[int] = []
    target_hits = 0
    gross_gain = 0.0
    gross_loss = 0.0
    max_drawdown = 0.0

    horizon = 15
    for index in range(140, len(frame) - horizon):
        snapshot = frame.iloc[: index + 1]
        snapshot_rs = None
        if benchmark_context is not None:
            benchmark_snapshot = benchmark_context.benchmark_frame.loc[
                : snapshot.index[-1]
            ]
            if not benchmark_snapshot.empty:
                snapshot_rs = build_relative_strength_snapshot(
                    snapshot,
                    RelativeStrengthContext(
                        benchmark_listing=benchmark_context.benchmark_listing,
                        benchmark_frame=benchmark_snapshot,
                    ),
                )

        match = detect_best_pattern(snapshot, snapshot_rs)
        if match is None or match.pattern != pattern:
            continue

        total_trades += 1
        entry = snapshot.iloc[-1]["Close"]
        risk = max(snapshot.iloc[-1]["atr14"] * 1.2, entry * 0.025)
        target = entry + risk * 2.4
        stop = entry - risk

        future = frame.iloc[index + 1 : index + 1 + horizon]
        outcome = _resolve_trade(future, entry, stop, target)
        returns.append(outcome.return_pct)
        holding_sessions.append(outcome.holding_sessions)
        if outcome.hit_target:
            target_hits += 1
            target_sessions.append(outcome.holding_sessions)

        if outcome.return_pct > 0:
            wins += 1
            gross_gain += outcome.return_pct
        else:
            losses += 1
            gross_loss += abs(outcome.return_pct)

        max_drawdown = min(max_drawdown, outcome.return_pct)

    if total_trades == 0:
        return BacktestStats(
            pattern=pattern,
            total_trades=0,
            win_rate=0.0,
            average_return_pct=0.0,
            max_drawdown_pct=0.0,
            profit_factor=0.0,
            target_hit_rate=0.0,
            average_holding_sessions=0.0,
            average_target_sessions=None,
        )

    profit_factor = gross_gain / gross_loss if gross_loss else gross_gain
    average_return = sum(returns) / len(returns)
    average_holding = sum(holding_sessions) / len(holding_sessions)
    average_target = (
        round(sum(target_sessions) / len(target_sessions), 1)
        if target_sessions
        else None
    )

    return BacktestStats(
        pattern=pattern,
        total_trades=total_trades,
        win_rate=round(wins / total_trades, 3),
        average_return_pct=round(average_return, 2),
        max_drawdown_pct=round(max_drawdown, 2),
        profit_factor=round(profit_factor, 2) if not math.isinf(profit_factor) else 99.0,
        target_hit_rate=round(target_hits / total_trades, 3),
        average_holding_sessions=round(average_holding, 1),
        average_target_sessions=average_target,
    )


def _resolve_trade(
    future: pd.DataFrame,
    entry: float,
    stop: float,
    target: float,
) -> TradeResolution:
    for index, (_, row) in enumerate(future.iterrows(), start=1):
        if row["Low"] <= stop:
            return TradeResolution(
                return_pct=round(((stop / entry) - 1) * 100, 2),
                holding_sessions=index,
                hit_target=False,
            )
        if row["High"] >= target:
            return TradeResolution(
                return_pct=round(((target / entry) - 1) * 100, 2),
                holding_sessions=index,
                hit_target=True,
            )

    close = future.iloc[-1]["Close"]
    return TradeResolution(
        return_pct=round(((close / entry) - 1) * 100, 2),
        holding_sessions=len(future),
        hit_target=False,
    )
