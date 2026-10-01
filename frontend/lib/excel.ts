"use client";

import {
  JournalTrade,
  WatchItem,
  closedMetrics,
  fmtPattern,
  journalSummary,
  todayISO,
} from "./store";
import { MarketRegimeSnapshot, TradeSetup } from "../types";

type Cell = string | number | boolean | null;
export type Row = Record<string, Cell>;
export interface Sheet {
  name: string;
  rows: Row[];
}

const round = (n: number | null | undefined, dp = 2) =>
  n === null || n === undefined || Number.isNaN(n) ? null : Math.round(n * 10 ** dp) / 10 ** dp;

/* Writes a real .xlsx workbook. The library is loaded on demand so it
   doesn't slow down the first page load. */
export async function exportWorkbook(baseName: string, sheets: Sheet[]) {
  const XLSX = await import("xlsx");
  const book = XLSX.utils.book_new();
  sheets.forEach(({ name, rows }) => {
    const data = rows.length ? rows : [{ Info: "No data" }];
    const sheet = XLSX.utils.json_to_sheet(data);
    const headers = Object.keys(data[0]);
    sheet["!cols"] = headers.map((h) => ({
      wch: Math.min(48, Math.max(h.length, ...data.map((r) => String(r[h] ?? "").length)) + 2),
    }));
    if (headers.length) sheet["!autofilter"] = { ref: sheet["!ref"] ?? "A1" };
    XLSX.utils.book_append_sheet(book, sheet, name.slice(0, 31));
  });
  XLSX.writeFile(book, `${baseName}-${todayISO()}.xlsx`, { compression: true });
}

/* ── row builders ───────────────────────────────────────────────────── */

export function setupRow(s: TradeSetup, extra: Row = {}): Row {
  const stopPct = s.entry_price ? ((s.entry_price - s.stop_loss) / s.entry_price) * 100 : null;
  return {
    Symbol: s.symbol.replace(/\.NS$/, ""),
    Company: s.company_name,
    Sector: s.sector,
    Pattern: fmtPattern(s.pattern),
    "CMP (₹)": round(s.current_price),
    "Entry (₹)": round(s.entry_price),
    "Stop (₹)": round(s.stop_loss),
    "Stop %": round(stopPct),
    "Target (₹)": round(s.target_price),
    "Target %": round(s.expected_return_pct),
    "R:R": round(s.risk_reward_ratio),
    "Confidence %": round(s.probability_score * 100, 1),
    "RS score": round(s.relative_strength.score * 100, 0),
    "RSI 14": round(s.indicators.rsi14, 1),
    "Volume ratio": round(s.indicators.volume_ratio),
    "Backtest win %": s.backtest.total_trades ? round(s.backtest.win_rate * 100, 1) : null,
    "Backtest trades": s.backtest.total_trades,
    "Target date": s.estimated_target_date,
    "Sessions to target": s.estimated_target_sessions,
    "% off 52W high": round(s.price_levels?.distance_from_52w_high_pct),
    "New 52W high": s.price_levels ? (s.price_levels.price_discovery ? "Yes" : "No") : null,
    "Weekly checks (of 3)": s.weekly_trend?.checks_passed ?? null,
    "Sector rank": s.peer_rank ? `${s.peer_rank.rank}/${s.peer_rank.peer_count}` : null,
    "Fundamentals": s.fundamentals?.checks_available
      ? `${s.fundamentals.quality_score}/${s.fundamentals.checks_available}` : null,
    "Next results": s.event_risk.earnings_date,
    "Ex-dividend": s.event_risk.ex_dividend_date ?? null,
    "Sector rotation rank": s.sector_rotation ? `${s.sector_rotation.rank}/${s.sector_rotation.sector_count}` : null,
    "RS line leads price": s.rs_line ? (s.rs_line.leads_price ? "Yes" : "No") : null,
    "Delivery spike": s.smart_money ? (s.smart_money.delivery_spike ? "Yes" : "No") : null,
    "Bulk deals (buys/sells)": s.smart_money && s.smart_money.bulk_deal_source !== "unavailable"
      ? `${s.smart_money.bulk_deal_buys}/${s.smart_money.bulk_deal_sells}` : null,
    "Warnings": (s.quality_flags ?? []).join("; ") || null,
    ...extra,
  };
}

export function regimeRows(r: MarketRegimeSnapshot | null): Row[] {
  if (!r) return [{ Item: "Market regime", Value: "Unavailable" }];
  return [
    { Item: "Regime", Value: r.regime },
    { Item: "Assessment", Value: r.notes[0] ?? null },
    { Item: `${r.benchmark_name} close`, Value: r.benchmark_close },
    { Item: "Above 200 EMA", Value: r.benchmark_above_ema200 },
    { Item: "Above 50 EMA", Value: r.benchmark_above_ema50 },
    { Item: "20-day return %", Value: r.benchmark_return_20d_pct },
    { Item: "Breadth % above 50 EMA", Value: r.breadth_above_ema50_pct },
    { Item: "India VIX", Value: r.vix },
    { Item: "Min confidence", Value: round(r.recommended_min_probability * 100, 0) },
    { Item: "Min R:R", Value: r.recommended_min_risk_reward },
    { Item: "Position size %", Value: round(r.position_size_multiplier * 100, 0) },
    { Item: "As of", Value: r.generated_at },
  ];
}

