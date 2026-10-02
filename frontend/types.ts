export type PatternType =
  | "consolidation_breakout"
  | "ema_pullback"
  | "relative_strength_breakout"
  | "support_bounce"
  | "volatility_contraction"
  | "gap_momentum";

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
  risk_level: string;
  ranking_penalty: number;
  ex_dividend_date?: string | null;
  days_to_ex_dividend?: number | null;
  blackout?: boolean;
}

export interface PriceLevelSnapshot {
  high_52w: number;
  low_52w: number;
  distance_from_52w_high_pct: number;
  near_52w_high: boolean;
  price_discovery: boolean;
}

export interface WeeklyTrendSnapshot {
  weekly_close: number;
  weekly_ema20: number;
  weekly_rsi14: number;
  above_weekly_ema20: boolean;
  weekly_rsi_above_50: boolean;
  weekly_volume_rising: boolean;
  checks_passed: number;
  aligned: boolean;
}

export interface PeerRankSnapshot {
  sector: string;
  rank: number;
  peer_count: number;
  percentile: number;
  sector_leader: boolean;
  sector_laggard: boolean;
  top_peers: string[];
}

export interface FundamentalSnapshot {
  source: string;
  revenue_growth_pct: number | null;
  profit_margin_pct: number | null;
  debt_to_equity: number | null;
  return_on_equity_pct: number | null;
  insider_holding_pct: number | null;
  institutional_holding_pct: number | null;
  quality_score: number;
  checks_available: number;
  passes: boolean | null;
}

export interface RsLineSnapshot {
  rs_line_new_high: boolean;
  leads_price: boolean;
  distance_from_rs_high_pct: number;
  price_distance_from_high_pct: number;
}

export interface SectorRotationSnapshot {
  sector: string;
  rank: number;
  sector_count: number;
  stocks: number;
  excess_return_1m_pct: number;
  excess_return_3m_pct: number;
  score: number;
  leading: boolean;
  lagging: boolean;
}

export interface SmartMoneySnapshot {
  delivery_spike: boolean;
  delivery_ratio: number | null;
  latest_delivery_pct: number | null;
  breakout: boolean;
  bulk_deal_buys: number;
  bulk_deal_sells: number;
  bulk_deal_net_qty: number;
  bulk_deal_source: string;
}

export interface SectorRotationResponse {
  universe: string;
  generated_at: string | null;
  sectors: SectorRotationSnapshot[];
}

export interface SymbolRisk {
  symbol: string;
  beta: number | null;
  volatility_pct: number | null;
  max_correlation: number | null;
  most_correlated_with: string | null;
  high_correlation: boolean;
}

export interface PortfolioRiskResponse {
  sessions: number;
  benchmark_name: string;
  portfolio_beta: number | null;
  capital_weighted_beta: number | null;
  average_pairwise_correlation: number | null;
  holdings: SymbolRisk[];
  candidates: SymbolRisk[];
  high_correlation_pairs: { a: string; b: string; correlation: number }[];
  correlation_threshold: number;
  unavailable: string[];
}

export type RegimeName = "bull" | "neutral" | "bear" | "unknown";

