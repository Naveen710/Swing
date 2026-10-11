import { Candle } from "../types";
import { JournalTrade, initialQty } from "./store";

/* Exit engine. Rules, checked in priority order on the latest daily close:
   1. Stop hit                         → exit
   2. Target reached                   → exit
   3. Price has reached +2R, no partial → book half
   4. In profit by ≥1R (or partial booked):
        close below the 20 EMA         → exit the rest
        otherwise                      → trail the stop up under the 20 EMA
   5. 10+ sessions and never reached +1R → time stop, exit
   6. Otherwise                        → hold                                    */

export const PARTIAL_AT_R = 2;
export const TIME_STOP_SESSIONS = 10;

export type ExitAction = "exit" | "book_partial" | "trail" | "hold";

export interface ExitAdvice {
  action: ExitAction;
  rule: "stop" | "target" | "partial_2r" | "ema20_exit" | "ema20_trail" | "time_stop" | null;
  message: string;
  lastClose: number;
  ema20: number | null;
  rNow: number;
  maxR: number;
  sessionsHeld: number;
  suggestedStop: number | null;
  partialQty: number | null;
  partialPrice: number | null;
}

function ema(values: number[], span: number): number[] {
  const k = 2 / (span + 1);
  const out: number[] = [];
  values.forEach((v, i) => out.push(i === 0 ? v : v * k + out[i - 1] * (1 - k)));
  return out;
}

export function computeExitAdvice(trade: JournalTrade, candles: Candle[]): ExitAdvice | null {
  if (trade.exit_profile === "curated") return curatedExitAdvice(trade, candles);
  if (!candles.length) return null;
  const closes = candles.map((c) => c.close);
  const ema20 = closes.length >= 20 ? ema(closes, 20)[closes.length - 1] : null;
  const since = candles.filter((c) => c.date.slice(0, 10) >= trade.entry_date);
  const last = candles[candles.length - 1];
  const initialStop = trade.initial_stop ?? trade.stop_loss;
  const risk = trade.entry_price - initialStop;
  if (risk <= 0) return null;

  const rNow = (last.close - trade.entry_price) / risk;
  const maxHigh = since.length ? Math.max(...since.map((c) => c.high)) : last.high;
  const maxR = (maxHigh - trade.entry_price) / risk;
  const sessionsHeld = since.length;
  const partialBooked = (trade.partials ?? []).length > 0;
  const base = { lastClose: last.close, ema20, rNow, maxR, sessionsHeld, suggestedStop: null, partialQty: null, partialPrice: null };
  const r1 = (v: number) => Math.round(v * 100) / 100;

  if (last.close <= trade.stop_loss) {
    return { ...base, action: "exit", rule: "stop", message: `Closed at or below your stop (₹${trade.stop_loss}). Exit — don't hope.` };
  }
  if (last.high >= trade.target_price) {
    return { ...base, action: "exit", rule: "target", message: `Target ₹${trade.target_price} reached. Book the profit.` };
  }
  const twoR = trade.entry_price + PARTIAL_AT_R * risk;
  if (!partialBooked && maxHigh >= twoR && trade.qty >= 2) {
    const qty = Math.floor(initialQty(trade) / 2);
    return {
      ...base, action: "book_partial", rule: "partial_2r", partialQty: Math.min(qty, trade.qty - 1), partialPrice: r1(twoR),
      message: `Reached +${PARTIAL_AT_R}R. Book half (${Math.min(qty, trade.qty - 1)} shares) near ₹${r1(twoR)} and let the rest run.`,
    };
  }
  if ((partialBooked || rNow >= 1) && ema20 !== null) {
    if (last.close < ema20) {
      return { ...base, action: "exit", rule: "ema20_exit", message: `Closed below the 20 EMA (₹${r1(ema20)}) while in profit. Exit the remaining shares.` };
    }
    const trail = r1(Math.max(trade.stop_loss, ema20 * 0.995));
    if (trail > trade.stop_loss) {
      return { ...base, action: "trail", rule: "ema20_trail", suggestedStop: trail, message: `In profit. Raise your stop to ₹${trail}, just under the 20 EMA.` };
    }
  }
  if (sessionsHeld >= TIME_STOP_SESSIONS && maxR < 1) {
    return { ...base, action: "exit", rule: "time_stop", message: `${sessionsHeld} sessions without reaching +1R. Time stop — free the capital for a better setup.` };
  }
  return { ...base, action: "hold", rule: null, message: `Hold. ${rNow >= 0 ? "+" : ""}${rNow.toFixed(2)}R now, best +${Math.max(0, maxR).toFixed(2)}R.` };
}

