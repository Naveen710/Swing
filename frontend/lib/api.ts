import {
  BacktestRunState,
  MarketRegimeSnapshot,
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
