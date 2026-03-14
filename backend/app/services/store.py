from __future__ import annotations

from collections.abc import Sequence

from app.schemas import TradeSetup


class SignalStore:
    def __init__(self) -> None:
        self._signals: list[TradeSetup] = []

    def replace(self, signals: Sequence[TradeSetup]) -> None:
        self._signals = list(signals)

    def all(self) -> list[TradeSetup]:
        return list(self._signals)

    def find(self, symbol: str) -> TradeSetup | None:
        symbol_upper = symbol.upper()
        return next(
            (signal for signal in self._signals if signal.symbol.upper() == symbol_upper),
            None,
        )


signal_store = SignalStore()

