from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class PatternType(str, Enum):
    CONSOLIDATION_BREAKOUT = "consolidation_breakout"
    EMA_PULLBACK = "ema_pullback"
    RELATIVE_STRENGTH_BREAKOUT = "relative_strength_breakout"
    SUPPORT_BOUNCE = "support_bounce"
    VOLATILITY_CONTRACTION = "volatility_contraction"
    GAP_MOMENTUM = "gap_momentum"


class MarketCapBucket(str, Enum):
    LARGE = "large_cap"
    MID = "mid_cap"
    SMALL = "small_cap"


class ScanUniverse(str, Enum):
    NIFTY500 = "nifty500"
    NIFTY_SMALLCAP_250 = "nifty_smallcap_250"
    MID_SMALL_2000_PLUS = "mid_small_2000_plus"


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


class LiquiditySnapshot(BaseModel):
    average_traded_value_20d_cr: float
    average_traded_value_50d_cr: float
    score: float
    passes_filter: bool


class AccumulationSnapshot(BaseModel):
    score: float
    up_volume_ratio_10d: float
    atr_contraction_ratio: float
    closes_near_high_10d: int
    average_delivery_pct_10d: float | None
    latest_delivery_pct: float | None
    rising_delivery_days_10d: int
    source: str


class SectorStrengthSnapshot(BaseModel):
    sector: str
    score: float
    rank: int
    sector_count: int
    average_relative_strength_score: float
    average_excess_return_50d_pct: float
    average_excess_return_120d_pct: float


class EventRiskSnapshot(BaseModel):
    earnings_date: date | None
    days_to_earnings: int | None
    risk_level: str
    ranking_penalty: float
    ex_dividend_date: date | None = None
    days_to_ex_dividend: int | None = None
    blackout: bool = False


class PriceLevelSnapshot(BaseModel):
    high_52w: float
    low_52w: float
    distance_from_52w_high_pct: float
    near_52w_high: bool
    price_discovery: bool


class WeeklyTrendSnapshot(BaseModel):
    weekly_close: float
    weekly_ema20: float
    weekly_rsi14: float
    above_weekly_ema20: bool
    weekly_rsi_above_50: bool
    weekly_volume_rising: bool
    checks_passed: int
    aligned: bool


class PeerRankSnapshot(BaseModel):
    sector: str
    rank: int
    peer_count: int
    percentile: float
    sector_leader: bool
    sector_laggard: bool
    top_peers: list[str] = Field(default_factory=list)


class FundamentalSnapshot(BaseModel):
    source: str
    revenue_growth_pct: float | None = None
    profit_margin_pct: float | None = None
    debt_to_equity: float | None = None
    return_on_equity_pct: float | None = None
    insider_holding_pct: float | None = None
    institutional_holding_pct: float | None = None
    quality_score: int = 0
    checks_available: int = 0
    passes: bool | None = None


class RsLineSnapshot(BaseModel):
    rs_line_new_high: bool
    leads_price: bool
    distance_from_rs_high_pct: float
    price_distance_from_high_pct: float


class SectorRotationSnapshot(BaseModel):
    sector: str
    rank: int
    sector_count: int
    stocks: int
    excess_return_1m_pct: float
    excess_return_3m_pct: float
    score: float
    leading: bool
    lagging: bool


class SmartMoneySnapshot(BaseModel):
    delivery_spike: bool = False
    delivery_ratio: float | None = None
    latest_delivery_pct: float | None = None
    breakout: bool = False
    bulk_deal_buys: int = 0
    bulk_deal_sells: int = 0
    bulk_deal_net_qty: float = 0.0
    bulk_deal_source: str = "unavailable"


class SectorRotationResponse(BaseModel):
    universe: ScanUniverse
    generated_at: datetime | None = None
    sectors: list[SectorRotationSnapshot] = Field(default_factory=list)


class PortfolioHoldingInput(BaseModel):
    symbol: str
    value: float = Field(gt=0)


class PortfolioRiskRequest(BaseModel):
    holdings: list[PortfolioHoldingInput] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)
    capital: float | None = None
    lookback_sessions: int = Field(default=120, ge=40, le=250)


class SymbolRisk(BaseModel):
    symbol: str
    beta: float | None
    volatility_pct: float | None
    max_correlation: float | None = None
    most_correlated_with: str | None = None
    high_correlation: bool = False


class CorrelationPair(BaseModel):
    a: str
    b: str
    correlation: float


class PortfolioRiskResponse(BaseModel):
    sessions: int
    benchmark_name: str
    portfolio_beta: float | None
    capital_weighted_beta: float | None
    average_pairwise_correlation: float | None
    holdings: list[SymbolRisk]
    candidates: list[SymbolRisk]
    high_correlation_pairs: list[CorrelationPair]
    correlation_threshold: float
    unavailable: list[str] = Field(default_factory=list)


