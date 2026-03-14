from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.schemas import PatternType, RelativeStrengthSnapshot


@dataclass(frozen=True)
class PatternMatch:
    pattern: PatternType
    strength: float
    explanation: str
    trigger_price: float
    support_price: float
    reward_multiple: float


def detect_best_pattern(
    frame: pd.DataFrame,
    relative_strength: RelativeStrengthSnapshot | None = None,
) -> PatternMatch | None:
    candidates = [
        detect_consolidation_breakout(frame),
        detect_ema_pullback(frame),
        detect_relative_strength_breakout(frame, relative_strength),
        detect_support_bounce(frame),
        detect_volatility_contraction(frame),
    ]
    matches = [candidate for candidate in candidates if candidate is not None]
    if not matches:
        return None
    return max(matches, key=lambda candidate: candidate.strength)


def detect_consolidation_breakout(frame: pd.DataFrame) -> PatternMatch | None:
    if len(frame) < 40:
        return None

    latest = frame.iloc[-1]
    prior = frame.iloc[-21:-1]
    prior_high = prior["High"].max()
    prior_low = prior["Low"].min()
    consolidation_width = (prior_high - prior_low) / max(prior_low, 1.0)
    close_ratio = latest["Close"] / max(prior_high, 1.0)
    full_trend = latest["ema20"] > latest["ema50"] > latest["ema200"]
    trend_supportive = full_trend or (
        latest["ema20"] > latest["ema50"] and latest["ema20"] > frame.iloc[-6]["ema20"]
    )

    strength = 0.0
    strength += _score_band(close_ratio, ((1.003, 0.26), (0.992, 0.2), (0.975, 0.12)))
    strength += _score_band(
        consolidation_width,
        ((0.09, 0.18), (0.13, 0.12), (0.18, 0.06)),
        invert=True,
    )
    strength += 0.16 if full_trend else 0.1 if trend_supportive else 0.0
    strength += _score_band(
        latest["volume_ratio"],
        ((1.35, 0.14), (1.0, 0.09), (0.85, 0.04)),
    )
    strength += _score_band(
        latest["rsi14"],
        ((45.0, 0.08), (38.0, 0.04)),
    ) if latest["rsi14"] <= 78 else 0.0

    if strength < 0.54:
        return None

    if close_ratio >= 1.0:
        explanation = "Tight base has started resolving above 20-day resistance."
    else:
        distance_pct = (1 - close_ratio) * 100
        explanation = (
            f"Tight base is coiling {distance_pct:.1f}% below 20-day resistance with "
            "trend support."
        )

    return PatternMatch(
        pattern=PatternType.CONSOLIDATION_BREAKOUT,
        strength=round(strength, 3),
        explanation=explanation,
        trigger_price=round(float(prior_high * 1.003), 2),
        support_price=round(float(max(prior_low, frame.iloc[-10:]["Low"].min())), 2),
        reward_multiple=2.6,
    )


def detect_ema_pullback(frame: pd.DataFrame) -> PatternMatch | None:
    if len(frame) < 80:
        return None

    latest = frame.iloc[-1]
    recent = frame.iloc[-12:]
    recent_high = recent["High"].max()
    ema20_rising = latest["ema20"] > frame.iloc[-6]["ema20"]
    ema50_rising = latest["ema50"] > frame.iloc[-12]["ema50"]
    trend_up = latest["ema20"] > latest["ema50"] > latest["ema200"]
    trend_supportive = trend_up or (
        latest["Close"] > latest["ema50"] * 0.99 and ema50_rising
    )
    touched_ema20 = recent["Low"].min() <= recent["ema20"].max() * 1.025
    touched_ema50 = recent["Low"].min() <= recent["ema50"].max() * 1.02
    close_near_support = latest["Close"] >= latest["ema50"] * 0.97
    recovering = (
        latest["Close"] >= frame.iloc[-3:]["Close"].mean() * 0.995
        and latest["High"] >= frame.iloc[-3:]["High"].max() * 0.99
    )

    strength = 0.0
    strength += 0.18 if trend_up else 0.12 if trend_supportive else 0.0
    strength += 0.16 if touched_ema20 else 0.11 if touched_ema50 else 0.0
    strength += 0.12 if close_near_support else 0.0
    strength += 0.1 if recovering else 0.05 if latest["Close"] >= frame.iloc[-2]["Close"] else 0.0
    strength += _score_band(
        latest["rsi14"],
        ((42.0, 0.1), (35.0, 0.07), (30.0, 0.04)),
    ) if latest["rsi14"] <= 72 else 0.0
    strength += _score_band(
        latest["volume_ratio"],
        ((1.2, 0.09), (0.95, 0.06), (0.8, 0.03)),
    )
    strength += 0.05 if ema20_rising else 0.0

    if strength < 0.52:
        return None

    trigger_price = recent_high * 1.002
    support_price = min(recent["Low"].min(), recent["ema50"].min())
    anchor = "20 EMA" if touched_ema20 else "50 EMA"
    explanation = (
        f"Trend is pulling back into the {anchor} with stabilization across the last "
        "few sessions."
    )
    return PatternMatch(
        pattern=PatternType.EMA_PULLBACK,
        strength=round(min(strength, 0.82), 3),
        explanation=explanation,
        trigger_price=round(float(trigger_price), 2),
        support_price=round(float(support_price), 2),
        reward_multiple=2.2,
    )


