from __future__ import annotations

import importlib
import logging
import math
import threading
import time
from dataclasses import dataclass

from app.config import settings
from app.schemas import FundamentalSnapshot

logger = logging.getLogger(__name__)

# Quality gate thresholds. The goal is not to find great businesses,
# only to avoid technically-perfect charts on deteriorating ones.
MIN_REVENUE_GROWTH_PCT = 0.0
MIN_PROFIT_MARGIN_PCT = 8.0
MAX_DEBT_TO_EQUITY = 1.5
MIN_ROE_PCT = 12.0


@dataclass(frozen=True)
class _Cached:
    snapshot: FundamentalSnapshot
    cached_at: float


def unknown_fundamentals(source: str = "unavailable") -> FundamentalSnapshot:
    return FundamentalSnapshot(source=source)


class YahooFundamentalsProvider:
    def __init__(self) -> None:
        self.ttl = settings.fundamentals_cache_ttl_minutes * 60
        self._cache: dict[str, _Cached] = {}
        self._info_cache: dict[str, tuple[dict, float]] = {}
        self._lock = threading.Lock()

    def get_snapshot(self, symbol: str) -> FundamentalSnapshot:
        key = symbol.upper()
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached.cached_at <= self.ttl:
                return cached.snapshot

        snapshot = self._fetch(symbol)
        with self._lock:
            self._cache[key] = _Cached(snapshot=snapshot, cached_at=now)
        return snapshot

    def get_info(self, symbol: str) -> dict:
        """Full Yahoo profile (valuation, growth, margins, ownership...), cached like the snapshot."""
        key = symbol.upper()
        now = time.time()
        with self._lock:
            cached = self._info_cache.get(key)
            if cached and now - cached[1] <= self.ttl:
                return cached[0]
        try:
            yf = importlib.import_module("yfinance")
            info = yf.Ticker(symbol).info or {}
        except Exception as exc:
            logger.warning("Unable to load fundamentals for %s. %s", symbol, exc)
            info = {}
        if not isinstance(info, dict):
            info = {}
        with self._lock:
            self._info_cache[key] = (info, now)
        return info

    def _fetch(self, symbol: str) -> FundamentalSnapshot:
        info = self.get_info(symbol)
        if not info:
            return unknown_fundamentals()
        return build_fundamental_snapshot(info)


def build_fundamental_snapshot(info: dict) -> FundamentalSnapshot:
    revenue_growth = _pct(info.get("revenueGrowth"))
    profit_margin = _pct(info.get("profitMargins"))
    roe = _pct(info.get("returnOnEquity"))
    raw_de = _num(info.get("debtToEquity"))
    # Yahoo reports D/E as a percentage (45.0 == 0.45x).
    debt_to_equity = round(raw_de / 100, 2) if raw_de is not None else None
    insiders = _pct(info.get("heldPercentInsiders"))
    institutions = _pct(info.get("heldPercentInstitutions"))

    checks = [
        (revenue_growth, lambda v: v > MIN_REVENUE_GROWTH_PCT),
        (profit_margin, lambda v: v > MIN_PROFIT_MARGIN_PCT),
        (debt_to_equity, lambda v: v < MAX_DEBT_TO_EQUITY),
        (roe, lambda v: v > MIN_ROE_PCT),
    ]
    available = [(value, test) for value, test in checks if value is not None]
    score = sum(1 for value, test in available if test(value))
    passes: bool | None
    if len(available) >= 3:
        passes = score >= 2
    else:
        passes = None

    return FundamentalSnapshot(
        source="yahoo",
        revenue_growth_pct=revenue_growth,
        profit_margin_pct=profit_margin,
        debt_to_equity=debt_to_equity,
        return_on_equity_pct=roe,
        insider_holding_pct=insiders,
        institutional_holding_pct=institutions,
        quality_score=score,
        checks_available=len(available),
        passes=passes,
    )


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _pct(value) -> float | None:
    number = _num(value)
    return round(number * 100, 2) if number is not None else None
