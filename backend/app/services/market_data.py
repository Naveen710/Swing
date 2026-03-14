from __future__ import annotations

from collections import OrderedDict
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

    def prefetch_histories(
        self,
        listings: list[StockListing],
        lookback_days: int = 320,
    ) -> None:
        """Warm cache for a batch of listings before a scan starts."""


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
        self.batch_size = max(5, settings.yahoo_batch_size)
        self.memory_cache_symbols = max(0, settings.market_data_memory_cache_symbols)
        self._download_lock = threading.Lock()
        self._memory_cache: OrderedDict[str, pd.DataFrame] = OrderedDict()
        self._memory_cache_lock = threading.Lock()

    def get_history(self, listing: StockListing, lookback_days: int = 320) -> pd.DataFrame:
        required_rows = max(lookback_days + 80, 260)
        cache_path = self.cache_dir / f"{_safe_symbol_filename(listing.symbol)}.csv"
        memory_cached = self._load_memory_cache(listing.symbol, required_rows)
        if memory_cached is not None:
            return memory_cached.tail(required_rows)

        cached = self._load_cached_frame(cache_path, listing.symbol)

        if cached is not None and self._is_cache_fresh(cache_path) and len(cached) >= required_rows:
            self._save_memory_cache(listing.symbol, cached)
            return cached.tail(required_rows)

        try:
            fresh = self._download_history(listing.symbol, required_rows)
            self._save_cache(cache_path, listing.symbol, fresh)
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

    def prefetch_histories(
        self,
        listings: list[StockListing],
        lookback_days: int = 320,
    ) -> None:
        required_rows = max(lookback_days + 80, 260)
        pending: list[StockListing] = []

        for listing in listings:
            if self._load_memory_cache(listing.symbol, required_rows) is not None:
                continue

            cache_path = self.cache_dir / f"{_safe_symbol_filename(listing.symbol)}.csv"
            cached = self._load_cached_frame(cache_path, listing.symbol)
            if (
                cached is not None
                and self._is_cache_fresh(cache_path)
                and len(cached) >= required_rows
            ):
                self._save_memory_cache(listing.symbol, cached)
                continue

            pending.append(listing)

        if not pending:
            return

        for chunk in _chunked(pending, self.batch_size):
            try:
                self._download_history_batch(chunk, required_rows)
            except Exception as exc:
                symbols = ", ".join(item.symbol for item in chunk[:4])
                logger.warning(
                    "Batch Yahoo Finance prefetch failed for %s%s. Falling back to on-demand loads. %s",
                    symbols,
                    "..." if len(chunk) > 4 else "",
                    exc,
                )

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

    def _download_history_batch(
        self,
        listings: list[StockListing],
        required_rows: int,
    ) -> None:
        if not listings:
            return

        yf = self._import_yfinance()
        end = datetime.now(UTC) + timedelta(days=1)
        start = end - timedelta(days=max(required_rows * 3, 800))
        ticker_string = " ".join(listing.symbol for listing in listings)

        with self._download_lock:
            frame = yf.download(
                ticker_string,
                start=start.date().isoformat(),
                end=end.date().isoformat(),
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=True,
                timeout=self.timeout_seconds,
                group_by="column",
            )

        for listing in listings:
            try:
                symbol_frame = _extract_symbol_frame(frame, listing.symbol)
                normalized = _normalize_ohlcv_frame(symbol_frame, listing.symbol)
            except Exception as exc:
                logger.warning(
                    "No normalized batch history was available for %s. %s",
                    listing.symbol,
                    exc,
                )
                continue

            cache_path = self.cache_dir / f"{_safe_symbol_filename(listing.symbol)}.csv"
            self._save_cache(cache_path, listing.symbol, normalized)

    def _load_cached_frame(self, cache_path: Path, symbol: str) -> pd.DataFrame | None:
        if not cache_path.exists():
            return None

        frame = pd.read_csv(cache_path, index_col="Date", parse_dates=["Date"])
        return _normalize_ohlcv_frame(frame, symbol)

    def _save_cache(self, cache_path: Path, symbol: str, frame: pd.DataFrame) -> None:
        frame.to_csv(cache_path, index_label="Date")
        self._save_memory_cache(symbol, frame)

    def _load_memory_cache(
        self,
        symbol: str,
        required_rows: int,
    ) -> pd.DataFrame | None:
        if self.memory_cache_symbols <= 0:
            return None

        with self._memory_cache_lock:
            frame = self._memory_cache.get(symbol.upper())
            if frame is not None:
                self._memory_cache.move_to_end(symbol.upper())
        if frame is None or len(frame) < required_rows:
            return None
        return frame

    def _save_memory_cache(self, symbol: str, frame: pd.DataFrame) -> None:
        if self.memory_cache_symbols <= 0:
            return

        with self._memory_cache_lock:
            key = symbol.upper()
            self._memory_cache[key] = frame
            self._memory_cache.move_to_end(key)
            while len(self._memory_cache) > self.memory_cache_symbols:
                self._memory_cache.popitem(last=False)

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

    def prefetch_histories(
        self,
        listings: list[StockListing],
        lookback_days: int = 320,
    ) -> None:
        try:
            self.primary.prefetch_histories(listings, lookback_days)
        except Exception as exc:
            logger.warning(
                "Primary market data prefetch failed. Continuing with on-demand fallback. %s",
                exc,
            )


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


def _extract_symbol_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if not isinstance(frame.columns, pd.MultiIndex):
        return frame

    level_zero = {str(value) for value in frame.columns.get_level_values(0)}
    level_one = {str(value) for value in frame.columns.get_level_values(1)}

    if symbol in level_zero:
        extracted = frame[symbol]
    elif symbol in level_one:
        extracted = frame.xs(symbol, axis=1, level=1)
    else:
        raise MarketDataError(f"Batch payload did not contain data for {symbol}.")

    if isinstance(extracted, pd.Series):
        return extracted.to_frame()
    return extracted


def _chunked(items: list[StockListing], chunk_size: int) -> list[list[StockListing]]:
    return [
        items[index : index + chunk_size]
        for index in range(0, len(items), chunk_size)
    ]