def detect_relative_strength_breakout(
    frame: pd.DataFrame,
    relative_strength: RelativeStrengthSnapshot | None,
) -> PatternMatch | None:
    if len(frame) < 140 or relative_strength is None:
        return None

    latest = frame.iloc[-1]
    recent = frame.iloc[-20:]
    prior_20_high = frame.iloc[-21:-1]["High"].max()
    prior_50_high = frame.iloc[-51:-1]["High"].max()
    close_vs_20d_high = latest["Close"] / max(prior_20_high, 1.0)
    close_vs_50d_high = latest["Close"] / max(prior_50_high, 1.0)
    full_trend = latest["ema20"] > latest["ema50"] > latest["ema200"]
    trend_supportive = full_trend or (
        latest["ema20"] > latest["ema50"]
        and latest["Close"] >= latest["ema20"] * 0.985
    )
    compression = (recent["High"].max() - recent["Low"].min()) / max(recent["Low"].min(), 1.0)
    higher_lows = recent["Low"].tail(5).mean() >= recent["Low"].head(5).mean() * 0.99

    strength = 0.0
    strength += _score_band(
        relative_strength.score,
        ((0.85, 0.26), (0.72, 0.2), (0.62, 0.14)),
    )
    strength += _score_band(
        relative_strength.excess_return_50d_pct,
        ((12.0, 0.16), (6.0, 0.11), (3.0, 0.06)),
    )
    strength += _score_band(
        relative_strength.excess_return_120d_pct,
        ((15.0, 0.12), (6.0, 0.08), (0.0, 0.04)),
    )
    strength += _score_band(
        close_vs_20d_high,
        ((1.0, 0.16), (0.988, 0.13), (0.97, 0.08)),
    )
    strength += _score_band(
        close_vs_50d_high,
        ((0.99, 0.09), (0.95, 0.05)),
    )
    strength += 0.1 if full_trend else 0.06 if trend_supportive else 0.0
    strength += _score_band(
        latest["rsi14"],
        ((52.0, 0.08), (45.0, 0.05)),
    ) if latest["rsi14"] <= 78 else 0.0
    strength += _score_band(
        latest["volume_ratio"],
        ((1.15, 0.07), (0.95, 0.04)),
    )
    strength += 0.05 if compression <= 0.11 else 0.0
    strength += 0.04 if higher_lows else 0.0

    if strength < 0.56:
        return None

    if close_vs_20d_high >= 1.0:
        explanation = (
            "Market leader is pressing into fresh short-term highs while outperforming NIFTY."
        )
    else:
        distance_pct = (1 - close_vs_20d_high) * 100
        explanation = (
            f"Market leader is only {distance_pct:.1f}% below a 20-day breakout trigger "
            "while maintaining strong outperformance vs NIFTY."
        )

    support_price = max(frame.iloc[-15:]["Low"].min(), float(latest["ema20"]) * 0.985)
    return PatternMatch(
        pattern=PatternType.RELATIVE_STRENGTH_BREAKOUT,
        strength=round(min(strength, 0.89), 3),
        explanation=explanation,
        trigger_price=round(float(prior_20_high * 1.002), 2),
        support_price=round(float(support_price), 2),
        reward_multiple=2.8,
    )


