"""Single source of truth for how a signal becomes a trade and how that trade plays out.

The live scanner, the per-stock backtest, the signal ledger and the portfolio
simulator all call these functions, so a backtest statistic always describes
exactly the trade the scanner recommends today.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.config import settings


@dataclass(frozen=True)
class TradePlan:
    entry: float
    stop: float
    target: float

    @property
    def risk(self) -> float:
        return self.entry - self.stop

    @property
    def reward_multiple(self) -> float:
        return (self.target - self.entry) / self.risk if self.risk > 0 else 0.0


@dataclass(frozen=True)
class TradeOutcome:
    # "expired": trigger never hit inside the fill window
    # "open": not enough bars yet to resolve
    # "target" | "stop" | "time": filled and closed
    status: str
    fill_index: int | None = None
    fill_price: float | None = None
    exit_index: int | None = None
    exit_price: float | None = None
    sessions_held: int = 0
    return_pct_net: float = 0.0
    r_multiple_net: float = 0.0

    @property
    def filled(self) -> bool:
        return self.fill_index is not None

    @property
    def resolved(self) -> bool:
        return self.status in {"expired", "target", "stop", "time"}

    @property
    def win(self) -> bool:
        return self.filled and self.resolved and self.return_pct_net > 0


def plan_trade(latest: pd.Series, trigger_price: float, support_price: float, reward_multiple: float) -> TradePlan:
    """Entry at the trigger (or current close if already above), stop under structure, target at N×R."""
    current = float(latest["Close"])
    atr = float(latest["atr14"])
    entry = round(max(current, trigger_price), 2)
    technical_stop = min(support_price * 0.995, entry - atr * 0.8)
    risk = max(entry - technical_stop, atr * 1.1, entry * 0.022)
    stop = round(entry - risk, 2)
    target = round(entry + risk * reward_multiple, 2)
    return TradePlan(entry=entry, stop=stop, target=target)


def plan_is_tradeable(plan: TradePlan) -> bool:
    """A swing trade whose structural stop sits more than MAX_STOP_PCT below entry
    implies a target it can't reach in the holding window, so the setup is skipped."""
    return plan.risk > 0 and plan.risk / plan.entry * 100 <= settings.max_stop_pct


def simulate_trade(
    future: pd.DataFrame,
    plan: TradePlan,
    *,
    fill_window: int | None = None,
    max_hold: int | None = None,
    cost_pct: float | None = None,
) -> TradeOutcome:
    """Play a plan forward over the bars *after* the signal bar.

    Conservative, realistic rules:
    - Buy-stop order: fills the first bar whose high reaches the entry, at max(open, entry)
      so gap-ups fill worse than planned. Unfilled after `fill_window` bars → expired.
    - Stop checked before target on every bar (assume the worst when a bar spans both).
    - Gaps through the stop exit at the open, not the stop price.
    - Time stop: exit at the close after `max_hold` sessions.
    - Round-trip costs (brokerage, STT, stamp duty, slippage) are deducted.
    """
    fill_window = settings.trade_fill_window_sessions if fill_window is None else fill_window
    max_hold = settings.trade_max_hold_sessions if max_hold is None else max_hold
    cost_pct = settings.trade_round_trip_cost_pct if cost_pct is None else cost_pct

    if plan.risk <= 0 or future.empty:
        return TradeOutcome(status="open" if future.empty else "expired")

    opens = future["Open"].to_numpy(dtype=float)
    highs = future["High"].to_numpy(dtype=float)
    lows = future["Low"].to_numpy(dtype=float)
    closes = future["Close"].to_numpy(dtype=float)
    n = len(future)

    fill_idx = None
    for i in range(min(fill_window, n)):
        if highs[i] >= plan.entry:
            fill_idx = i
            break
    if fill_idx is None:
        return TradeOutcome(status="expired" if n >= fill_window else "open")

    fill_price = max(opens[fill_idx], plan.entry)
    if fill_price >= plan.target:
        # Gapped straight past the target: the move already happened, don't chase it.
        return TradeOutcome(status="expired")

    def finish(status: str, exit_idx: int, exit_price: float) -> TradeOutcome:
        gross = (exit_price / fill_price - 1) * 100
        net = gross - cost_pct
        # R is measured against the risk actually taken from the fill (a gap-up fill
        # widens it), matching how the journal and the portfolio simulator size trades.
        actual_risk = max(fill_price - plan.stop, 1e-9)
        r = (exit_price - fill_price) / actual_risk - (fill_price * cost_pct / 100) / actual_risk
        return TradeOutcome(
            status=status,
            fill_index=fill_idx,
            fill_price=round(fill_price, 2),
            exit_index=exit_idx,
            exit_price=round(exit_price, 2),
            sessions_held=exit_idx - fill_idx + 1,
            return_pct_net=round(net, 3),
            r_multiple_net=round(r, 3),
        )

    for i in range(fill_idx, n):
        # On the fill bar the open is already priced into the fill; only intrabar moves matter.
        bar_open = fill_price if i == fill_idx else opens[i]
        if lows[i] <= plan.stop:
            return finish("stop", i, min(bar_open, plan.stop))
        if highs[i] >= plan.target:
            return finish("target", i, max(bar_open, plan.target) if i > fill_idx else plan.target)
        if i - fill_idx + 1 >= max_hold:
            return finish("time", i, closes[i])

    return TradeOutcome(status="open", fill_index=fill_idx, fill_price=round(fill_price, 2))
