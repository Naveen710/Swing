from __future__ import annotations

import numpy as np
import pandas as pd


def apply_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    enriched = frame.copy()

    enriched["ema20"] = enriched["Close"].ewm(span=20, adjust=False).mean()
    enriched["ema50"] = enriched["Close"].ewm(span=50, adjust=False).mean()
    enriched["ema200"] = enriched["Close"].ewm(span=200, adjust=False).mean()

    delta = enriched["Close"].diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.ewm(alpha=1 / 14, adjust=False).mean()
    average_loss = losses.ewm(alpha=1 / 14, adjust=False).mean()
    rs = average_gain / average_loss.replace(0, np.nan)
    enriched["rsi14"] = 100 - (100 / (1 + rs))
    enriched["rsi14"] = enriched["rsi14"].fillna(50.0)

    previous_close = enriched["Close"].shift(1)
    true_range = pd.concat(
        [
            enriched["High"] - enriched["Low"],
            (enriched["High"] - previous_close).abs(),
            (enriched["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    enriched["atr14"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()

    enriched["volume_avg20"] = enriched["Volume"].rolling(20).mean()
    enriched["volume_ratio"] = (
        enriched["Volume"] / enriched["volume_avg20"].replace(0, np.nan)
    ).fillna(1.0)

    enriched["rolling_high_20"] = enriched["High"].rolling(20).max()
    enriched["rolling_low_20"] = enriched["Low"].rolling(20).min()
    enriched["rolling_high_30"] = enriched["High"].rolling(30).max()
    enriched["rolling_low_30"] = enriched["Low"].rolling(30).min()

    return enriched
