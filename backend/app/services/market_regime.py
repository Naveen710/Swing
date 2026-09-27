from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime

import pandas as pd

from app.config import settings
from app.schemas import MarketCapBucket, MarketRegimeSnapshot, ScanUniverse
from app.services.market_data import MarketDataError
from app.services.universe import StockListing, get_benchmark_candidates, load_universe

logger = logging.getLogger(__name__)

# (min_probability, min_risk_reward, position_size_multiplier)
REGIME_THRESHOLDS: dict[str, tuple[float, float, float]] = {
    "bull": (0.65, 2.5, 1.0),
    "neutral": (0.72, 3.0, 0.75),
    "bear": (0.78, 3.0, 0.5),
    "unknown": (0.72, 3.0, 0.75),
}
BREADTH_MAX_AGE_SECONDS = 24 * 3600


class MarketRegimeService:
    """Answers 'is this a market worth being long in?' before any single-stock signal."""

    def __init__(self, market_data) -> None:
        self.market_data = market_data
        self._lock = threading.Lock()
        self._cached: tuple[float, MarketRegimeSnapshot] | None = None
        self._scan_breadth: tuple[float, float, int] | None = None  # (recorded_at, pct, n)

    def record_scan_breadth(self, above_ema50: int, total: int) -> None:
        if total < 20:
            return
        with self._lock:
            self._scan_breadth = (time.time(), round(above_ema50 / total * 100, 1), total)
            self._cached = None

    def get_snapshot(self, force: bool = False) -> MarketRegimeSnapshot:
        ttl = settings.regime_cache_ttl_minutes * 60
        with self._lock:
            if not force and self._cached and time.time() - self._cached[0] <= ttl:
                return self._cached[1]
        snapshot = self._compute()
        with self._lock:
            self._cached = (time.time(), snapshot)
        return snapshot

    # ── internals ──────────────────────────────────────────────────────────

    def _compute(self) -> MarketRegimeSnapshot:
        notes: list[str] = []
        points = 0
        benchmark_name = settings.benchmark_name
        close = above50 = above200 = ema_stack = ret20 = None

        frame = self._load_benchmark()
        if frame is not None and len(frame) >= 210:
            closes = frame["Close"]
            ema50 = closes.ewm(span=50, adjust=False).mean()
            ema200 = closes.ewm(span=200, adjust=False).mean()
            close = round(float(closes.iloc[-1]), 2)
            above50 = bool(closes.iloc[-1] > ema50.iloc[-1])
            above200 = bool(closes.iloc[-1] > ema200.iloc[-1])
            ema_stack = bool(ema50.iloc[-1] > ema200.iloc[-1])
            ret20 = round(float((closes.iloc[-1] / closes.iloc[-21] - 1) * 100), 2)

            points += 2 if above200 else -2
            points += 1 if above50 else -1
            points += 1 if ema_stack else -1
            if ret20 > 2:
                points += 1
            elif ret20 < -3:
                points -= 1
            notes.append(
                f"{benchmark_name} is {'above' if above200 else 'below'} its 200 EMA and "
                f"{'above' if above50 else 'below'} its 50 EMA ({ret20:+.1f}% over 20 sessions)."
            )
        else:
            notes.append("Benchmark data unavailable — regime cannot be confirmed.")

        vix = self._load_vix()
        if vix is not None:
            if vix < 14:
                points += 1
                notes.append(f"India VIX {vix:.1f} — calm conditions.")
            elif vix > 25:
                points -= 2
                notes.append(f"India VIX {vix:.1f} — high fear, gaps likely.")
            elif vix > 20:
                points -= 1
                notes.append(f"India VIX {vix:.1f} — elevated volatility.")
            else:
                notes.append(f"India VIX {vix:.1f} — normal range.")

        breadth, breadth_n, breadth_source = self._breadth()
        if breadth is not None:
            if breadth >= 60:
                points += 2
            elif breadth < 35:
                points -= 2
            elif breadth < 45:
                points -= 1
            notes.append(
                f"{breadth:.0f}% of {breadth_n} sampled stocks trade above their 50 EMA."
            )

        if close is None:
            regime = "unknown"
        elif points >= 3:
            regime = "bull"
        elif points <= -2:
            regime = "bear"
        else:
            regime = "neutral"

        min_prob, min_rr, size_mult = REGIME_THRESHOLDS[regime]
        advice = {
            "bull": "Trend and breadth support longs — standard thresholds and full size.",
            "neutral": "Mixed tape — trade only A+ setups at reduced size.",
            "bear": "Hostile tape — most breakouts fail here. Stay mostly in cash; half size at most.",
            "unknown": "Regime unconfirmed — defaulting to cautious thresholds.",
        }[regime]
        notes.insert(0, advice)

        return MarketRegimeSnapshot(
            regime=regime,
            score=round(min(max((points + 9) / 17, 0.0), 1.0), 3),
            benchmark_name=benchmark_name,
            benchmark_close=close,
            benchmark_above_ema50=above50,
            benchmark_above_ema200=above200,
            benchmark_ema50_above_ema200=ema_stack,
            benchmark_return_20d_pct=ret20,
            vix=vix,
            breadth_above_ema50_pct=breadth,
            breadth_sample_size=breadth_n,
            breadth_source=breadth_source,
            recommended_min_probability=min_prob,
            recommended_min_risk_reward=min_rr,
            position_size_multiplier=size_mult,
            notes=notes,
            generated_at=datetime.now(UTC),
        )

    def _load_benchmark(self) -> pd.DataFrame | None:
        for listing in get_benchmark_candidates():
            try:
                return self.market_data.get_history(listing, lookback_days=260)
            except MarketDataError as exc:
                logger.warning("Regime benchmark %s unavailable. %s", listing.symbol, exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Regime benchmark %s failed. %s", listing.symbol, exc)
        return None

    def _load_vix(self) -> float | None:
        if settings.market_data_provider == "demo" or not settings.enable_external_overlays:
            return None
        listing = StockListing(settings.vix_symbol, "India VIX", "Benchmark", MarketCapBucket.LARGE)
        try:
            frame = self.market_data.get_history(listing, lookback_days=120)
            value = float(frame["Close"].iloc[-1])
        except Exception as exc:  # noqa: BLE001
            logger.warning("India VIX unavailable. %s", exc)
            return None
        # Guard against synthetic/demo fallbacks producing nonsense values.
        return round(value, 2) if 5 <= value <= 90 else None

    def _breadth(self) -> tuple[float | None, int, str]:
        with self._lock:
            recorded = self._scan_breadth
        if recorded and time.time() - recorded[0] <= BREADTH_MAX_AGE_SECONDS:
            return recorded[1], recorded[2], "latest_scan"

        try:
            listings = load_universe(universe=ScanUniverse.NIFTY500)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Breadth universe unavailable. %s", exc)
            return None, 0, "unavailable"
        if not listings:
            return None, 0, "unavailable"

        sample_size = max(10, settings.regime_breadth_sample)
        step = max(1, len(listings) // sample_size)
        sample = listings[::step][:sample_size]
        try:
            self.market_data.prefetch_histories(sample, 120)
        except Exception:  # noqa: BLE001
            pass

        above = total = 0
        for listing in sample:
            try:
                closes = self.market_data.get_history(listing, lookback_days=120)["Close"]
            except Exception:  # noqa: BLE001
                continue
            if len(closes) < 60:
                continue
            total += 1
            if closes.iloc[-1] > closes.ewm(span=50, adjust=False).mean().iloc[-1]:
                above += 1
        if total < 10:
            return None, total, "unavailable"
        return round(above / total * 100, 1), total, "sample_nifty500"
