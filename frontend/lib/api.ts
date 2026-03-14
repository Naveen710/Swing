import {
  ScanResponse,
  ScanStatusResponse,
  StockDetailResponse,
  StockSummary,
  TradeSetup
} from "../types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api";

interface ScanPayload {
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

export function getStocks(): Promise<StockSummary[]> {
  return request<StockSummary[]>("/stocks");
}

export function getLatestSignals(): Promise<TradeSetup[]> {
  return request<TradeSetup[]>("/signals");
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
