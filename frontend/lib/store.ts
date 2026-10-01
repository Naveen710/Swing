"use client";

import { useCallback, useEffect, useState } from "react";

import { TradeSetup } from "../types";

/* Journal, watchlist and trading settings live in this browser's localStorage.
   They survive refreshes and backend restarts, but are per-browser: use the
   journal's Backup / Restore buttons to move them between devices. */

const KEYS = {
  journal: "swing.journal.v1",
  watchlist: "swing.watchlist.v1",
  settings: "swing.settings.v1",
} as const;
const CHANGE_EVENT = "swing-store-change";

export const MAX_OPEN_POSITIONS = 5;
export const MAX_PER_SECTOR = 2;
export const WATCH_DAYS = 7;

export type ExitReason = "target" | "stop" | "manual" | "time";

export interface JournalTrade {
  id: string;
  symbol: string;
  company_name: string;
  sector: string;
  pattern: string;
  status: "open" | "closed";
  entry_date: string;
  planned_entry: number;
  entry_price: number;
  qty: number;
  stop_loss: number;
  target_price: number;
  backtest_win_rate: number | null;
  probability_score: number | null;
  exit_date?: string;
  exit_price?: number;
  exit_reason?: ExitReason;
  notes?: string;
  /** Original stop before any trailing — R is always measured against this. */
  initial_stop?: number;
  /** Partial exits booked before the final close. */
  partials?: { qty: number; price: number; date: string }[];
  /** Whether the final exit followed an exit-engine rule or was discretionary. */
  exit_discipline?: "rule" | "instinct";
  exit_rule?: string | null;
}

export interface WatchItem {
  id: string;
  symbol: string;
  company_name: string;
  sector: string;
  pattern: string;
  trigger_price: number;
  stop_loss: number;
  target_price: number;
  added_at: string;
  expires_at: string;
  last_close?: number;
  last_high?: number;
  last_checked?: string;
  notified?: boolean;
}

export interface TradingSettings {
  capital: number;
  riskPct: number;
}

export const DEFAULT_SETTINGS: TradingSettings = { capital: 1800000, riskPct: 1.5 };

/* ── low-level storage ─────────────────────────────────────────────── */

function read<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write<T>(key: string, value: T) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: key }));
  } catch {
    /* storage full or disabled — ignore */
  }
}

function useStored<T>(key: string, fallback: T): [T, (next: T | ((prev: T) => T)) => void, boolean] {
  const [value, setValue] = useState<T>(fallback);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setValue(read(key, fallback));
    setReady(true);
    const sync = (event: Event) => {
      const changed =
        event instanceof StorageEvent ? event.key : (event as CustomEvent<string>).detail;
      if (changed === key) setValue(read(key, fallback));
    };
    window.addEventListener("storage", sync);
    window.addEventListener(CHANGE_EVENT, sync);
    return () => {
      window.removeEventListener("storage", sync);
      window.removeEventListener(CHANGE_EVENT, sync);
    };
    // fallback is a constant per call-site
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const update = useCallback(
    (next: T | ((prev: T) => T)) => {
      const resolved =
        typeof next === "function" ? (next as (prev: T) => T)(read(key, fallback)) : next;
      write(key, resolved);
      setValue(resolved);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key]
  );

  return [value, update, ready];
}

export function useJournal() {
  return useStored<JournalTrade[]>(KEYS.journal, []);
}
export function useWatchlist() {
  return useStored<WatchItem[]>(KEYS.watchlist, []);
}
export function useTradingSettings() {
  return useStored<TradingSettings>(KEYS.settings, DEFAULT_SETTINGS);
}

/* ── helpers ────────────────────────────────────────────────────────── */

export function uid() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export function todayISO() {
  const d = new Date();
  const offset = d.getTimezoneOffset() * 60000;
  return new Date(d.getTime() - offset).toISOString().slice(0, 10);
}

