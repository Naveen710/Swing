from __future__ import annotations

import math

import pandas as pd

from app.schemas import BacktestStats, PatternType
from app.services.patterns import detect_best_pattern
from app.services.relative_strength import (
    RelativeStrengthContext,
    build_relative_strength_snapshot,
)


def backtest_pattern(
    frame: pd.DataFrame,
    pattern: PatternType,
    benchmark_context: RelativeStrengthContext | None = None,
) -> BacktestStats:
    total_trades = 0
    wins = 0
    losses = 0
    returns: list[float] = []
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
        returns.append(outcome)

        if outcome > 0:
            wins += 1
            gross_gain += outcome
        else:
            losses += 1
            gross_loss += abs(outcome)

        max_drawdown = min(max_drawdown, outcome)

    if total_trades == 0:
        return BacktestStats(
            pattern=pattern,
            total_trades=0,
            win_rate=0.0,
            average_return_pct=0.0,
            max_drawdown_pct=0.0,
            profit_factor=0.0,
        )

    profit_factor = gross_gain / gross_loss if gross_loss else gross_gain
    average_return = sum(returns) / len(returns)

    return BacktestStats(
        pattern=pattern,
        total_trades=total_trades,
        win_rate=round(wins / total_trades, 3),
        average_return_pct=round(average_return, 2),
        max_drawdown_pct=round(max_drawdown, 2),
        profit_factor=round(profit_factor, 2) if not math.isinf(profit_factor) else 99.0,
    )


def _resolve_trade(
    future: pd.DataFrame,
    entry: float,
    stop: float,
    target: float,
) -> float:
    for _, row in future.iterrows():
        if row["Low"] <= stop:
            return round(((stop / entry) - 1) * 100, 2)
        if row["High"] >= target:
            return round(((target / entry) - 1) * 100, 2)

    close = future.iloc[-1]["Close"]
    return round(((close / entry) - 1) * 100, 2)
