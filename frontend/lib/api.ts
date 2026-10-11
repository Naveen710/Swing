import {
  BacktestRunState,
  AlgoBacktest,
  AlgoLiveResponse,
  MarketRegimeSnapshot,
  MultibaggerBacktest,
  MultibaggerLedger,
  MultibaggerResult,
  MultibaggerStatus,
  MultibaggerStockResult,
  PortfolioRiskResponse,
  SectorRotationResponse,
  QuantScreenResponse,
  StockAnalysis,
  StockSearchResult,
  ValuationResponse,
  VcpMonitor,
  VcpScanResponse,
  PerformanceSummary,
  PortfolioBacktestResult,
  ScanUniverse,
  ScanResponse,
  ScanStatusResponse,
  StockDetailResponse,
  StockSummary,
  TradeSetup
} from "../types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api";

interface ScanPayload {
  universe: ScanUniverse;
  max_results: number;
  min_probability: number;
  min_risk_reward: number;
  investment_amount: number;
  sectors?: string[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    cache: "no-store"
  });

  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }

  return (await response.json()) as T;
}

export function getStocks(universe: ScanUniverse): Promise<StockSummary[]> {
  return request<StockSummary[]>(`/stocks?universe=${encodeURIComponent(universe)}`);
}

export function getLatestSignals(universe?: ScanUniverse): Promise<TradeSetup[]> {
  const query = universe ? `?universe=${encodeURIComponent(universe)}` : "";
  return request<TradeSetup[]>(`/signals${query}`);
}

