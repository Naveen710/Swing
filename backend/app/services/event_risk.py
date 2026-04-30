from __future__ import annotations

import importlib
import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime

from app.config import settings
from app.schemas import EventRiskSnapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedEarningsEvent:
    earnings_date: date | None
    cached_at: float


class YahooEventRiskProvider:
    def __init__(self) -> None:
        self.cache_ttl_seconds = settings.event_data_cache_ttl_minutes * 60
        self._cache: dict[str, CachedEarningsEvent] = {}
        self._lock = threading.Lock()

    def get_snapshot(self, symbol: str, reference_date: date) -> EventRiskSnapshot:
        earnings_date = self._get_earnings_date(symbol)
        if earnings_date is None:
            return EventRiskSnapshot(
                earnings_date=None,
                days_to_earnings=None,
                risk_level="unknown",
                ranking_penalty=0.0,
            )

        days_to_earnings = (earnings_date - reference_date).days
        if -settings.event_risk_post_result_cooloff_days <= days_to_earnings <= settings.event_risk_high_penalty_days:
            return EventRiskSnapshot(
                earnings_date=earnings_date,
                days_to_earnings=days_to_earnings,
                risk_level="high",
                ranking_penalty=0.18,
            )

        if days_to_earnings <= settings.event_risk_window_days:
            return EventRiskSnapshot(
                earnings_date=earnings_date,
                days_to_earnings=days_to_earnings,
                risk_level="elevated",
                ranking_penalty=0.1,
            )

        return EventRiskSnapshot(
            earnings_date=earnings_date,
            days_to_earnings=days_to_earnings,
            risk_level="clear",
            ranking_penalty=0.0,
        )

    def _get_earnings_date(self, symbol: str) -> date | None:
        now = time.time()
        with self._lock:
            cached = self._cache.get(symbol.upper())
            if cached and now - cached.cached_at <= self.cache_ttl_seconds:
                return cached.earnings_date

        earnings_date = self._fetch_earnings_date(symbol)
        with self._lock:
            self._cache[symbol.upper()] = CachedEarningsEvent(
                earnings_date=earnings_date,
                cached_at=now,
            )
        return earnings_date

    def _fetch_earnings_date(self, symbol: str) -> date | None:
        try:
            yf = importlib.import_module("yfinance")
            ticker = yf.Ticker(symbol)
            calendar = ticker.calendar
        except Exception as exc:
            logger.warning("Unable to load earnings calendar for %s. %s", symbol, exc)
            return None

        if not isinstance(calendar, dict):
            return None

        raw_dates = calendar.get("Earnings Date")
        if isinstance(raw_dates, list):
            dates = [self._coerce_date(item) for item in raw_dates]
            dates = [item for item in dates if item is not None]
            return min(dates) if dates else None

        return self._coerce_date(raw_dates)

    def _coerce_date(self, value) -> date | None:
        if value is None:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        try:
            return datetime.fromisoformat(str(value)).date()
        except Exception:
            return None
