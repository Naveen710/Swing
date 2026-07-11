import {
  ScanUniverse,
  ScanResponse,
  ScanStatusResponse,
  StockDetailResponse,
  StockSummary,
  TradeSetup
} from "../types";

const API_BASE_URL =
  stripTrailingSlash(process.env.NEXT_PUBLIC_API_BASE_URL || "/api-proxy");

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

function stripTrailingSlash(value: string) {
  return value.replace(/\/+$/, "");
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