export function runScan(payload: ScanPayload): Promise<ScanResponse> {
  return request<ScanResponse>("/scan", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function getScanStatus(): Promise<ScanStatusResponse> {
  return request<ScanStatusResponse>("/scan/status");
}

export function getStockDetail(symbol: string): Promise<StockDetailResponse> {
  return request<StockDetailResponse>(`/stock/${encodeURIComponent(symbol)}`);
}

export function getMarketRegime(refresh = false): Promise<MarketRegimeSnapshot> {
  return request<MarketRegimeSnapshot>(`/regime${refresh ? "?refresh=true" : ""}`);
}

export function getPerformance(): Promise<PerformanceSummary> {
  return request<PerformanceSummary>("/performance");
}

export function evaluateLedger(): Promise<{ checked?: number; updated?: number; skipped?: string }> {
  return request("/performance/evaluate", { method: "POST" });
}

export function startPortfolioBacktest(universe: ScanUniverse, years: number): Promise<{ started: boolean; state: BacktestRunState }> {
  return request(`/backtest/portfolio?universe=${encodeURIComponent(universe)}&years=${years}`, { method: "POST" });
}

export function getPortfolioBacktestStatus(): Promise<BacktestRunState> {
  return request<BacktestRunState>("/backtest/portfolio/status");
}

export async function getPortfolioBacktest(universe: ScanUniverse): Promise<PortfolioBacktestResult | null> {
  try {
    return await request<PortfolioBacktestResult>(`/backtest/portfolio?universe=${encodeURIComponent(universe)}`);
  } catch {
    return null; // 404 when no run exists yet
  }
}

export function getSectorRotation(universe: ScanUniverse): Promise<SectorRotationResponse> {
  return request<SectorRotationResponse>(`/sectors?universe=${universe}`);
}

export function getPortfolioRisk(body: {
  holdings: { symbol: string; value: number }[];
  candidates?: string[];
  capital?: number;
}): Promise<PortfolioRiskResponse> {
  return request<PortfolioRiskResponse>("/portfolio/risk", {
    method: "POST",
    body: JSON.stringify({ candidates: [], ...body }),
  });
}

export function searchStocks(query: string, limit = 8): Promise<StockSearchResult[]> {
  return request<StockSearchResult[]>(`/search?q=${encodeURIComponent(query)}&limit=${limit}`);
}

export function analyzeStock(symbol: string): Promise<StockAnalysis> {
  return request<StockAnalysis>(`/analyze/${encodeURIComponent(symbol)}`);
}

export function getQuantScreen(universe: ScanUniverse, top: number, refresh = false): Promise<QuantScreenResponse> {
  return request<QuantScreenResponse>(`/quant?universe=${universe}&top=${top}${refresh ? "&refresh=true" : ""}`);
}

export function getValuation(symbol: string, overrides: Record<string, string | number | null | undefined> = {}): Promise<ValuationResponse> {
  const params = new URLSearchParams();
  Object.entries(overrides).forEach(([k, v]) => { if (v !== null && v !== undefined && v !== "") params.set(k, String(v)); });
  const qs = params.toString();
  return request<ValuationResponse>(`/valuation/${encodeURIComponent(symbol)}${qs ? `?${qs}` : ""}`);
}

export function getAlgo(universe: ScanUniverse, exclude: string[], refresh = false): Promise<AlgoLiveResponse> {
  const ex = exclude.length ? `&exclude=${encodeURIComponent(exclude.join(","))}` : "";
  return request<AlgoLiveResponse>(`/algo?universe=${universe}${ex}${refresh ? "&refresh=true" : ""}`);
}

export function startAlgoBacktest(universe: ScanUniverse, start: string, confirmations: number, full: boolean) {
  return request<{ started: boolean; state: { running: boolean; stage: string } }>(
    `/algo/backtest?universe=${universe}&start=${start}&confirmations=${confirmations}&full=${full}`, { method: "POST" });
}

export function getAlgoBacktestStatus() {
  return request<{ running: boolean; stage: string; progress?: number; error?: string }>("/algo/backtest/status");
}

export function getAlgoBacktest(universe: ScanUniverse): Promise<AlgoBacktest> {
  return request<AlgoBacktest>(`/algo/backtest?universe=${universe}`);
}

export function getVcpScan(universe: ScanUniverse, refresh = false): Promise<VcpScanResponse> {
  return request<VcpScanResponse>(`/vcp?universe=${universe}${refresh ? "&refresh=true" : ""}`);
}

export function getVcpMonitor(positions: { symbol: string; entry_date: string; entry_price: number }[]): Promise<VcpMonitor[]> {
  return request<VcpMonitor[]>("/vcp/monitor", { method: "POST", body: JSON.stringify({ positions }) });
}

/* ── Multibagger framework ─────────────────────────────────────────── */

export function startMultibagger(universe: ScanUniverse, exclude: string[], refreshFundamentals: boolean) {
  const q = new URLSearchParams({ universe, exclude: exclude.join(","), refresh_fundamentals: String(refreshFundamentals) });
  return request<{ started: boolean; state: MultibaggerStatus }>(`/multibagger/run?${q}`, { method: "POST" });
}

export function getMultibaggerStatus() {
  return request<MultibaggerStatus>("/multibagger/status");
}

/** Latest run for the universe, or null when none has been run yet (404). */
export async function getMultibagger(universe: ScanUniverse): Promise<MultibaggerResult | null> {
  try {
    return await request<MultibaggerResult>(`/multibagger?universe=${encodeURIComponent(universe)}`);
  } catch (e) {
    if (e instanceof Error && e.message.endsWith("404")) return null;
    throw e;
  }
}

export function getMultibaggerStock(symbol: string) {
  return request<MultibaggerStockResult>(`/multibagger/stock/${encodeURIComponent(symbol.trim().toUpperCase())}`);
}

export function startMultibaggerBacktest(universe: ScanUniverse, start = "2015-01-01") {
  const q = new URLSearchParams({ universe, start });
  return request<{ started: boolean; state: MultibaggerStatus }>(`/multibagger/backtest?${q}`, { method: "POST" });
}

export async function getMultibaggerBacktest(universe: ScanUniverse): Promise<MultibaggerBacktest | null> {
  try {
    return await request<MultibaggerBacktest>(`/multibagger/backtest?universe=${encodeURIComponent(universe)}`);
  } catch (e) {
    if (e instanceof Error && e.message.endsWith("404")) return null;
    throw e;
  }
}

export function getMultibaggerLedger(universe: ScanUniverse) {
  return request<MultibaggerLedger>(`/multibagger/ledger?universe=${encodeURIComponent(universe)}`);
}