/* Algo (5-layer) exits: stop hit → exit; +1.5R → sell half and move stop to breakeven;
   after that, close below the 10 EMA → exit the rest; 10 sessions without +1.5R → time stop. */
function curatedExitAdvice(trade: JournalTrade, candles: Candle[]): ExitAdvice | null {
  if (!candles.length) return null;
  const closes = candles.map((c) => c.close);
  const ema10 = closes.length >= 10 ? ema(closes, 10)[closes.length - 1] : null;
  const since = candles.filter((c) => c.date.slice(0, 10) >= trade.entry_date);
  const last = candles[candles.length - 1];
  const initialStop = trade.initial_stop ?? trade.stop_loss;
  const risk = trade.entry_price - initialStop;
  if (risk <= 0) return null;
  const rNow = (last.close - trade.entry_price) / risk;
  const maxHigh = since.length ? Math.max(...since.map((c) => c.high)) : last.high;
  const maxR = (maxHigh - trade.entry_price) / risk;
  const sessionsHeld = since.length;
  const partialBooked = (trade.partials ?? []).length > 0;
  const base = { lastClose: last.close, ema20: ema10, rNow, maxR, sessionsHeld, suggestedStop: null, partialQty: null, partialPrice: null };
  const r2 = (v: number) => Math.round(v * 100) / 100;
  const target = trade.entry_price + 1.5 * risk;

  if (last.close <= trade.stop_loss) {
    return { ...base, action: "exit", rule: "stop", message: `Closed at or below your stop (₹${trade.stop_loss}). Exit.` };
  }
  if (!partialBooked && maxHigh >= target && trade.qty >= 2) {
    const qty = Math.min(Math.floor(initialQty(trade) / 2), trade.qty - 1);
    return { ...base, action: "book_partial", rule: "partial_2r", partialQty: qty, partialPrice: r2(target),
      message: `Reached +1.5R. Sell half (${qty} shares) near ₹${r2(target)}, then move the stop to breakeven (₹${trade.entry_price}).` };
  }
  if (partialBooked) {
    if (trade.stop_loss < trade.entry_price) {
      return { ...base, action: "trail", rule: "ema20_trail", suggestedStop: trade.entry_price,
        message: `Half booked — move the stop to breakeven (₹${trade.entry_price}).` };
    }
    if (ema10 !== null && last.close < ema10) {
      return { ...base, action: "exit", rule: "ema20_exit", message: `Closed below the 10 EMA (₹${r2(ema10)}). Exit the remaining shares.` };
    }
    return { ...base, action: "hold", rule: null, message: `Trailing under the 10 EMA (₹${ema10 !== null ? r2(ema10) : "—"}). Exit on a close below it.` };
  }
  if (sessionsHeld >= 10) {
    return { ...base, action: "exit", rule: "time_stop", message: `${sessionsHeld} sessions without reaching +1.5R. Time stop — exit.` };
  }
  return { ...base, action: "hold", rule: null, message: `Hold. ${rNow >= 0 ? "+" : ""}${rNow.toFixed(2)}R now; half comes off at ₹${r2(target)} (+1.5R).` };
}
