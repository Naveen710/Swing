from __future__ import annotations

import csv
import io
import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryTrend:
    average_delivery_pct_10d: float
    latest_delivery_pct: float
    rising_delivery_days_10d: int
    session_count: int


class NseDeliveryDataProvider:
    def __init__(self) -> None:
        self.cache_dir = settings.cache_dir / "delivery-data"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_seconds = settings.delivery_data_cache_ttl_minutes * 60
        self.archive_base_url = settings.nse_delivery_archive_base_url.rstrip("/")
        self._memory_cache: dict[str, dict[str, float]] = {}
        self._memory_cache_timestamps: dict[str, float] = {}
        self._lock = threading.Lock()

    def get_recent_delivery_trends(
        self,
        reference_date: date,
        sessions: int | None = None,
    ) -> dict[str, DeliveryTrend]:
        target_sessions = sessions or settings.delivery_data_sessions
        symbol_history: dict[str, list[float]] = {}
        collected_sessions = 0
        cursor = reference_date
        max_days = max(target_sessions * 4, 30)

        for _ in range(max_days):
            day_data = self._load_day(cursor)
            cursor -= timedelta(days=1)
            if not day_data:
                continue

            collected_sessions += 1
            for symbol, delivery_pct in day_data.items():
                history = symbol_history.setdefault(symbol, [])
                history.append(delivery_pct)

            if collected_sessions >= target_sessions:
                break

        trends: dict[str, DeliveryTrend] = {}
        for symbol, values in symbol_history.items():
            if not values:
                continue

            recent_values = values[:target_sessions]
            rising_days = sum(
                newer_value >= older_value
                for older_value, newer_value in zip(
                    recent_values[1:],
                    recent_values[:-1],
                    strict=False,
                )
            )
            trends[symbol] = DeliveryTrend(
                average_delivery_pct_10d=round(sum(recent_values) / len(recent_values), 2),
                latest_delivery_pct=round(recent_values[0], 2),
                rising_delivery_days_10d=rising_days,
                session_count=len(recent_values),
            )

        return trends

    def _load_day(self, trade_date: date) -> dict[str, float]:
        cache_key = trade_date.isoformat()
        now = time.time()

        with self._lock:
            cached = self._memory_cache.get(cache_key)
            cached_at = self._memory_cache_timestamps.get(cache_key, 0.0)
            if cached is not None and (now - cached_at <= self.cache_ttl_seconds or trade_date < datetime.now(UTC).date()):
                return cached

        cache_path = self.cache_dir / f"MTO_{trade_date.strftime('%d%m%Y')}.DAT"
        text: str | None = None
        if cache_path.exists():
            if trade_date < datetime.now(UTC).date() or now - cache_path.stat().st_mtime <= self.cache_ttl_seconds:
                text = cache_path.read_text(encoding="utf-8")

        if text is None:
            url = f"{self.archive_base_url}/MTO_{trade_date.strftime('%d%m%Y')}.DAT"
            try:
                response = requests.get(
                    url,
                    timeout=20,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if response.status_code != 200:
                    return {}
                text = response.text
                cache_path.write_text(text, encoding="utf-8")
            except Exception as exc:
                logger.warning("Unable to fetch NSE delivery file for %s. %s", trade_date, exc)
                return {}

        parsed = self._parse_delivery_file(text)
        with self._lock:
            self._memory_cache[cache_key] = parsed
            self._memory_cache_timestamps[cache_key] = now
        return parsed

    def _parse_delivery_file(self, raw_text: str) -> dict[str, float]:
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if len(lines) < 5:
            return {}

        header_index = next(
            (
                index
                for index, line in enumerate(lines)
                if line.startswith("Record Type,")
            ),
            None,
        )
        if header_index is None:
            return {}

        reader = csv.reader(io.StringIO("\n".join(lines[header_index:])))
        delivery_map: dict[str, float] = {}
        next(reader, None)
        for row in reader:
            if len(row) < 7:
                continue
            record_type = row[0].strip()
            if record_type != "20":
                continue
            symbol = row[2].strip().upper()
            series = row[3].strip().upper()
            if not symbol or series != "EQ":
                continue
            try:
                delivery_pct = float(row[6])
            except ValueError:
                continue
            delivery_map[f"{symbol}.NS"] = delivery_pct
        return delivery_map
