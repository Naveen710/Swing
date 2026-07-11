from __future__ import annotations

import importlib
import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.config import settings
from app.schemas import EventRiskSnapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedCorporateEvents:
    earnings_date: date | None
    dividend_date: date | None
    cached_at: float


class YahooEventRiskProvider:
    def __init__(self) -> None:
        self.cache_ttl_seconds = settings.event_data_cache_ttl_minutes * 60
        self._cache: dict[str, CachedCorporateEvents] = {}
        self._lock = threading.Lock()

    def get_snapshot(self, symbol: str, reference_date: date) -> EventRiskSnapshot:
        events = self._get_event_dates(symbol)
        earnings_date = events.earnings_date
        dividend_date = events.dividend_date
        days_to_earnings = (
            self._trading_days_between(reference_date, earnings_date)
            if earnings_date is not None
            else None
        )
        days_to_dividend = (
            self._trading_days_between(reference_date, dividend_date)
            if dividend_date is not None
            else None
        )

        classified = self._classify_event_risk(
            earnings_date=earnings_date,
            dividend_date=dividend_date,
            days_to_earnings=days_to_earnings,
            days_to_dividend=days_to_dividend,
        )
        if classified is not None:
            return EventRiskSnapshot(
                earnings_date=earnings_date,
                days_to_earnings=days_to_earnings,
                event_date=classified["event_date"],
                days_to_event=classified["days_to_event"],
                event_type=classified["event_type"],
                risk_level=classified["risk_level"],
                ranking_penalty=classified["ranking_penalty"],
                blocked=classified["blocked"],
            )

        if earnings_date is None and dividend_date is None:
            return EventRiskSnapshot(
                earnings_date=None,
                days_to_earnings=None,
                event_date=None,
                days_to_event=None,
                event_type=None,
                risk_level="unknown",
                ranking_penalty=0.0,
                blocked=False,
            )

        next_event_type, next_event_date, next_event_days = self._resolve_next_event(
            earnings_date=earnings_date,
            dividend_date=dividend_date,
            days_to_earnings=days_to_earnings,
            days_to_dividend=days_to_dividend,
        )

        return EventRiskSnapshot(
            earnings_date=earnings_date,
            days_to_earnings=days_to_earnings,
            event_date=next_event_date,
            days_to_event=next_event_days,
            event_type=next_event_type,
            risk_level="clear",
            ranking_penalty=0.0,
            blocked=False,
        )

    def _get_event_dates(self, symbol: str) -> CachedCorporateEvents:
        now = time.time()
        with self._lock:
            cached = self._cache.get(symbol.upper())
            if cached and now - cached.cached_at <= self.cache_ttl_seconds:
                return cached

        events = self._fetch_event_dates(symbol)
        with self._lock:
            self._cache[symbol.upper()] = CachedCorporateEvents(
                earnings_date=events.earnings_date,
                dividend_date=events.dividend_date,
                cached_at=now,
            )
        return self._cache[symbol.upper()]

    def _fetch_event_dates(self, symbol: str) -> CachedCorporateEvents:
        earnings_date = None
        dividend_date = None
        try:
            yf = importlib.import_module("yfinance")
            ticker = yf.Ticker(symbol)
            calendar = ticker.calendar
        except Exception as exc:
            logger.warning("Unable to load event calendar for %s. %s", symbol, exc)
            return CachedCorporateEvents(
                earnings_date=None,
                dividend_date=None,
                cached_at=time.time(),
            )

        earnings_date = self._extract_calendar_date(calendar, "Earnings Date")
        dividend_date = self._extract_calendar_date(calendar, "Ex-Dividend Date")

        if dividend_date is None:
            try:
                info = ticker.info
                dividend_date = self._coerce_date(info.get("exDividendDate"))
            except Exception:
                dividend_date = None

        return CachedCorporateEvents(
            earnings_date=earnings_date,
            dividend_date=dividend_date,
            cached_at=time.time(),
        )

    def _classify_event_risk(
        self,
        *,
        earnings_date: date | None,
        dividend_date: date | None,
        days_to_earnings: int | None,
        days_to_dividend: int | None,
    ) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []

        if days_to_earnings is not None:
            if (
                -settings.event_risk_post_result_cooloff_days
                <= days_to_earnings
                <= settings.event_risk_exclusion_trading_days
            ):
                candidates.append(
                    {
                        "event_type": "earnings",
                        "event_date": earnings_date,
                        "days_to_event": days_to_earnings,
                        "risk_level": "blocked" if days_to_earnings >= 0 else "high",
                        "ranking_penalty": 0.24 if days_to_earnings >= 0 else 0.18,
                        "blocked": days_to_earnings >= 0,
                        "severity": 4 if days_to_earnings >= 0 else 3,
                    }
                )
            elif 0 <= days_to_earnings <= settings.event_risk_window_days:
                candidates.append(
                    {
                        "event_type": "earnings",
                        "event_date": earnings_date,
                        "days_to_event": days_to_earnings,
                        "risk_level": "elevated",
                        "ranking_penalty": 0.1,
                        "blocked": False,
                        "severity": 2,
                    }
                )

        if days_to_dividend is not None:
            if 0 <= days_to_dividend <= settings.event_risk_exclusion_trading_days:
                candidates.append(
                    {
                        "event_type": "dividend",
                        "event_date": dividend_date,
                        "days_to_event": days_to_dividend,
                        "risk_level": "blocked",
                        "ranking_penalty": 0.16,
                        "blocked": True,
                        "severity": 3,
                    }
                )
            elif 0 <= days_to_dividend <= settings.event_risk_window_days:
                candidates.append(
                    {
                        "event_type": "dividend",
                        "event_date": dividend_date,
                        "days_to_event": days_to_dividend,
                        "risk_level": "elevated",
                        "ranking_penalty": 0.06,
                        "blocked": False,
                        "severity": 1,
                    }
                )

        if not candidates:
            return None

        ranked = sorted(
            candidates,
            key=lambda candidate: (
                candidate["severity"],
                -abs(candidate["days_to_event"]),
            ),
            reverse=True,
        )
        selected = ranked[0].copy()
        selected.pop("severity", None)
        return selected

    def _resolve_next_event(
        self,
        *,
        earnings_date: date | None,
        dividend_date: date | None,
        days_to_earnings: int | None,
        days_to_dividend: int | None,
    ) -> tuple[str | None, date | None, int | None]:
        candidates: list[tuple[str, date, int]] = []
        if earnings_date is not None and days_to_earnings is not None and days_to_earnings >= 0:
            candidates.append(("earnings", earnings_date, days_to_earnings))
        if dividend_date is not None and days_to_dividend is not None and days_to_dividend >= 0:
            candidates.append(("dividend", dividend_date, days_to_dividend))
        if not candidates:
            return (None, None, None)
        event_type, event_date, days_to_event = min(candidates, key=lambda item: item[2])
        return (event_type, event_date, days_to_event)

    def _extract_calendar_date(self, calendar, key: str) -> date | None:
        raw_value = self._unwrap_calendar_value(self._extract_calendar_value(calendar, key))
        return self._coerce_date(raw_value)

    def _extract_calendar_value(self, calendar, key: str):
        if calendar is None:
            return None
        if isinstance(calendar, dict):
            return calendar.get(key)

        index = getattr(calendar, "index", None)
        if index is not None and key in index:
            try:
                return self._unwrap_calendar_value(calendar.loc[key])
            except Exception:
                return None

        columns = getattr(calendar, "columns", None)
        if columns is not None and key in columns:
            try:
                return self._unwrap_calendar_value(calendar[key])
            except Exception:
                return None
        return None

    def _unwrap_calendar_value(self, value):
        if value is None:
            return None
        if isinstance(value, (list, tuple, set)):
            for item in value:
                parsed = self._coerce_date(item)
                if parsed is not None:
                    return parsed
            return None

        if hasattr(value, "iloc"):
            try:
                return self._unwrap_calendar_value(value.iloc[0])
            except Exception:
                return None

        if hasattr(value, "values") and not isinstance(value, (str, bytes)):
            try:
                values = list(value.values)
                return self._unwrap_calendar_value(values)
            except Exception:
                return None

        return value

    def _trading_days_between(self, start: date, end: date) -> int:
        if start == end:
            return 0

        step = 1 if end > start else -1
        current = start
        trading_days = 0
        while current != end:
            current += timedelta(days=step)
            if current.weekday() < 5:
                trading_days += step
        return trading_days

    def _coerce_date(self, value) -> date | None:
        if value is None:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, (int, float)):
            try:
                timestamp = float(value)
                if timestamp > 10_000:
                    return datetime.fromtimestamp(timestamp, tz=UTC).date()
            except Exception:
                return None
        try:
            return datetime.fromisoformat(str(value)).date()
        except Exception:
            return None
