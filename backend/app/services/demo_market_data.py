from __future__ import annotations

import math
import zlib
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.services.universe import StockListing


class DemoMarketDataProvider:
    """Creates deterministic OHLCV data so the app works without live APIs."""

    def get_history(self, listing: StockListing, lookback_days: int = 320) -> pd.DataFrame:
        total_days = max(lookback_days + 80, 260)
        dates = pd.bdate_range(end=datetime.now(UTC), periods=total_days)
        seed = zlib.crc32(listing.symbol.encode("utf-8"))
        rng = np.random.default_rng(seed)

        drift = 0.0006 + (seed % 7) * 0.00004
        volatility = 0.012 + (seed % 5) * 0.0015
        base_price = 120 + (seed % 250)

        close = np.empty(total_days, dtype=float)
        close[0] = base_price
        for index in range(1, total_days):
            shock = rng.normal(drift, volatility)
            cyclical = math.sin(index / 17) * 0.0008
            close[index] = max(30.0, close[index - 1] * (1 + shock + cyclical))

        volume = rng.integers(1_000_000, 12_000_000, size=total_days).astype(int)

        scenario = seed % 3
        if scenario == 0:
            self._apply_consolidation_breakout(close, volume)
        elif scenario == 1:
            self._apply_ema_pullback(close, volume)
        else:
            self._apply_vcp(close, volume)

        open_prices = close * (1 + rng.normal(0, 0.003, total_days))
        spread = np.abs(rng.normal(0.007, 0.002, total_days))
        high = np.maximum(open_prices, close) * (1 + spread)
        low = np.minimum(open_prices, close) * (1 - spread)

        frame = pd.DataFrame(
            {
                "Open": open_prices,
                "High": high,
                "Low": low,
                "Close": close,
                "Volume": volume,
            },
            index=dates,
        )

        return frame.round(2)

    def _apply_consolidation_breakout(self, close: np.ndarray, volume: np.ndarray) -> None:
        anchor = close[-35]
        base = np.linspace(anchor * 0.98, anchor * 1.02, 24)
        noise = np.sin(np.arange(24) / 2.2) * anchor * 0.006
        close[-26:-2] = base + noise
        close[-2] = close[-3] * 1.004
        close[-1] = max(close[-26:-1]) * 1.035
        volume[-15:-1] = (volume[-15:-1] * 0.72).astype(int)
        volume[-1] = int(volume[-20:-1].mean() * 2.4)

    def _apply_ema_pullback(self, close: np.ndarray, volume: np.ndarray) -> None:
        start = close[-46]
        trend = np.linspace(start, start * 1.18, 32)
        pullback = np.linspace(trend[-1] * 0.97, trend[-1] * 0.93, 9)
        rebound = np.linspace(pullback[-1] * 1.02, pullback[-1] * 1.09, 5)
        close[-46:-14] = trend
        close[-14:-5] = pullback
        close[-5:] = rebound
        volume[-14:-5] = (volume[-14:-5] * 0.84).astype(int)
        volume[-1] = int(volume[-20:-1].mean() * 1.7)

    def _apply_vcp(self, close: np.ndarray, volume: np.ndarray) -> None:
        anchor = close[-55]
        phase_one = anchor * (1 + np.sin(np.linspace(0, 2.8, 18)) * 0.09)
        phase_two = phase_one[-1] * (1 + np.sin(np.linspace(0, 2.5, 15)) * 0.055)
        phase_three = phase_two[-1] * (1 + np.sin(np.linspace(0, 2.0, 14)) * 0.028)
        close[-49:-31] = phase_one
        close[-31:-16] = phase_two
        close[-16:-2] = phase_three
        close[-2] = close[-3] * 1.006
        close[-1] = max(close[-25:-1]) * 1.028
        volume[-25:-10] = (volume[-25:-10] * 0.74).astype(int)
        volume[-10:-1] = (volume[-10:-1] * 0.58).astype(int)
        volume[-1] = int(volume[-20:-1].mean() * 2.1)