class MarketRegimeSnapshot(BaseModel):
    regime: str
    score: float
    benchmark_name: str
    benchmark_close: float | None = None
    benchmark_above_ema50: bool | None = None
    benchmark_above_ema200: bool | None = None
    benchmark_ema50_above_ema200: bool | None = None
    benchmark_return_20d_pct: float | None = None
    vix: float | None = None
    breadth_above_ema50_pct: float | None = None
    breadth_sample_size: int = 0
    breadth_source: str = "unavailable"
    recommended_min_probability: float
    recommended_min_risk_reward: float
    position_size_multiplier: float
    notes: list[str] = Field(default_factory=list)
    generated_at: datetime


class BacktestStats(BaseModel):
    pattern: PatternType
    total_trades: int
    win_rate: float
    average_return_pct: float
    max_drawdown_pct: float
    profit_factor: float
    target_hit_rate: float
    average_holding_sessions: float
    average_target_sessions: float | None
    signals: int = 0
    fill_rate: float = 0.0
    average_r: float = 0.0
    cost_pct: float = 0.0


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
    estimated_target_sessions: int
    estimated_target_date: date
    confidence_reason: str
    indicators: IndicatorSnapshot
    relative_strength: RelativeStrengthSnapshot
    liquidity: LiquiditySnapshot
    accumulation: AccumulationSnapshot
    sector_strength: SectorStrengthSnapshot
    event_risk: EventRiskSnapshot
    backtest: BacktestStats
    price_levels: PriceLevelSnapshot | None = None
    weekly_trend: WeeklyTrendSnapshot | None = None
    peer_rank: PeerRankSnapshot | None = None
    fundamentals: FundamentalSnapshot | None = None
    quality_flags: list[str] = Field(default_factory=list)
    rs_line: RsLineSnapshot | None = None
    sector_rotation: SectorRotationSnapshot | None = None
    smart_money: SmartMoneySnapshot | None = None
    signal_date: date | None = None
    historical_win_rate: float | None = None
    calibration_samples: int | None = None


class ScanRequest(BaseModel):
    universe: ScanUniverse = Field(default=ScanUniverse.NIFTY500)
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
    universe: ScanUniverse
    generated_at: datetime
    universe_size: int
    scanned_symbols: int
    results: list[TradeSetup]
    from_cache: bool = False
    refresh_started: bool = False
    scan_in_progress: bool = False


class ScanStatusResponse(BaseModel):
    universe: ScanUniverse | None
    scan_in_progress: bool
    latest_generated_at: datetime | None
    universe_size: int
    scanned_symbols: int
    latest_results_count: int
    data_rejected: int = 0
    data_rejected_examples: list[str] = Field(default_factory=list)


class StockDetailResponse(BaseModel):
    stock: StockSummary
    latest_signal: TradeSetup | None
    candles: list[Candle]


class StockSearchResult(BaseModel):
    symbol: str
    company_name: str
    sector: str
    market_cap_bucket: MarketCapBucket


class AnalysisFactor(BaseModel):
    name: str
    value: str
    status: str  # good | neutral | bad | na
    note: str = ""


class AnalysisCategory(BaseModel):
    name: str
    weight: float
    score: float | None
    factors: list[AnalysisFactor] = Field(default_factory=list)


class AnalysisSection(BaseModel):
    available: bool
    score: float | None
    grade: str | None
    label: str | None
    categories: list[AnalysisCategory] = Field(default_factory=list)
    note: str | None = None


class AnalysisSetup(BaseModel):
    pattern: PatternType
    explanation: str
    entry: float
    stop: float
    target: float
    risk_reward: float
    tradeable: bool


class AnalysisLevels(BaseModel):
    support_20d: float
    resistance_20d: float
    support_60d: float
    resistance_60d: float
    high_52w: float
    low_52w: float
    atr_pct: float


class StockAnalysisResponse(BaseModel):
    symbol: str
    company_name: str
    sector: str
    industry: str | None = None
    description: str | None = None
    in_scan_universe: bool
    price: float
    change_pct: float
    as_of: date
    market_cap_cr: float | None = None
    overall_score: float
    overall_grade: str
    overall_label: str
    summary: str
    technical: AnalysisSection
    fundamental: AnalysisSection
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    setup: AnalysisSetup | None = None
    levels: AnalysisLevels
    regime: str | None = None
    candles: list[Candle] = Field(default_factory=list)
    data_notes: list[str] = Field(default_factory=list)
