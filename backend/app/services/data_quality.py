"""Reject price series that would produce signals from bad data."""
from __future__ import annotations

from datetime import date

import pandas as pd

from app.config import settings


def data_quality_issue(frame: pd.DataFrame, reference_date: date) -> str | None:
    """Return a reason string if the series is unsafe to trade from, else None."""
    if frame is None or frame.empty or len(frame) < 60:
        return "insufficient history"

    last_date = frame.index[-1].date()
    lag = (reference_date - last_date).days
    if lag > settings.stale_data_max_lag_days:
        return f"stale: last bar {last_date.isoformat()} is {lag} days behind the market"

    recent = frame.tail(60)
    closes = recent["Close"]
    if closes.isna().any() or (closes <= 0).any():
        return "missing or zero prices in the last 60 sessions"

    # NSE circuit bands cap most daily moves at 20%; a >40% jump is almost always
    # an unadjusted split/bonus or a bad print, which would fake a breakout.
    moves = closes.pct_change().abs().dropna() * 100
    if not moves.empty and moves.max() > settings.stale_data_max_gap_pct:
        when = moves.idxmax().date().isoformat()
        return f"suspicious {moves.max():.0f}% one-day move on {when} (likely unadjusted corporate action)"

    if (recent["Volume"].tail(5) <= 0).all():
        return "no trading volume in the last 5 sessions (suspended?)"
    return None