export interface MarketRegimeSnapshot {
  regime: RegimeName;
  score: number;
  benchmark_name: string;
  benchmark_close: number | null;
  benchmark_above_ema50: boolean | null;
  benchmark_above_ema200: boolean | null;
  benchmark_ema50_above_ema200: boolean | null;
  benchmark_return_20d_pct: number | null;
  vix: number | null;
  breadth_above_ema50_pct: number | null;
  breadth_sample_size: number;
  breadth_source: string;
  recommended_min_probability: number;
  recommended_min_risk_reward: number;
  position_size_multiplier: number;
  notes: string[];
  generated_at: string;
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
  signals?: number;
  fill_rate?: number;
  average_r?: number;
  cost_pct?: number;
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
  price_levels?: PriceLevelSnapshot | null;
  weekly_trend?: WeeklyTrendSnapshot | null;
  peer_rank?: PeerRankSnapshot | null;
  fundamentals?: FundamentalSnapshot | null;
  quality_flags?: string[];
  rs_line?: RsLineSnapshot | null;
  sector_rotation?: SectorRotationSnapshot | null;
  smart_money?: SmartMoneySnapshot | null;
  signal_date?: string | null;
  historical_win_rate?: number | null;
  calibration_samples?: number | null;
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
  data_rejected?: number;
  data_rejected_examples?: string[];
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

export interface OutcomeStats {
  win_rate: number | null;
  average_r: number | null;
  average_return_pct: number | null;
  profit_factor: number | null;
}

export interface LedgerGroup extends OutcomeStats {
  key: string;
  signals: number;
  trades: number;
  fill_rate: number | null;
}

export interface LedgerSignal {
  id: number;
  signal_date: string;
  symbol: string;
  company_name: string | null;
  sector: string | null;
  universe: string | null;
  pattern: string;
  regime: string | null;
  entry: number;
  stop: number;
  target: number;
  probability: number | null;
  ranking_score: number | null;
  risk_reward: number | null;
  status: "pending" | "open" | "expired" | "target" | "stop" | "time";
  fill_date: string | null;
  fill_price: number | null;
  exit_date: string | null;
  exit_price: number | null;
  sessions_held: number | null;
  return_pct_net: number | null;
  r_multiple_net: number | null;
}

export interface CalibrationModel {
  samples: number;
  base_win_rate: number;
  spread?: number;
  low_score_win_rate?: number;
  high_score_win_rate?: number;
  z_score?: number;
  significant?: boolean;
  bands: { band: string; score_from: number; score_to: number; trades: number; win_rate: number }[];
  source: string;
  fitted_at: string;
}

export interface PerformanceSummary extends OutcomeStats {
  total_signals: number;
  pending: number;
  open: number;
  expired: number;
  closed: number;
  fill_rate: number | null;
  first_signal: string | null;
  last_signal: string | null;
  by_pattern: LedgerGroup[];
  by_regime: LedgerGroup[];
  by_probability_band: (OutcomeStats & { band: string; predicted: number; trades: number })[];
  recent: LedgerSignal[];
  persistent_storage: boolean;
  calibration: CalibrationModel | null;
}

export interface PortfolioBacktestMetrics {
  start: string;
  end: string;
  starting_capital: number;
  final_equity: number;
  total_return_pct: number;
  cagr_pct: number;
  benchmark_return_pct: number;
  max_drawdown_pct: number;
  trades: number;
  exposure_pct: number;
  open_positions_at_end: number;
  avg_monthly_return_pct: number | null;
  positive_months_pct?: number | null;
  win_rate: number | null;
  average_r: number | null;
  profit_factor: number | null;
  average_sessions: number | null;
}

export interface PortfolioBacktestResult {
  metrics: PortfolioBacktestMetrics;
  monthly_returns: { month: string; return_pct: number }[];
  by_pattern: { key: string; trades: number; win_rate: number; average_r: number; pnl: number }[];
  by_regime: { key: string; trades: number; win_rate: number; average_r: number; pnl: number }[];
  equity_curve: { date: string; equity: number; benchmark: number }[];
  trades: {
    symbol: string; sector: string; pattern: string; regime: string; entry_date: string; exit_date: string;
    entry: number; exit: number; qty: number; reason: string; pnl: number; return_pct: number; r: number; sessions: number;
  }[];
  assumptions: string[];
  signal_samples: number;
  symbols_tested: number;
  symbols_skipped: number;
  skipped_examples: string[];
  universe: ScanUniverse;
  years: number;
  generated_at: string;
}

export interface BacktestRunState {
  running: boolean;
  universe?: string;
  years?: number;
  stage?: string;
  progress?: number;
  started_at?: string;
  finished_at?: string;
  error?: string | null;
}

export interface StockSearchResult {
  symbol: string;
  company_name: string;
  sector: string;
  market_cap_bucket: MarketCapBucket;
}

export interface AnalysisFactor {
  name: string;
  value: string;
  status: "good" | "neutral" | "bad" | "na";
  note: string;
}

export interface AnalysisCategory {
  name: string;
  weight: number;
  score: number | null;
  factors: AnalysisFactor[];
}

export interface AnalysisSection {
  available: boolean;
  score: number | null;
  grade: string | null;
  label: string | null;
  categories: AnalysisCategory[];
  note: string | null;
}

export interface StockAnalysis {
  symbol: string;
  company_name: string;
  sector: string;
  industry: string | null;
  description: string | null;
  in_scan_universe: boolean;
  price: number;
  change_pct: number;
  as_of: string;
  market_cap_cr: number | null;
  overall_score: number;
  overall_grade: string;
  overall_label: string;
  summary: string;
  technical: AnalysisSection;
  fundamental: AnalysisSection;
  strengths: string[];
  concerns: string[];
  setup: {
    pattern: PatternType;
    explanation: string;
    entry: number;
    stop: number;
    target: number;
    risk_reward: number;
    tradeable: boolean;
  } | null;
  levels: {
    support_20d: number;
    resistance_20d: number;
    support_60d: number;
    resistance_60d: number;
    high_52w: number;
    low_52w: number;
    atr_pct: number;
  };
  regime: string | null;
  candles: Candle[];
  data_notes: string[];
}

export interface QuantStats {
  total_return_pct: number;
  cagr_pct: number | null;
  volatility_pct: number;
  sharpe: number | null;
  max_drawdown_pct: number;
}

export interface QuantPick {
  rank: number;
  symbol: string;
  company_name: string;
  sector: string;
  price: number;
  score: number;
  percentile: number;
  weight_pct: number;
  volatility_pct: number;
  factors: Record<string, { z: number | null; raw: number | null }>;
}

export interface QuantScreenResponse {
  available: boolean;
  reason?: string;
  universe: string;
  as_of: string;
  eligible: number;
  loaded: number;
  top_n: number;
  factor_weights: Record<string, number>;
  factor_labels: Record<string, string>;
  rules: string[];
  picks: QuantPick[];
  validation: {
    available: boolean;
    reason?: string;
    start?: string;
    end?: string;
    rebalances?: number;
    strategy?: QuantStats;
    universe?: QuantStats;
    benchmark?: QuantStats | null;
    hit_rate?: number;
    ic_mean?: number;
    ic_t_stat?: number;
    excess_t_stat?: number;
    significant?: boolean;
    curve?: { date: string; strategy: number; universe: number; benchmark: number }[];
    cost_pct_round_trip?: number;
  };
  generated_at: string;
}

export interface ValuationAssumptions {
  method: "fcf" | "earnings";
  base_cash_flow: number;
  growth_pct: number;
  terminal_growth_pct: number;
  discount_rate_pct: number;
  margin_of_safety_pct: number;
}

export interface ValuationResult {
  available: boolean;
  reason?: string;
  assumptions: ValuationAssumptions;
  defaults: ValuationAssumptions;
  historical: { historical_growth_pct: number | null; revenue_cagr_pct: number | null; profit_cagr_pct: number | null };
  intrinsic_value: number;
  buy_below: number;
  verdict: "Undervalued" | "Fairly valued" | "Overvalued";
  upside_pct: number;
  fair_band_pct: number;
  dcf: {
    rows: { year: number; growth_pct: number; cash_flow: number; discount_factor: number; present_value: number }[];
    pv_cash_flows: number; terminal_value: number; pv_terminal: number; enterprise_value: number;
    cash: number; debt: number; equity_value: number; shares: number; terminal_share_pct: number | null;
  };
  scenarios: { bear: number; base: number; bull: number };
  sensitivity: { discount_rates_pct: number[]; terminal_growth_pct: number[]; values: (number | null)[][] };
  reverse_dcf: { implied_growth_pct: number | null; historical_growth_pct: number | null; assumed_growth_pct: number; interpretation: string };
  warnings: string[];
  default_notes: string[];
}

export interface ValuationResponse {
  symbol: string;
  company_name: string;
  sector: string | null;
  industry: string | null;
  source: string;
  price: number;
  market_cap_cr: number | null;
  is_financial: boolean;
  cash: number | null;
  debt: number | null;
  shares: number | null;
  history: { year: number; revenue: number | null; net_income: number | null; operating_cash_flow: number | null; capex: number | null; free_cash_flow: number | null }[];
  valuation: ValuationResult;
}