def detect_volatility_contraction(frame: pd.DataFrame) -> PatternMatch | None:
    if len(frame) < 70:
        return None

    window = frame.iloc[-49:-1]
    segment_one = window.iloc[:18]
    segment_two = window.iloc[18:33]
    segment_three = window.iloc[33:]
    contractions = [
        _segment_range(segment_one),
        _segment_range(segment_two),
        _segment_range(segment_three),
    ]
    contraction_quality = (
        contractions[0] > contractions[1] > contractions[2]
        or (
            contractions[2] < contractions[0] * 0.82
            and contractions[1] < contractions[0] * 1.05
        )
    )
    volume_dry_up = segment_three["Volume"].mean() < segment_one["Volume"].mean() * 0.92
    latest = frame.iloc[-1]
    resistance = frame.iloc[-20:-1]["High"].max()
    close_ratio = latest["Close"] / max(resistance, 1.0)
    trend_supportive = latest["ema20"] > latest["ema50"] and latest["Close"] >= latest["ema50"] * 0.98

    strength = 0.0
    strength += 0.2 if contraction_quality else 0.0
    strength += 0.12 if volume_dry_up else 0.05
    strength += _score_band(close_ratio, ((1.002, 0.2), (0.99, 0.15), (0.975, 0.09)))
    strength += 0.14 if trend_supportive else 0.0
    strength += _score_band(
        latest["volume_ratio"],
        ((1.35, 0.1), (1.0, 0.06), (0.85, 0.03)),
    )
    strength += _score_band(
        latest["rsi14"],
        ((45.0, 0.08), (38.0, 0.04)),
    ) if latest["rsi14"] <= 80 else 0.0

    if strength < 0.5:
        return None

    if close_ratio >= 1.0:
        explanation = "Volatility contractions tightened ahead of a live breakout attempt."
    else:
        distance_pct = (1 - close_ratio) * 100
        explanation = (
            f"Volatility has compressed and price is sitting {distance_pct:.1f}% below "
            "the trigger."
        )
    return PatternMatch(
        pattern=PatternType.VOLATILITY_CONTRACTION,
        strength=round(min(strength, 0.85), 3),
        explanation=explanation,
        trigger_price=round(float(resistance * 1.003), 2),
        support_price=round(float(window["Low"].min()), 2),
        reward_multiple=2.9,
    )


def detect_support_bounce(frame: pd.DataFrame) -> PatternMatch | None:
    if len(frame) < 120:
        return None

    latest = frame.iloc[-1]
    recent = frame.iloc[-10:]
    trigger_window = frame.iloc[-4:]
    support_window = frame.iloc[-90:]
    support_level = support_window["Low"].min()
    recent_high = trigger_window["High"].max()
    distance_to_support = latest["Close"] / max(support_level, 1.0) - 1
    support_tests = (support_window["Low"] <= support_level * 1.03).sum()
    close_position = (
        (latest["Close"] - latest["Low"]) / max(latest["High"] - latest["Low"], 0.01)
    )
    short_term_recovery = latest["Close"] >= frame.iloc[-3:]["Close"].mean() * 0.995
    holding_recent_low = latest["Close"] >= recent["Low"].min() * 1.002
    volume_supportive = latest["volume_ratio"] >= 0.9
    bullish_day = latest["Close"] > latest["Open"] or latest["Close"] > frame.iloc[-2]["Close"]
    rebound_from_support = close_position >= 0.55 or bullish_day or short_term_recovery

    strength = 0.0
    strength += _score_band(
        distance_to_support,
        ((0.006, 0.24), (0.015, 0.19), (0.03, 0.14), (0.05, 0.08)),
        invert=True,
    )
    strength += 0.16 if support_tests >= 4 else 0.12 if support_tests >= 2 else 0.0
    strength += 0.14 if rebound_from_support else 0.05 if holding_recent_low else 0.0
    strength += 0.08 if holding_recent_low else 0.0
    strength += _score_band(
        latest["rsi14"],
        ((22.0, 0.12), (18.0, 0.08)),
    ) if latest["rsi14"] <= 45 else _score_band(latest["rsi14"], ((45.0, 0.08),))
    strength += _score_band(
        latest["volume_ratio"],
        ((1.25, 0.1), (0.95, 0.06), (0.75, 0.03)),
    )
    strength += 0.04 if latest["Close"] >= latest["ema20"] * 0.92 else 0.0
    strength += 0.03 if latest["Close"] >= latest["ema50"] * 0.9 else 0.0

    if not volume_supportive and latest["rsi14"] < 20:
        strength -= 0.05

    if strength < 0.52:
        return None

    distance_pct = distance_to_support * 100
    explanation = (
        f"Price is basing {distance_pct:.1f}% above a well-tested 60-90 day support zone "
        "and showing early rebound behavior."
    )

    return PatternMatch(
        pattern=PatternType.SUPPORT_BOUNCE,
        strength=round(min(max(strength, 0.0), 0.8), 3),
        explanation=explanation,
        trigger_price=round(float(recent_high * 1.003), 2),
        support_price=round(float(support_level), 2),
        reward_multiple=2.0,
    )


def _segment_range(segment: pd.DataFrame) -> float:
    high = segment["High"].max()
    low = segment["Low"].min()
    return (high - low) / max(low, 1.0)


def _score_band(
    value: float,
    thresholds: tuple[tuple[float, float], ...],
    invert: bool = False,
) -> float:
    for threshold, score in thresholds:
        if invert:
            if value <= threshold:
                return score
        elif value >= threshold:
            return score
    return 0.0
