export type PatternType =
  | "consolidation_breakout"
  | "ema_pullback"
  | "relative_strength_breakout"
  | "support_bounce"
  | "volatility_contraction";

export type MarketCapBucket = "large_cap" | "mid_cap" | "small_cap";
export type ScanUniverse =
  | "nifty500"
  | "nifty_smallcap_250"
  | "mid_small_2000_plus";

export interface StockSummary {
  symbol: string;
  company_name: string;
  sector: string;
  market_cap_bucket: MarketCapBucket;
}

export interface IndicatorSnapshot {
  ema20: number;
  ema50: number;
  ema200: number;
  rsi14: number;
  atr14: number;
  volume_ratio: number;
  volume_expansion_ratio_3d: number;
  roc_20d_pct: number;
  rsi_slope_5d: number;
  price_vs_ema20_pct: number;
}

export interface RelativeStrengthSnapshot {
  benchmark_symbol: string;
  benchmark_name: string;
  score: number;
  stock_return_20d_pct: number;
  benchmark_return_20d_pct: number;
  excess_return_20d_pct: number;
  stock_return_50d_pct: number;
  benchmark_return_50d_pct: number;
  excess_return_50d_pct: number;
  stock_return_120d_pct: number;
  benchmark_return_120d_pct: number;
  excess_return_120d_pct: number;
}

export interface LiquiditySnapshot {
  average_traded_value_20d_cr: number;
  average_traded_value_50d_cr: number;
  score: number;
  passes_filter: boolean;
}

export interface AccumulationSnapshot {
  score: number;
  up_volume_ratio_10d: number;
  volume_expansion_ratio_3d: number;
  atr_contraction_ratio: number;
  closes_near_high_10d: number;
  average_delivery_pct_10d: number | null;
  latest_delivery_pct: number | null;
  rising_delivery_days_10d: number;
  source: string;
}

export interface SectorStrengthSnapshot {
  sector: string;
  score: number;
  rank: number;
  sector_count: number;
  average_relative_strength_score: number;
  average_excess_return_50d_pct: number;
  average_excess_return_120d_pct: number;
}

export interface EventRiskSnapshot {
  earnings_date: string | null;
  days_to_earnings: number | null;
  event_date: string | null;
  days_to_event: number | null;
  event_type: string | null;
  risk_level: string;
  ranking_penalty: number;
  blocked: boolean;
}

export interface BacktestStats {
  pattern: PatternType;
  total_trades: number;
  win_rate: number;
  average_return_pct: number;
  max_drawdown_pct: number;
  profit_factor: number;
  target_hit_rate: number;
  average_holding_sessions: number;
  average_target_sessions: number | null;
}

export interface TradeSetup {
  symbol: string;
  company_name: string;
  sector: string;
  market_cap_bucket: MarketCapBucket;
  pattern: PatternType;
  current_price: number;
  entry_price: number;
  stop_loss: number;
  target_price: number;
  risk_reward_ratio: number;
  probability_score: number;
  ranking_score: number;
  breakout_quality_score: number;
  expected_profit_amount: number;
  expected_return_pct: number;
  estimated_target_sessions: number;
  estimated_target_date: string;
  confidence_reason: string;
  indicators: IndicatorSnapshot;
  relative_strength: RelativeStrengthSnapshot;
  liquidity: LiquiditySnapshot;
  accumulation: AccumulationSnapshot;
  sector_strength: SectorStrengthSnapshot;
  event_risk: EventRiskSnapshot;
  backtest: BacktestStats;
}

export interface ScanResponse {
  universe: ScanUniverse;
  generated_at: string;
  universe_size: number;
  scanned_symbols: number;
  results: TradeSetup[];
  from_cache: boolean;
  refresh_started: boolean;
  scan_in_progress: boolean;
}

export interface ScanStatusResponse {
  universe: ScanUniverse | null;
  scan_in_progress: boolean;
  latest_generated_at: string | null;
  universe_size: number;
  scanned_symbols: number;
  latest_results_count: number;
}

export interface Candle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface StockDetailResponse {
  stock: StockSummary;
  latest_signal: TradeSetup | null;
  candles: Candle[];
}