export function positionQty(
  entry: number,
  stop: number,
  settings: TradingSettings,
  sizeMultiplier = 1
): number {
  const riskRs = (settings.capital * settings.riskPct * sizeMultiplier) / 100;
  const perShare = entry - stop;
  if (entry <= 0 || perShare <= 0) return 0;
  const byRisk = riskRs / perShare;
  const byCap = (settings.capital * 0.2) / entry;
  return Math.max(0, Math.floor(Math.min(byRisk, byCap)));
}

export function tradeFromSetup(setup: TradeSetup, qty: number): JournalTrade {
  return {
    id: uid(),
    symbol: setup.symbol,
    company_name: setup.company_name,
    sector: setup.sector,
    pattern: setup.pattern,
    status: "open",
    entry_date: todayISO(),
    planned_entry: setup.entry_price,
    entry_price: setup.entry_price,
    qty,
    stop_loss: setup.stop_loss,
    target_price: setup.target_price,
    backtest_win_rate: setup.backtest.total_trades > 0 ? setup.backtest.win_rate : null,
    probability_score: setup.probability_score,
  };
}

export function watchFromSetup(setup: TradeSetup): WatchItem {
  const now = new Date();
  const expires = new Date(now.getTime() + WATCH_DAYS * 86400000);
  return {
    id: uid(),
    symbol: setup.symbol,
    company_name: setup.company_name,
    sector: setup.sector,
    pattern: setup.pattern,
    trigger_price: setup.entry_price,
    stop_loss: setup.stop_loss,
    target_price: setup.target_price,
    added_at: now.toISOString(),
    expires_at: expires.toISOString(),
    last_close: setup.current_price,
  };
}

/* ── analytics ──────────────────────────────────────────────────────── */

export interface ClosedMetrics {
  pnl: number;
  r: number | null;
  holdingDays: number;
  slippagePct: number;
  win: boolean;
}

export function initialQty(t: JournalTrade) {
  return t.qty + (t.partials ?? []).reduce((a, p) => a + p.qty, 0);
}

export function closedMetrics(t: JournalTrade): ClosedMetrics {
  const exit = t.exit_price ?? t.entry_price;
  const partialPnl = (t.partials ?? []).reduce((a, p) => a + (p.price - t.entry_price) * p.qty, 0);
  const pnl = partialPnl + (exit - t.entry_price) * t.qty;
  const riskPerShare = t.entry_price - (t.initial_stop ?? t.stop_loss);
  const totalRisk = riskPerShare * initialQty(t);
  const r = totalRisk > 0 ? pnl / totalRisk : null;
  const start = new Date(t.entry_date).getTime();
  const end = new Date(t.exit_date ?? todayISO()).getTime();
  const holdingDays = Math.max(0, Math.round((end - start) / 86400000));
  const slippagePct = t.planned_entry > 0 ? ((t.entry_price - t.planned_entry) / t.planned_entry) * 100 : 0;
  return { pnl, r, holdingDays, slippagePct, win: pnl > 0 };
}

export interface GroupStat {
  key: string;
  trades: number;
  winRate: number;
  avgR: number | null;
  pnl: number;
  backtestWinRate: number | null;
}

function groupStats(trades: JournalTrade[], keyOf: (t: JournalTrade) => string): GroupStat[] {
  const groups = new Map<string, JournalTrade[]>();
  trades.forEach((t) => groups.set(keyOf(t), [...(groups.get(keyOf(t)) ?? []), t]));
  return [...groups.entries()]
    .map(([key, list]) => {
      const metrics = list.map(closedMetrics);
      const rs = metrics.map((m) => m.r).filter((r): r is number => r !== null);
      const bts = list.map((t) => t.backtest_win_rate).filter((b): b is number => b !== null);
      return {
        key,
        trades: list.length,
        winRate: metrics.filter((m) => m.win).length / list.length,
        avgR: rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : null,
        pnl: metrics.reduce((a, m) => a + m.pnl, 0),
        backtestWinRate: bts.length ? bts.reduce((a, b) => a + b, 0) / bts.length : null,
      };
    })
    .sort((a, b) => b.trades - a.trades);
}

