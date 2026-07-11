from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.schemas import ScanResponse, ScanUniverse, TradeSetup


@dataclass
class SignalSnapshot:
    signals: list[TradeSetup] = field(default_factory=list)
    generated_at: datetime | None = None
    universe_size: int = 0
    scanned_symbols: int = 0


@dataclass
class ScanCacheEntry:
    response: ScanResponse
    cached_at: datetime


class SignalStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshots: dict[ScanUniverse, SignalSnapshot] = {}
        self._universe_size = 0
        self._scanned_symbols = 0
        self._scan_in_progress = False
        self._universe: ScanUniverse | None = None
        self._scan_cache: dict[str, ScanCacheEntry] = {}

    def replace(
        self,
        signals: Sequence[TradeSetup],
        *,
        universe: ScanUniverse,
        generated_at: datetime,
        universe_size: int,
        scanned_symbols: int,
    ) -> None:
        with self._lock:
            self._snapshots[universe] = SignalSnapshot(
                signals=list(signals),
                generated_at=generated_at,
                universe_size=universe_size,
                scanned_symbols=scanned_symbols,
            )
            self._universe = universe
            self._universe_size = universe_size
            self._scanned_symbols = scanned_symbols
            self._scan_in_progress = False

    def all(self, universe: ScanUniverse | None = None) -> list[TradeSetup]:
        with self._lock:
            snapshot = self._select_snapshot(universe)
            if snapshot is None:
                return []
            return list(snapshot.signals)

    def find(self, symbol: str) -> TradeSetup | None:
        symbol_upper = symbol.upper()
        with self._lock:
            ordered_snapshots = sorted(
                (
                    snapshot
                    for snapshot in self._snapshots.values()
                    if snapshot.generated_at is not None
                ),
                key=lambda snapshot: snapshot.generated_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
            for snapshot in ordered_snapshots:
                match = next(
                    (
                        signal
                        for signal in snapshot.signals
                        if signal.symbol.upper() == symbol_upper
                    ),
                    None,
                )
                if match is not None:
                    return match
            return None

    def snapshot(
        self,
        universe: ScanUniverse,
        max_results: int | None = None,
    ) -> tuple[
        datetime | None,
        int,
        int,
        list[TradeSetup],
    ]:
        with self._lock:
            snapshot = self._snapshots.get(universe)
            if snapshot is None:
                return (None, 0, 0, [])

            signals = list(snapshot.signals)
            if max_results is not None:
                signals = signals[:max_results]
            return (
                snapshot.generated_at,
                snapshot.universe_size,
                snapshot.scanned_symbols,
                signals,
            )

    def begin_scan(self, *, universe: ScanUniverse, universe_size: int) -> bool:
        with self._lock:
            if self._scan_in_progress:
                return False
            self._universe = universe
            self._universe_size = universe_size
            self._scanned_symbols = 0
            self._scan_in_progress = True
            return True

    def finish_scan(self) -> None:
        with self._lock:
            self._scan_in_progress = False

    def update_progress(self, *, scanned_symbols: int, universe_size: int | None = None) -> None:
        with self._lock:
            self._scanned_symbols = scanned_symbols
            if universe_size is not None:
                self._universe_size = universe_size

    def status(self) -> tuple[ScanUniverse | None, bool, datetime | None, int, int, int]:
        with self._lock:
            snapshot = self._select_snapshot(self._universe)
            return (
                self._universe,
                self._scan_in_progress,
                snapshot.generated_at if snapshot is not None else None,
                self._universe_size,
                self._scanned_symbols,
                len(snapshot.signals) if snapshot is not None else 0,
            )

    def get_scan_cache(
        self,
        key: str,
        *,
        ttl_minutes: int,
    ) -> ScanResponse | None:
        with self._lock:
            entry = self._scan_cache.get(key)
            if entry is None:
                return None

            if datetime.now(UTC) - entry.cached_at > timedelta(minutes=ttl_minutes):
                self._scan_cache.pop(key, None)
                return None

            return entry.response

    def put_scan_cache(self, key: str, response: ScanResponse) -> None:
        with self._lock:
            self._scan_cache[key] = ScanCacheEntry(
                response=response,
                cached_at=datetime.now(UTC),
            )

    def _select_snapshot(self, universe: ScanUniverse | None) -> SignalSnapshot | None:
        if universe is not None:
            return self._snapshots.get(universe)

        if self._universe is not None and self._universe in self._snapshots:
            return self._snapshots[self._universe]

        generated_snapshots = [
            snapshot
            for snapshot in self._snapshots.values()
            if snapshot.generated_at is not None
        ]
        if not generated_snapshots:
            return None

        return max(
            generated_snapshots,
            key=lambda snapshot: snapshot.generated_at or datetime.min.replace(tzinfo=UTC),
        )


signal_store = SignalStore()
