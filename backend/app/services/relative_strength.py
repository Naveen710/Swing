from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.config import settings
from app.schemas import RelativeStrengthSnapshot
from app.services.universe import StockListing


@dataclass(frozen=True)
class RelativeStrengthContext:
    benchmark_listing: StockListing
    benchmark_frame: pd.DataFrame


def build_relative_strength_snapshot(
    stock_frame: pd.DataFrame,
    benchmark_context: RelativeStrengthContext | None,
) -> RelativeStrengthSnapshot:
    if benchmark_context is None:
        return _neutral_snapshot()

    stock_close = stock_frame["Close"].rename("stock_close")
    benchmark_close = benchmark_context.benchmark_frame["Close"].rename("benchmark_close")
    aligned = pd.concat([stock_close, benchmark_close], axis=1, join="inner").dropna()

    if len(aligned) < 121:
        return _neutral_snapshot(
            benchmark_symbol=benchmark_context.benchmark_listing.symbol,
            benchmark_name=benchmark_context.benchmark_listing.company_name,
        )

    stock_returns = {
        20: _pct_return(aligned["stock_close"], 20),
        50: _pct_return(aligned["stock_close"], 50),
        120: _pct_return(aligned["stock_close"], 120),
    }
    benchmark_returns = {
        20: _pct_return(aligned["benchmark_close"], 20),
        50: _pct_return(aligned["benchmark_close"], 50),
        120: _pct_return(aligned["benchmark_close"], 120),
    }
    excess_returns = {
        horizon: stock_returns[horizon] - benchmark_returns[horizon]
        for horizon in stock_returns
    }

    weighted_excess = (
        excess_returns[20] * 0.5
        + excess_returns[50] * 0.3
        + excess_returns[120] * 0.2
    )
    consistency_bonus = 0.05 * sum(excess > 0 for excess in excess_returns.values())
    score = 0.5 + weighted_excess / 40 + consistency_bonus
    score = round(min(max(score, 0.05), 0.95), 3)

    return RelativeStrengthSnapshot(
        benchmark_symbol=benchmark_context.benchmark_listing.symbol,
        benchmark_name=benchmark_context.benchmark_listing.company_name,
        score=score,
        stock_return_20d_pct=round(stock_returns[20], 2),
        benchmark_return_20d_pct=round(benchmark_returns[20], 2),
        excess_return_20d_pct=round(excess_returns[20], 2),
        stock_return_50d_pct=round(stock_returns[50], 2),
        benchmark_return_50d_pct=round(benchmark_returns[50], 2),
        excess_return_50d_pct=round(excess_returns[50], 2),
        stock_return_120d_pct=round(stock_returns[120], 2),
        benchmark_return_120d_pct=round(benchmark_returns[120], 2),
        excess_return_120d_pct=round(excess_returns[120], 2),
    )


def _pct_return(series: pd.Series, lookback: int) -> float:
    if len(series) <= lookback:
        return 0.0
    start = float(series.iloc[-(lookback + 1)])
    end = float(series.iloc[-1])
    if start == 0:
        return 0.0
    return ((end / start) - 1) * 100


def _neutral_snapshot(
    benchmark_symbol: str | None = None,
    benchmark_name: str | None = None,
) -> RelativeStrengthSnapshot:
    return RelativeStrengthSnapshot(
        benchmark_symbol=benchmark_symbol or settings.benchmark_symbol,
        benchmark_name=benchmark_name or settings.benchmark_name,
        score=0.5,
        stock_return_20d_pct=0.0,
        benchmark_return_20d_pct=0.0,
        excess_return_20d_pct=0.0,
        stock_return_50d_pct=0.0,
        benchmark_return_50d_pct=0.0,
        excess_return_50d_pct=0.0,
        stock_return_120d_pct=0.0,
        benchmark_return_120d_pct=0.0,
        excess_return_120d_pct=0.0,
    )