export function journalSheets(trades: JournalTrade[], capital: number): Sheet[] {
  const summary = journalSummary(trades, capital);
  const open = trades.filter((t) => t.status === "open").map((t) => ({
    Symbol: t.symbol.replace(/\.NS$/, ""),
    Sector: t.sector,
    Pattern: fmtPattern(t.pattern),
    "Entry date": t.entry_date,
    "Planned entry (₹)": round(t.planned_entry),
    "Fill (₹)": round(t.entry_price),
    Qty: t.qty,
    "Stop (₹)": round(t.stop_loss),
    "Target (₹)": round(t.target_price),
    "Deployed (₹)": round(t.entry_price * t.qty),
    "Risk at stop (₹)": round(Math.max(0, t.entry_price - t.stop_loss) * t.qty),
    "Risk % of capital": capital ? round((Math.max(0, t.entry_price - t.stop_loss) * t.qty / capital) * 100) : null,
  }));
  const closed = trades.filter((t) => t.status === "closed").map((t) => {
    const m = closedMetrics(t);
    return {
      Symbol: t.symbol.replace(/\.NS$/, ""),
      Sector: t.sector,
      Pattern: fmtPattern(t.pattern),
      "Entry date": t.entry_date,
      "Exit date": t.exit_date ?? null,
      "Fill (₹)": round(t.entry_price),
      "Exit (₹)": round(t.exit_price),
      Qty: t.qty,
      "P&L (₹)": round(m.pnl),
      "R multiple": round(m.r),
      "Days held": m.holdingDays,
      "Slippage %": round(m.slippagePct),
      "Exit reason": t.exit_reason ?? null,
      "Exit discipline": t.exit_discipline ?? null,
      "Exit rule": t.exit_rule ?? null,
      "Partial exits": (t.partials ?? []).map((p) => `${p.qty}@${p.price}`).join(", ") || null,
      "Backtest win % at entry": t.backtest_win_rate === null ? null : round(t.backtest_win_rate * 100, 1),
    };
  });
  const groupRows = (groups: typeof summary.byPattern, label: string) => groups.map((g) => ({
    [label]: label === "Pattern" ? fmtPattern(g.key) : g.key,
    Trades: g.trades,
    "Win rate %": round(g.winRate * 100, 1),
    "Backtest win %": g.backtestWinRate === null ? null : round(g.backtestWinRate * 100, 1),
    "Avg R": round(g.avgR),
    "P&L (₹)": round(g.pnl),
  }));
  return [
    {
      name: "Summary",
      rows: [
        { Metric: "Capital (₹)", Value: capital },
        { Metric: "Closed trades", Value: summary.closedCount },
        { Metric: "Open positions", Value: summary.openCount },
        { Metric: "Win rate %", Value: summary.winRate === null ? null : round(summary.winRate * 100, 1) },
        { Metric: "Avg R per trade", Value: round(summary.avgR) },
        { Metric: "Net P&L (₹)", Value: round(summary.netPnl) },
        { Metric: "Net P&L % of capital", Value: capital ? round((summary.netPnl / capital) * 100) : null },
        { Metric: "Avg slippage %", Value: round(summary.avgSlippagePct) },
        { Metric: "Open risk (₹)", Value: round(summary.openRisk) },
        { Metric: "Exported", Value: new Date().toLocaleString("en-IN") },
      ],
    },
    { name: "Open positions", rows: open },
    { name: "Closed trades", rows: closed },
    { name: "By pattern", rows: groupRows(summary.byPattern, "Pattern") },
    { name: "Exit discipline", rows: groupRows(summary.byDiscipline, "Exit type") },
    { name: "By sector", rows: groupRows(summary.bySector, "Sector") },
    {
      name: "By month",
      rows: summary.monthly.map(([month, pnl]) => ({
        Month: month,
        "P&L (₹)": round(pnl),
        "Return % of capital": capital ? round((pnl / capital) * 100) : null,
      })),
    },
  ];
}

export function watchlistRows(items: WatchItem[]): Row[] {
  return items.map((w) => ({
    Symbol: w.symbol.replace(/\.NS$/, ""),
    Sector: w.sector,
    Pattern: fmtPattern(w.pattern),
    "Entry trigger (₹)": round(w.trigger_price),
    "Stop (₹)": round(w.stop_loss),
    "Target (₹)": round(w.target_price),
    "Last close (₹)": round(w.last_close),
    "% below trigger": w.last_close ? round(((w.trigger_price - w.last_close) / w.last_close) * 100) : null,
    Added: w.added_at.slice(0, 10),
    Expires: w.expires_at.slice(0, 10),
    "Last checked": w.last_checked ? new Date(w.last_checked).toLocaleString("en-IN") : null,
  }));
}
