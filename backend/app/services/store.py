from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import UTC, datetime

from app.schemas import TradeSetup


class SignalStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._signals: list[TradeSetup] = []
        self._generated_at: datetime | None = None
        self._universe_size = 0
        self._scanned_symbols = 0
        self._scan_in_progress = False

    def replace(
        self,
        signals: Sequence[TradeSetup],
        *,
        generated_at: datetime,
        universe_size: int,
        scanned_symbols: int,
    ) -> None:
        with self._lock:
            self._signals = list(signals)
            self._generated_at = generated_at
            self._universe_size = universe_size
            self._scanned_symbols = scanned_symbols
            self._scan_in_progress = False

    def all(self) -> list[TradeSetup]:
        with self._lock:
            return list(self._signals)

    def find(self, symbol: str) -> TradeSetup | None:
        symbol_upper = symbol.upper()
        with self._lock:
            return next(
                (signal for signal in self._signals if signal.symbol.upper() == symbol_upper),
                None,
            )

    def snapshot(self, max_results: int | None = None) -> tuple[
        datetime | None,
        int,
        int,
        list[TradeSetup],
    ]:
        with self._lock:
            signals = list(self._signals)
            if max_results is not None:
                signals = signals[:max_results]
            return (
                self._generated_at,
                self._universe_size,
                self._scanned_symbols,
                signals,
            )

    def begin_scan(self, *, universe_size: int) -> bool:
        with self._lock:
            if self._scan_in_progress:
                return False
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

    def status(self) -> tuple[bool, datetime | None, int, int, int]:
        with self._lock:
            return (
                self._scan_in_progress,
                self._generated_at,
                self._universe_size,
                self._scanned_symbols,
                len(self._signals),
            )


signal_store = SignalStore()
