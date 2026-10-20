"""Provider interfaces. Any data source (Yahoo, NSE, RSS, fixtures, manual import) implements
one or more of these, so a source can be swapped without touching the research engine.

Every call returns a ProviderResult with an explicit status — a missing or unsupported field is
reported, never silently filled.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Protocol

OK, MISSING, UNSUPPORTED, ERROR = "ok", "missing", "unsupported", "error"


@dataclass
class RawPayload:
    source_key: str           # logical identity, e.g. "yahoo:income:DIXON.NS"
    content: bytes
    content_type: str
    url: str | None = None
    published_at: datetime | None = None


@dataclass
class ProviderResult:
    status: str
    data: Any = None
    error: str | None = None
    raw: list[RawPayload] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)      # fields the provider could not supply
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == OK


@dataclass
class FactRecord:
    metric: str
    value: float | None
    unit: str                 # INR | INR_cr | INR_lakh | INR_mn | shares | ratio | <currency>
    period_end: date
    period_type: str          # FY | Q | TTM
    scope: str = "unknown"    # consolidated | standalone | unknown
    published_at: datetime | None = None
    raw_key: str | None = None
    note: str | None = None


@dataclass
class SecurityRef:
    exchange: str
    symbol: str
    provider_symbol: str | None = None
    company_name: str | None = None
    isin: str | None = None


@dataclass
class AnnouncementRecord:
    external_id: str
    category: str | None
    text: str | None
    published_at: datetime | None
    attachment_url: str | None
    isin: str | None = None
    raw_key: str | None = None


@dataclass
class NewsRecord:
    headline: str
    url: str
    publisher: str | None
    published_at: datetime | None
    summary: str | None
    feed: str
    raw_key: str | None = None


class PriceProvider(Protocol):
    name: str
    def get_prices(self, sec: SecurityRef, years: int = 3) -> ProviderResult: ...      # data: DataFrame(date, open, high, low, close, adj_close, volume, dividend, split)


class MetadataProvider(Protocol):
    name: str
    def get_metadata(self, sec: SecurityRef) -> ProviderResult: ...                    # data: dict


class FinancialsProvider(Protocol):
    name: str
    def get_financials(self, sec: SecurityRef) -> ProviderResult: ...                  # data: list[FactRecord]


class AnnouncementsProvider(Protocol):
    name: str
    def get_announcements(self, sec: SecurityRef, limit: int = 40) -> ProviderResult: ...  # data: list[AnnouncementRecord]


class NewsProvider(Protocol):
    name: str
    def get_news(self) -> ProviderResult: ...                                          # data: list[NewsRecord]


class DocumentFetcher(Protocol):
    name: str
    def fetch(self, url: str) -> ProviderResult: ...                                   # data: bytes


def with_retries(fn: Callable[[], Any], attempts: int = 3, backoff: float = 1.5, retry_on: tuple = (Exception,),
                 sleep=time.sleep) -> Any:
    """Call fn, retrying on failure with exponential backoff (1.5s, 2.25s, ...)."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except retry_on as exc:  # noqa: PERF203
            last = exc
            if i < attempts - 1:
                sleep(backoff ** (i + 1))
    assert last is not None
    raise last


class RateLimiter:
    """At most one call per `interval` seconds per instance (thread-safe enough for our pool sizes)."""

    def __init__(self, interval: float) -> None:
        import threading

        self.interval = interval
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delay = self._next - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._next = time.monotonic() + self.interval
