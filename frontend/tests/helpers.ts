import { Candle } from "../types";
import { JournalTrade } from "../lib/store";

export function trade(over: Partial<JournalTrade> = {}): JournalTrade {
  return {
    id: "t1", symbol: "TEST", company_name: "Test", sector: "IT", pattern: "breakout", status: "open",
    entry_date: "2026-01-01", planned_entry: 100, entry_price: 100, qty: 100, stop_loss: 95,
    target_price: 115, backtest_win_rate: null, probability_score: null, initial_stop: 95, ...over,
  };
}

/** Daily candles from closes, dated from entry day; high/low ±0.5. */
export function candles(closes: number[], highs?: number[]): Candle[] {
  return closes.map((c, i) => {
    const d = new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10);
    return { date: d, open: c, high: highs?.[i] ?? c + 0.5, low: c - 0.5, close: c, volume: 1000 };
  });
}
