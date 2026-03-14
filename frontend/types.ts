export type PatternType =
  | "consolidation_breakout"
  | "ema_pullback"
  | "relative_strength_breakout"
  | "support_bounce"
  | "volatility_contraction";

export type MarketCapBucket = "large_cap" | "mid_cap" | "small_cap";

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

export interface BacktestStats {
  pattern: PatternType;
  total_trades: number;
  win_rate: number;
  average_return_pct: number;
  max_drawdown_pct: number;
  profit_factor: number;
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
  expected_profit_amount: number;
  expected_return_pct: number;
  confidence_reason: string;
  indicators: IndicatorSnapshot;
  relative_strength: RelativeStrengthSnapshot;
  backtest: BacktestStats;
}

export interface ScanResponse {
  generated_at: string;
  universe_size: number;
  scanned_symbols: number;
  results: TradeSetup[];
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