export function journalSummary(trades: JournalTrade[], capital: number) {
  const closed = trades.filter((t) => t.status === "closed");
  const open = trades.filter((t) => t.status === "open");
  const metrics = closed.map(closedMetrics);
  const rs = metrics.map((m) => m.r).filter((r): r is number => r !== null);
  const wins = metrics.filter((m) => m.win).length;

  const openRisk = open.reduce((a, t) => a + Math.max(0, t.entry_price - t.stop_loss) * t.qty, 0);
  const openDeployed = open.reduce((a, t) => a + t.entry_price * t.qty, 0);
  const sectorCounts = new Map<string, number>();
  open.forEach((t) => sectorCounts.set(t.sector, (sectorCounts.get(t.sector) ?? 0) + 1));

  const monthly = new Map<string, number>();
  closed.forEach((t, i) => {
    const month = (t.exit_date ?? t.entry_date).slice(0, 7);
    monthly.set(month, (monthly.get(month) ?? 0) + metrics[i].pnl);
  });

  return {
    closedCount: closed.length,
    openCount: open.length,
    winRate: closed.length ? wins / closed.length : null,
    avgR: rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : null,
    netPnl: metrics.reduce((a, m) => a + m.pnl, 0),
    avgHoldingDays: metrics.length ? metrics.reduce((a, m) => a + m.holdingDays, 0) / metrics.length : null,
    avgSlippagePct: metrics.length ? metrics.reduce((a, m) => a + m.slippagePct, 0) / metrics.length : null,
    openRisk,
    openRiskPct: capital > 0 ? (openRisk / capital) * 100 : 0,
    openDeployed,
    sectorCounts: [...sectorCounts.entries()].sort((a, b) => b[1] - a[1]),
    byPattern: groupStats(closed, (t) => t.pattern),
    byDiscipline: groupStats(
      closed.filter((t) => t.exit_discipline),
      (t) => (t.exit_discipline === "rule" ? "Followed an exit rule" : "Discretionary exit"),
    ),
    bySector: groupStats(closed, (t) => t.sector),
    monthly: [...monthly.entries()].sort((a, b) => b[0].localeCompare(a[0])),
  };
}

export function openSectorCounts(trades: JournalTrade[]) {
  const counts = new Map<string, number>();
  trades
    .filter((t) => t.status === "open")
    .forEach((t) => counts.set(t.sector, (counts.get(t.sector) ?? 0) + 1));
  return counts;
}

export function journalToCsv(trades: JournalTrade[]) {
  const header = [
    "symbol", "sector", "pattern", "status", "entry_date", "planned_entry", "entry_price", "qty",
    "stop_loss", "target_price", "exit_date", "exit_price", "exit_reason", "pnl", "r_multiple",
  ];
  const rows = trades.map((t) => {
    const m = t.status === "closed" ? closedMetrics(t) : null;
    return [
      t.symbol, t.sector, t.pattern, t.status, t.entry_date, t.planned_entry, t.entry_price, t.qty,
      t.stop_loss, t.target_price, t.exit_date ?? "", t.exit_price ?? "", t.exit_reason ?? "",
      m ? m.pnl.toFixed(2) : "", m && m.r !== null ? m.r.toFixed(2) : "",
    ]
      .map((v) => `"${String(v).replace(/"/g, '""')}"`)
      .join(",");
  });
  return [header.join(","), ...rows].join("\n");
}

export function downloadText(filename: string, text: string, mime = "text/plain") {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/* ── formatting ─────────────────────────────────────────────────────── */

export function fmtINR(n: number) {
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(n);
}
export function fmtPattern(p: string) {
  return p.split("_").map((w) => (w[0]?.toUpperCase() ?? "") + w.slice(1)).join(" ");
}
