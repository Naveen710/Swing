from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import pandas as pd

from app.config import settings
from app.schemas import (
    AccumulationSnapshot,
    LiquiditySnapshot,
    RelativeStrengthSnapshot,
    SectorStrengthSnapshot,
)
from app.services.delivery_data import DeliveryTrend


@dataclass(frozen=True)
class SectorObservation:
    sector: str
    relative_strength_score: float
    excess_return_50d_pct: float
    excess_return_120d_pct: float
    liquidity_score: float
    accumulation_score: float


def build_liquidity_snapshot(frame: pd.DataFrame) -> LiquiditySnapshot:
    avg20 = float(frame["traded_value"].tail(20).mean())
    avg50 = float(frame["traded_value"].tail(50).mean())
    avg20_cr = round(avg20 / 10_000_000, 2)
    avg50_cr = round(avg50 / 10_000_000, 2)

    score = 0.18
    if avg20 >= 50_000_000:
        score += 0.24
    if avg20 >= 100_000_000:
        score += 0.2
    if avg20 >= 250_000_000:
        score += 0.18
    if avg50 >= 100_000_000:
        score += 0.1
    if avg50 >= 250_000_000:
        score += 0.05

    return LiquiditySnapshot(
        average_traded_value_20d_cr=avg20_cr,
        average_traded_value_50d_cr=avg50_cr,
        score=round(min(score, 0.95), 3),
        passes_filter=avg20 >= settings.liquidity_filter_min_avg_traded_value_inr,
    )


def build_accumulation_snapshot(
    frame: pd.DataFrame,
    delivery_trend: DeliveryTrend | None = None,
) -> AccumulationSnapshot:
    recent = frame.tail(10).copy()
    if recent.empty:
        return AccumulationSnapshot(
            score=0.5,
            up_volume_ratio_10d=1.0,
            volume_expansion_ratio_3d=1.0,
            atr_contraction_ratio=1.0,
            closes_near_high_10d=0,
            average_delivery_pct_10d=None,
            latest_delivery_pct=None,
            rising_delivery_days_10d=0,
            source="volume_proxy",
        )

    close_delta = recent["Close"].diff().fillna(0.0)
    up_volume = float(recent.loc[close_delta >= 0, "Volume"].sum())
    down_volume = float(recent.loc[close_delta < 0, "Volume"].sum())
    up_volume_ratio = up_volume / max(down_volume, 1.0)
    volume_expansion_ratio_3d = float(frame["volume_expansion_ratio_3d"].iloc[-1])

    atr_pct = (frame["atr14"] / frame["Close"].replace(0, pd.NA)).fillna(0.0)
    recent_atr = float(atr_pct.tail(5).mean())
    baseline_window = atr_pct.iloc[-25:-5] if len(atr_pct) >= 25 else atr_pct.iloc[:-5]
    baseline_atr = float(baseline_window.mean()) if not baseline_window.empty else recent_atr
    atr_contraction_ratio = recent_atr / max(baseline_atr, 0.0001)

    day_range = (recent["High"] - recent["Low"]).replace(0, 0.01)
    close_location = ((recent["Close"] - recent["Low"]) / day_range).clip(lower=0.0, upper=1.0)
    closes_near_high = int((close_location >= 0.7).sum())

    score = 0.18
    if up_volume_ratio >= 1.6:
        score += 0.28
    elif up_volume_ratio >= 1.2:
        score += 0.2
    elif up_volume_ratio >= 0.95:
        score += 0.12

    if volume_expansion_ratio_3d >= 1.5:
        score += 0.18
    elif volume_expansion_ratio_3d >= 1.2:
        score += 0.12
    elif volume_expansion_ratio_3d >= 1.0:
        score += 0.06

    if atr_contraction_ratio <= 0.85:
        score += 0.24
    elif atr_contraction_ratio <= 0.95:
        score += 0.18
    elif atr_contraction_ratio <= 1.05:
        score += 0.1

    if closes_near_high >= 7:
        score += 0.25
    elif closes_near_high >= 5:
        score += 0.18
    elif closes_near_high >= 3:
        score += 0.1

    if float(recent["Close"].iloc[-1]) >= float(frame["rolling_high_20"].iloc[-1]) * 0.96:
        score += 0.08

    average_delivery_pct = None
    latest_delivery_pct = None
    rising_delivery_days = 0
    source = "volume_proxy"
    if delivery_trend is not None and delivery_trend.session_count > 0:
        average_delivery_pct = delivery_trend.average_delivery_pct_10d
        latest_delivery_pct = delivery_trend.latest_delivery_pct
        rising_delivery_days = delivery_trend.rising_delivery_days_10d
        source = "nse_delivery"

        if average_delivery_pct >= 45:
            score += 0.2
        elif average_delivery_pct >= 35:
            score += 0.14
        elif average_delivery_pct >= 25:
            score += 0.08

        if latest_delivery_pct >= average_delivery_pct + 5:
            score += 0.08
        elif latest_delivery_pct >= average_delivery_pct:
            score += 0.04

        if rising_delivery_days >= 6:
            score += 0.08
        elif rising_delivery_days >= 4:
            score += 0.05

    return AccumulationSnapshot(
        score=round(min(score, 0.95), 3),
        up_volume_ratio_10d=round(up_volume_ratio, 2),
        volume_expansion_ratio_3d=round(volume_expansion_ratio_3d, 2),
        atr_contraction_ratio=round(atr_contraction_ratio, 2),
        closes_near_high_10d=closes_near_high,
        average_delivery_pct_10d=average_delivery_pct,
        latest_delivery_pct=latest_delivery_pct,
        rising_delivery_days_10d=rising_delivery_days,
        source=source,
    )


