from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

from app.schemas import PeerRankSnapshot, PriceLevelSnapshot, WeeklyTrendSnapshot

NEAR_52W_HIGH_PCT = 3.0
PRICE_DISCOVERY_LOOKBACK = 5
MIN_WEEKLY_BARS = 30


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    losses = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gains / losses.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def build_price_levels(frame: pd.DataFrame) -> PriceLevelSnapshot | None:
    if len(frame) < 60:
        return None
    window = frame.tail(252)
    high_52w = float(window["High"].max())
    low_52w = float(window["Low"].min())
    close = float(frame["Close"].iloc[-1])
    distance = max(0.0, (high_52w - close) / max(high_52w, 0.01) * 100)

    price_discovery = False
    if len(frame) > 252 + PRICE_DISCOVERY_LOOKBACK:
        prior_high = float(
            frame["High"].iloc[-(252 + PRICE_DISCOVERY_LOOKBACK):-PRICE_DISCOVERY_LOOKBACK].max()
        )
        recent_close_max = float(frame["Close"].iloc[-PRICE_DISCOVERY_LOOKBACK:].max())
        price_discovery = recent_close_max > prior_high

    return PriceLevelSnapshot(
        high_52w=round(high_52w, 2),
        low_52w=round(low_52w, 2),
        distance_from_52w_high_pct=round(distance, 2),
        near_52w_high=distance <= NEAR_52W_HIGH_PCT,
        price_discovery=price_discovery,
    )


def build_weekly_trend(frame: pd.DataFrame) -> WeeklyTrendSnapshot | None:
    if not isinstance(frame.index, pd.DatetimeIndex):
        return None
    weekly = (
        frame[["Close", "Volume"]]
        .resample("W-FRI")
        .agg({"Close": "last", "Volume": "sum"})
        .dropna()
    )
    if len(weekly) < MIN_WEEKLY_BARS:
        return None

    ema20 = weekly["Close"].ewm(span=20, adjust=False).mean()
    rsi = _rsi(weekly["Close"])
    weekly_close = float(weekly["Close"].iloc[-1])
    weekly_ema20 = float(ema20.iloc[-1])
    weekly_rsi = float(rsi.iloc[-1])
    recent_volume = float(weekly["Volume"].iloc[-4:].mean())
    prior_volume = float(weekly["Volume"].iloc[-12:-4].mean())

    above = weekly_close > weekly_ema20
    rsi_ok = weekly_rsi > 50
    volume_rising = prior_volume > 0 and recent_volume > prior_volume
    checks = int(above) + int(rsi_ok) + int(volume_rising)

    return WeeklyTrendSnapshot(
        weekly_close=round(weekly_close, 2),
        weekly_ema20=round(weekly_ema20, 2),
        weekly_rsi14=round(weekly_rsi, 1),
        above_weekly_ema20=above,
        weekly_rsi_above_50=rsi_ok,
        weekly_volume_rising=volume_rising,
        checks_passed=checks,
        aligned=checks >= 2,
    )


def build_peer_ranks(
    universe_scores: dict[str, tuple[str, float]],
    symbols: list[str],
) -> dict[str, PeerRankSnapshot]:
    """Rank each symbol against every scanned stock in its sector by relative strength."""
    by_sector: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for symbol, (sector, score) in universe_scores.items():
        by_sector[sector].append((symbol, score))
    for peers in by_sector.values():
        peers.sort(key=lambda item: item[1], reverse=True)

    ranks: dict[str, PeerRankSnapshot] = {}
    for symbol in symbols:
        entry = universe_scores.get(symbol)
        if entry is None:
            continue
        sector = entry[0]
        peers = by_sector[sector]
        ordered = [peer for peer, _ in peers]
        rank = ordered.index(symbol) + 1
        count = len(ordered)
        percentile = 1.0 if count == 1 else 1 - (rank - 1) / (count - 1)
        leader = percentile >= 0.75 if count >= 4 else rank == 1
        laggard = count >= 4 and percentile < 0.5
        ranks[symbol] = PeerRankSnapshot(
            sector=sector,
            rank=rank,
            peer_count=count,
            percentile=round(percentile, 3),
            sector_leader=leader,
            sector_laggard=laggard,
            top_peers=[peer for peer in ordered[:4] if peer != symbol][:3],
        )
    return ranks
