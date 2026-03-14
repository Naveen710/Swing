from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class PatternType(str, Enum):
    CONSOLIDATION_BREAKOUT = "consolidation_breakout"
    EMA_PULLBACK = "ema_pullback"
    RELATIVE_STRENGTH_BREAKOUT = "relative_strength_breakout"
    SUPPORT_BOUNCE = "support_bounce"
    VOLATILITY_CONTRACTION = "volatility_contraction"


class MarketCapBucket(str, Enum):
    LARGE = "large_cap"
    MID = "mid_cap"
    SMALL = "small_cap"


class StockSummary(BaseModel):
    symbol: str
    company_name: str
    sector: str
    market_cap_bucket: MarketCapBucket


class Candle(BaseModel):
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class IndicatorSnapshot(BaseModel):
    ema20: float
    ema50: float
    ema200: float
    rsi14: float
    atr14: float
    volume_ratio: float
    price_vs_ema20_pct: float


class RelativeStrengthSnapshot(BaseModel):
    benchmark_symbol: str
    benchmark_name: str
    score: float
    stock_return_20d_pct: float
    benchmark_return_20d_pct: float
    excess_return_20d_pct: float
    stock_return_50d_pct: float
    benchmark_return_50d_pct: float
    excess_return_50d_pct: float
    stock_return_120d_pct: float
    benchmark_return_120d_pct: float
    excess_return_120d_pct: float


class BacktestStats(BaseModel):
    pattern: PatternType
    total_trades: int
    win_rate: float
    average_return_pct: float
    max_drawdown_pct: float
    profit_factor: float


class TradeSetup(BaseModel):
    symbol: str
    company_name: str
    sector: str
    market_cap_bucket: MarketCapBucket
    pattern: PatternType
    current_price: float
    entry_price: float
    stop_loss: float
    target_price: float
    risk_reward_ratio: float
    probability_score: float
    ranking_score: float
    expected_profit_amount: float
    expected_return_pct: float
    confidence_reason: str
    indicators: IndicatorSnapshot
    relative_strength: RelativeStrengthSnapshot
    backtest: BacktestStats


class ScanRequest(BaseModel):
    symbols: list[str] | None = Field(
        default=None,
        description="Optional subset of NSE symbols to scan.",
    )
    max_results: int = Field(default=20, ge=1, le=100)
    min_probability: float = Field(default=0.55, ge=0.0, le=1.0)
    min_risk_reward: float = Field(default=1.8, ge=0.5, le=10.0)
    lookback_days: int = Field(default=300, ge=120, le=700)
    investment_amount: int = Field(default=100000, ge=10000, le=10000000)
    sectors: list[str] | None = Field(default=None)
    market_caps: list[MarketCapBucket] | None = Field(default=None)


class ScanResponse(BaseModel):
    generated_at: datetime
    universe_size: int
    scanned_symbols: int
    results: list[TradeSetup]


class StockDetailResponse(BaseModel):
    stock: StockSummary
    latest_signal: TradeSetup | None
    candles: list[Candle]
