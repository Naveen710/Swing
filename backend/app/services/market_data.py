from __future__ import annotations

import importlib
import logging
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import pandas as pd

from app.config import settings
from app.services.demo_market_data import DemoMarketDataProvider
from app.services.universe import StockListing

logger = logging.getLogger(__name__)

REQUIRED_OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class MarketDataError(RuntimeError):
    """Raised when live market data could not be fetched or normalized."""


class MarketDataProvider(Protocol):
    def get_history(self, listing: StockListing, lookback_days: int = 320) -> pd.DataFrame:
        """Return a normalized OHLCV frame indexed by trading date."""


class CachedYahooFinanceMarketDataProvider:
    def __init__(
        self,
        cache_dir: Path,
        cache_ttl_minutes: int,
        timeout_seconds: int,
    ) -> None:
        self.cache_dir = cache_dir / "market-data" / "yahoo"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_seconds = cache_ttl_minutes * 60
        self.timeout_seconds = timeout_seconds
        self._download_lock = threading.Lock()

    def get_history(self, listing: StockListing, lookback_days: int = 320) -> pd.DataFrame:
        required_rows = max(lookback_days + 80, 260)
        cache_path = self.cache_dir / f"{_safe_symbol_filename(listing.symbol)}.csv"
        cached = self._load_cached_frame(cache_path, listing.symbol)

        if cached is not None and self._is_cache_fresh(cache_path) and len(cached) >= required_rows:
            return cached.tail(required_rows)

        try:
            fresh = self._download_history(listing.symbol, required_rows)
            self._save_cache(cache_path, fresh)
            return fresh.tail(required_rows)
        except Exception as exc:
            if cached is not None and not cached.empty:
                logger.warning(
                    "Yahoo Finance fetch failed for %s. Using cached data instead. %s",
                    listing.symbol,
                    exc,
                )
                return cached.tail(required_rows)
            raise MarketDataError(
                f"Unable to load Yahoo Finance history for {listing.symbol}: {exc}"
            ) from exc

    def _download_history(self, symbol: str, required_rows: int) -> pd.DataFrame:
        yf = self._import_yfinance()
        end = datetime.now(UTC) + timedelta(days=1)
        start = end - timedelta(days=max(required_rows * 3, 800))

        with self._download_lock:
            frame = yf.download(
                symbol,
                start=start.date().isoformat(),
                end=end.date().isoformat(),
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
                timeout=self.timeout_seconds,
            )
        return _normalize_ohlcv_frame(frame, symbol)

    def _load_cached_frame(self, cache_path: Path, symbol: str) -> pd.DataFrame | None:
        if not cache_path.exists():
            return None

        frame = pd.read_csv(cache_path, index_col="Date", parse_dates=["Date"])
        return _normalize_ohlcv_frame(frame, symbol)

    def _save_cache(self, cache_path: Path, frame: pd.DataFrame) -> None:
        frame.to_csv(cache_path, index_label="Date")

    def _is_cache_fresh(self, cache_path: Path) -> bool:
        age_seconds = time.time() - cache_path.stat().st_mtime
        return age_seconds <= self.cache_ttl_seconds

    def _import_yfinance(self):
        try:
            return importlib.import_module("yfinance")
        except ModuleNotFoundError as exc:
            raise MarketDataError(
                "yfinance is not installed. Install backend requirements first."
            ) from exc


class FallbackMarketDataProvider:
    def __init__(
        self,
        primary: MarketDataProvider,
        fallback: MarketDataProvider,
    ) -> None:
        self.primary = primary
        self.fallback = fallback

    def get_history(self, listing: StockListing, lookback_days: int = 320) -> pd.DataFrame:
        try:
            return self.primary.get_history(listing, lookback_days)
        except MarketDataError as exc:
            logger.warning(
                "Primary market data provider failed for %s. Falling back. %s",
                listing.symbol,
                exc,
            )
            return self.fallback.get_history(listing, lookback_days)


def create_market_data_provider() -> MarketDataProvider:
    provider_name = settings.market_data_provider
    live_provider = CachedYahooFinanceMarketDataProvider(
        cache_dir=settings.cache_dir,
        cache_ttl_minutes=settings.market_data_cache_ttl_minutes,
        timeout_seconds=settings.yahoo_timeout_seconds,
    )

    if provider_name == "demo":
        return DemoMarketDataProvider()

    if provider_name in {"yahoo", "auto"}:
        if settings.allow_demo_fallback:
            return FallbackMarketDataProvider(live_provider, DemoMarketDataProvider())
        return live_provider

    raise ValueError(f"Unsupported MARKET_DATA_PROVIDER: {provider_name}")


def _normalize_ohlcv_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if frame.empty:
        raise MarketDataError(f"No OHLCV rows returned for {symbol}.")

    normalized = frame.copy()
    if isinstance(normalized.columns, pd.MultiIndex):
        normalized.columns = normalized.columns.get_level_values(0)

    if "Adj Close" in normalized.columns and "Close" not in normalized.columns:
        normalized["Close"] = normalized["Adj Close"]

    missing_columns = [
        column for column in REQUIRED_OHLCV_COLUMNS if column not in normalized.columns
    ]
    if missing_columns:
        raise MarketDataError(
            f"Missing OHLCV columns for {symbol}: {', '.join(missing_columns)}"
        )

    normalized = normalized.loc[:, REQUIRED_OHLCV_COLUMNS].copy()
    normalized.index = pd.to_datetime(normalized.index, utc=True)
    normalized = normalized[~normalized.index.duplicated(keep="last")].sort_index()

    for column in ("Open", "High", "Low", "Close", "Volume"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

    normalized = normalized.dropna(subset=["Open", "High", "Low", "Close"])
    normalized["Volume"] = normalized["Volume"].fillna(0).astype(int)

    if normalized.empty:
        raise MarketDataError(f"All OHLCV rows were invalid for {symbol}.")

    return normalized.round(2)


def _safe_symbol_filename(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", symbol)