def build_sector_strength_map(
    observations: list[SectorObservation],
) -> dict[str, SectorStrengthSnapshot]:
    if not observations:
        return {}

    grouped: dict[str, list[SectorObservation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.sector].append(observation)

    ranked: list[SectorStrengthSnapshot] = []
    for sector, members in grouped.items():
        average_rs = sum(item.relative_strength_score for item in members) / len(members)
        average_excess_50 = sum(item.excess_return_50d_pct for item in members) / len(members)
        average_excess_120 = sum(item.excess_return_120d_pct for item in members) / len(members)
        average_liquidity = sum(item.liquidity_score for item in members) / len(members)
        average_accumulation = sum(item.accumulation_score for item in members) / len(members)

        trend_component = min(
            max(0.05, 0.5 + average_excess_50 / 60 + average_excess_120 / 100),
            0.95,
        )
        sector_score = (
            average_rs * 0.55
            + trend_component * 0.25
            + average_liquidity * 0.12
            + average_accumulation * 0.08
        )
        ranked.append(
            SectorStrengthSnapshot(
                sector=sector,
                score=round(min(max(sector_score, 0.05), 0.95), 3),
                rank=0,
                sector_count=0,
                average_relative_strength_score=round(average_rs, 3),
                average_excess_return_50d_pct=round(average_excess_50, 2),
                average_excess_return_120d_pct=round(average_excess_120, 2),
            )
        )

    ranked.sort(key=lambda item: item.score, reverse=True)
    sector_count = len(ranked)
    final_map: dict[str, SectorStrengthSnapshot] = {}
    for index, snapshot in enumerate(ranked, start=1):
        final_map[snapshot.sector] = snapshot.model_copy(
            update={"rank": index, "sector_count": sector_count}
        )
    return final_map


def build_sector_observation(
    sector: str,
    relative_strength: RelativeStrengthSnapshot,
    liquidity: LiquiditySnapshot,
    accumulation: AccumulationSnapshot,
) -> SectorObservation:
    return SectorObservation(
        sector=sector,
        relative_strength_score=relative_strength.score,
        excess_return_50d_pct=relative_strength.excess_return_50d_pct,
        excess_return_120d_pct=relative_strength.excess_return_120d_pct,
        liquidity_score=liquidity.score,
        accumulation_score=accumulation.score,
    )
