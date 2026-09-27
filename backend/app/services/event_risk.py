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
class CachedCorporateEvents:
    earnings_date: date | None
    ex_dividend_date: date | None
    cached_at: float


class YahooEventRiskProvider:
    """Earnings and corporate-action awareness.

    Earnings inside the blackout window make a setup untradeable (gap risk ignores stops).
    Ex-dividend dates are surfaced so targets can be judged net of the dividend drop.
    """

    def __init__(self) -> None:
        self.cache_ttl_seconds = settings.event_data_cache_ttl_minutes * 60
        self._cache: dict[str, CachedCorporateEvents] = {}
        self._lock = threading.Lock()

    def get_snapshot(self, symbol: str, reference_date: date) -> EventRiskSnapshot:
        events = self._get_events(symbol)
        earnings_date = events.earnings_date
        ex_div = events.ex_dividend_date
        days_to_ex_div = (ex_div - reference_date).days if ex_div else None
        if days_to_ex_div is not None and days_to_ex_div < 0:
            ex_div, days_to_ex_div = None, None

        if earnings_date is None:
            return EventRiskSnapshot(
                earnings_date=None,
                days_to_earnings=None,
                risk_level="unknown",
                ranking_penalty=0.0,
                ex_dividend_date=ex_div,
                days_to_ex_dividend=days_to_ex_div,
                blackout=False,
            )

        days_to_earnings = (earnings_date - reference_date).days
        blackout = (
            -settings.event_risk_post_result_cooloff_days
            <= days_to_earnings
            <= settings.earnings_blackout_days
        )

        if -settings.event_risk_post_result_cooloff_days <= days_to_earnings <= settings.event_risk_high_penalty_days:
            level, penalty = "high", 0.18
        elif days_to_earnings <= settings.event_risk_window_days and days_to_earnings >= 0:
            level, penalty = "elevated", 0.1
        else:
            level, penalty = "clear", 0.0

        return EventRiskSnapshot(
            earnings_date=earnings_date,
            days_to_earnings=days_to_earnings,
            risk_level=level,
            ranking_penalty=penalty,
            ex_dividend_date=ex_div,
            days_to_ex_dividend=days_to_ex_div,
            blackout=blackout,
        )

    def _get_events(self, symbol: str) -> CachedCorporateEvents:
        now = time.time()
        key = symbol.upper()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached.cached_at <= self.cache_ttl_seconds:
                return cached

        earnings_date, ex_dividend_date = self._fetch_events(symbol)
        events = CachedCorporateEvents(
            earnings_date=earnings_date,
            ex_dividend_date=ex_dividend_date,
            cached_at=now,
        )
        with self._lock:
            self._cache[key] = events
        return events

    def _fetch_events(self, symbol: str) -> tuple[date | None, date | None]:
        try:
            yf = importlib.import_module("yfinance")
            calendar = yf.Ticker(symbol).calendar
        except Exception as exc:
            logger.warning("Unable to load corporate calendar for %s. %s", symbol, exc)
            return None, None

        if not isinstance(calendar, dict):
            return None, None

        raw_dates = calendar.get("Earnings Date")
        if isinstance(raw_dates, list):
            dates = [d for d in (self._coerce_date(item) for item in raw_dates) if d is not None]
            earnings = min(dates) if dates else None
        else:
            earnings = self._coerce_date(raw_dates)

        ex_dividend = self._coerce_date(calendar.get("Ex-Dividend Date"))
        return earnings, ex_dividend

    def _coerce_date(self, value) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return datetime.fromisoformat(str(value)).date()
        except Exception:
            return None
