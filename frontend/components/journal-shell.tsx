"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import {
  ExitReason,
  JournalTrade,
  MAX_OPEN_POSITIONS,
  MAX_PER_SECTOR,
  closedMetrics,
  downloadText,
  fmtINR,
  fmtPattern,
  journalSummary,
  journalToCsv,
  todayISO,
  uid,
  useJournal,
  useTradingSettings,
} from "../lib/store";
import { getPortfolioRisk, getStockDetail } from "../lib/api";
import { exportWorkbook, journalSheets } from "../lib/excel";
import { ExitAdvice, computeExitAdvice } from "../lib/exits";
import { PortfolioRiskResponse } from "../types";
import { AppNav } from "./app-nav";

const PATTERNS = [
  "consolidation_breakout",
  "ema_pullback",
  "relative_strength_breakout",
  "support_bounce",
  "volatility_contraction",
  "gap_momentum",
  "manual",
];

interface CloseDraft { price: string; date: string; reason: ExitReason }
interface EditDraft { entry: string; qty: string; stop: string; target: string; date: string }

export function JournalShell() {
  const [trades, setTrades, ready] = useJournal();
  const [settings, setSettings] = useTradingSettings();
  const [closing, setClosing] = useState<Record<string, CloseDraft>>({});
  const [editing, setEditing] = useState<Record<string, EditDraft>>({});
  const [showAdd, setShowAdd] = useState(false);
  const [advice, setAdvice] = useState<Record<string, ExitAdvice | null>>({});
  const [adviceLoading, setAdviceLoading] = useState(false);
  const [risk, setRisk] = useState<PortfolioRiskResponse | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const summary = journalSummary(trades, settings.capital);
  const open = trades.filter((t) => t.status === "open");
  const closed = trades
    .filter((t) => t.status === "closed")
    .sort((a, b) => (b.exit_date ?? "").localeCompare(a.exit_date ?? ""));

  function update(id: string, patch: Partial<JournalTrade>) {
    setTrades((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));
  }

  const openKey = trades.filter((t) => t.status === "open").map((t) => `${t.id}:${t.qty}:${t.stop_loss}`).join("|");

  async function refreshAdvice() {
    const openTrades = trades.filter((t) => t.status === "open");
    if (!openTrades.length) { setAdvice({}); setRisk(null); return; }
    setAdviceLoading(true);
    const next: Record<string, ExitAdvice | null> = {};
    const queue = [...openTrades];
    const worker = async () => {
      while (queue.length) {
        const t = queue.shift()!;
        try {
          const detail = await getStockDetail(t.symbol);
          next[t.id] = computeExitAdvice(t, detail.candles);
        } catch {
          next[t.id] = null;
        }
      }
    };
    await Promise.all([worker(), worker(), worker()]);
    setAdvice(next);
    setAdviceLoading(false);
    getPortfolioRisk({
      holdings: openTrades.map((t) => ({ symbol: t.symbol, value: t.entry_price * t.qty })),
      capital: settings.capital,
    }).then(setRisk).catch(() => setRisk(null));
  }

  useEffect(() => {
    if (ready) void refreshAdvice();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, openKey]);

  function bookPartial(t: JournalTrade, a: ExitAdvice) {
    const qty = a.partialQty ?? Math.floor(t.qty / 2);
    const input = window.prompt(`Sell ${qty} shares of ${t.symbol} at what price?`, String(a.partialPrice ?? a.lastClose));
    const price = Number(input);
    if (!input || !(price > 0) || qty <= 0 || qty >= t.qty) return;
    update(t.id, {
      qty: t.qty - qty,
      initial_stop: t.initial_stop ?? t.stop_loss,
      partials: [...(t.partials ?? []), { qty, price, date: todayISO() }],
    });
    setMessage(`Booked ${qty} ${t.symbol} at ₹${price}. ${t.qty - qty} shares still open.`);
  }

  function applyTrail(t: JournalTrade, a: ExitAdvice) {
    if (a.suggestedStop === null) return;
    update(t.id, { stop_loss: a.suggestedStop, initial_stop: t.initial_stop ?? t.stop_loss });
    setMessage(`${t.symbol} stop raised to ₹${a.suggestedStop}.`);
  }

  function startClose(t: JournalTrade) {
    const a = advice[t.id];
    const reason: ExitReason =
      a?.rule === "stop" ? "stop" : a?.rule === "time_stop" ? "time" : a?.rule === "target" ? "target" : "manual";
    const price = a?.rule === "target" ? t.target_price : a ? a.lastClose : t.target_price;
    setClosing({ ...closing, [t.id]: { price: String(price), date: todayISO(), reason } });
  }
  function confirmClose(t: JournalTrade) {
    const d = closing[t.id];
    const price = Number(d.price);
    if (!price || price <= 0) { setMessage("Enter a valid exit price."); return; }
    const a = advice[t.id];
    update(t.id, {
      status: "closed", exit_price: price, exit_date: d.date, exit_reason: d.reason,
      initial_stop: t.initial_stop ?? t.stop_loss,
      exit_discipline: a?.action === "exit" ? "rule" : "instinct",
      exit_rule: a?.action === "exit" ? a.rule : null,
    });
    const rest = { ...closing }; delete rest[t.id]; setClosing(rest);
    setMessage(`${t.symbol} closed.`);
  }
  function setReason(t: JournalTrade, reason: ExitReason) {
    const d = closing[t.id];
    const price = reason === "target" ? t.target_price : reason === "stop" ? t.stop_loss : Number(d.price);
    setClosing({ ...closing, [t.id]: { ...d, reason, price: String(price) } });
  }

  function startEdit(t: JournalTrade) {
    setEditing({
      ...editing,
      [t.id]: { entry: String(t.entry_price), qty: String(t.qty), stop: String(t.stop_loss), target: String(t.target_price), date: t.entry_date },
    });
  }
  function saveEdit(t: JournalTrade) {
    const d = editing[t.id];
    const entry = Number(d.entry), qty = Math.floor(Number(d.qty)), stop = Number(d.stop), target = Number(d.target);
    if (!(entry > 0) || !(qty > 0)) { setMessage("Entry price and quantity must be positive."); return; }
    if (!(stop > 0) || stop >= entry) { setMessage("Stop loss must be below the entry price."); return; }
    if (!(target > entry)) { setMessage("Target must be above the entry price."); return; }
    update(t.id, {
      entry_price: Number(d.entry) || t.entry_price,
      qty: Math.max(0, Math.floor(Number(d.qty))) || t.qty,
      stop_loss: Number(d.stop) || t.stop_loss,
      target_price: Number(d.target) || t.target_price,
      entry_date: d.date || t.entry_date,
    });
    const rest = { ...editing }; delete rest[t.id]; setEditing(rest);
  }

  function remove(t: JournalTrade) {
    if (window.confirm(`Delete ${t.symbol} from the journal? This cannot be undone.`)) {
      setTrades((prev) => prev.filter((x) => x.id !== t.id));
    }
  }
  function reopen(t: JournalTrade) {
    update(t.id, { status: "open", exit_price: undefined, exit_date: undefined, exit_reason: undefined });
  }

  function backup() {
    downloadText(`swing-journal-${todayISO()}.json`, JSON.stringify({ version: 1, trades, settings }, null, 2), "application/json");
  }
  function restore(file: File) {
    file.text().then((text) => {
      try {
        const data = JSON.parse(text);
        const incoming: JournalTrade[] = Array.isArray(data) ? data : data.trades;
        if (!Array.isArray(incoming)) throw new Error("bad file");
        if (!window.confirm(`Replace your journal with ${incoming.length} trades from this backup?`)) return;
        setTrades(incoming);
        if (data.settings) setSettings(data.settings);
        setMessage(`Restored ${incoming.length} trades.`);
      } catch {
        setMessage("That file isn't a valid journal backup.");
      }
    });
  }

  return (
    <main className="page-shell">
      <AppNav />

      <section className="jr-head">
        <div>
          <p className="eyebrow">Trade journal</p>
          <h1 className="jr-title">Your real results, not the backtest.</h1>
          <p className="muted">
            Log every trade the system gives you. After ~20 closed trades per pattern, compare your real win rate
            to the backtest — that gap is your true edge (or leak).
          </p>
        </div>
        <div className="jr-capital">
          <label className="field">
            Trading capital (₹)
            <input type="number" min={10000} step={50000} value={settings.capital}
              onChange={(e) => setSettings({ ...settings, capital: Number(e.target.value) })} />
          </label>
          <div className="jr-io">
            <button className="mini-btn mini-btn--primary" onClick={() => void exportWorkbook("swing-journal", journalSheets(trades, settings.capital))} disabled={!trades.length}>Export to Excel</button>
            <button className="mini-btn" onClick={() => downloadText(`swing-journal-${todayISO()}.csv`, journalToCsv(trades), "text/csv")} disabled={!trades.length}>Export CSV</button>
            <button className="mini-btn" onClick={backup} disabled={!trades.length}>Backup</button>
            <button className="mini-btn" onClick={() => fileRef.current?.click()}>Restore</button>
            <input ref={fileRef} type="file" accept="application/json" hidden
              onChange={(e) => { const f = e.target.files?.[0]; if (f) restore(f); e.target.value = ""; }} />
          </div>
          <p className="jr-hint">Saved in this browser. Use Backup to move it to another device.</p>
        </div>
      </section>

      {message && <p className="jr-msg" onClick={() => setMessage(null)}>{message}</p>}

      <section className="jr-stats">
        <Stat label="Closed trades" value={String(summary.closedCount)} />
        <Stat label="Win rate" value={summary.winRate === null ? "—" : `${Math.round(summary.winRate * 100)}%`}
          tone={summary.winRate === null ? undefined : summary.winRate >= 0.45 ? "ok" : "bad"} />
        <Stat label="Avg R per trade" value={summary.avgR === null ? "—" : `${summary.avgR >= 0 ? "+" : ""}${summary.avgR.toFixed(2)}R`}
          tone={summary.avgR === null ? undefined : summary.avgR > 0 ? "ok" : "bad"} />
        <Stat label="Net P&L" value={fmtINR(summary.netPnl)} tone={summary.netPnl === 0 ? undefined : summary.netPnl > 0 ? "ok" : "bad"}
          sub={settings.capital > 0 ? `${((summary.netPnl / settings.capital) * 100).toFixed(2)}% of capital` : undefined} />
        <Stat label="Avg slippage" value={summary.avgSlippagePct === null ? "—" : `${summary.avgSlippagePct >= 0 ? "+" : ""}${summary.avgSlippagePct.toFixed(2)}%`}
          sub="Actual fill vs planned entry" />
        <Stat label="Open risk" value={fmtINR(summary.openRisk)} sub={`${summary.openRiskPct.toFixed(1)}% of capital`}
          tone={summary.openRiskPct > 6 ? "bad" : undefined} />
      </section>

      {/* Portfolio heat */}
      <section className="panel jr-panel">
        <div className="jr-panel-head">
          <h2>Open positions ({open.length}/{MAX_OPEN_POSITIONS})</h2>
          <button className="mini-btn mini-btn--primary" onClick={() => setShowAdd((v) => !v)}>
            {showAdd ? "Cancel" : "+ Add trade manually"}
          </button>
        </div>
        {summary.sectorCounts.length > 0 && (
          <div className="jr-heat">
            {summary.sectorCounts.map(([sector, n]) => (
              <span key={sector} className={`pill-tag ${n >= MAX_PER_SECTOR ? "pill-tag--bad" : "pill-tag--muted"}`}>
                {sector} {n}/{MAX_PER_SECTOR}
              </span>
            ))}
            <span className="jr-hint">Deployed {fmtINR(summary.openDeployed)}</span>
          </div>
        )}

        {showAdd && <AddTradeForm onAdd={(t) => { setTrades((prev) => [t, ...prev]); setShowAdd(false); setMessage(`${t.symbol} added.`); }} />}

        {!ready ? null : open.length === 0 ? (
          <div className="empty-state">
            <p>No open positions. Use <strong>Log trade</strong> on a system pick, or add one manually.</p>
            <Link href="/" className="text-link">Go to the scanner →</Link>
          </div>
        ) : (
          <div className="table-shell">
            <table className="jr-tbl">
              <thead>
                <tr><th>Stock</th><th>Entered</th><th>Entry</th><th>Qty</th><th>Stop</th><th>Target</th><th>Risk</th><th>Exit plan</th><th></th></tr>
              </thead>
              <tbody>
                {open.map((t) => {
                  const e = editing[t.id];
                  const c = closing[t.id];
                  const risk = Math.max(0, t.entry_price - t.stop_loss) * t.qty;
                  const slip = t.planned_entry > 0 ? ((t.entry_price - t.planned_entry) / t.planned_entry) * 100 : 0;
                  return (
                    <tr key={t.id}>
                      <td>
                        <Link href={`/stocks/${t.symbol}`} className="stock-link">{t.symbol}</Link>
                        <div className="table-subtext">{t.sector} · {fmtPattern(t.pattern)}</div>
                      </td>
                      <td>{e ? <input className="num-input" type="date" value={e.date} onChange={(x) => setEditing({ ...editing, [t.id]: { ...e, date: x.target.value } })} /> : t.entry_date}</td>
                      <td>
                        {e ? <input className="num-input" type="number" value={e.entry} onChange={(x) => setEditing({ ...editing, [t.id]: { ...e, entry: x.target.value } })} />
                          : <>{fmtINR(t.entry_price)}{Math.abs(slip) >= 0.05 && <div className="table-subtext">plan {fmtINR(t.planned_entry)} ({slip > 0 ? "+" : ""}{slip.toFixed(1)}%)</div>}</>}
                      </td>
                      <td>{e ? <input className="num-input" type="number" value={e.qty} onChange={(x) => setEditing({ ...editing, [t.id]: { ...e, qty: x.target.value } })} /> : t.qty}</td>
                      <td className="q-bad">{e ? <input className="num-input" type="number" value={e.stop} onChange={(x) => setEditing({ ...editing, [t.id]: { ...e, stop: x.target.value } })} /> : fmtINR(t.stop_loss)}</td>
                      <td className="q-ok">{e ? <input className="num-input" type="number" value={e.target} onChange={(x) => setEditing({ ...editing, [t.id]: { ...e, target: x.target.value } })} /> : fmtINR(t.target_price)}</td>
                      <td>{fmtINR(risk)}<div className="table-subtext">{settings.capital ? ((risk / settings.capital) * 100).toFixed(2) : "0"}%</div></td>
                      <td className="jr-exit">
                        {(() => {
                          const a = advice[t.id];
                          if (a === undefined) return <span className="table-subtext">{adviceLoading ? "Checking…" : "—"}</span>;
                          if (a === null) return <span className="table-subtext">No price data</span>;
                          const tone = a.action === "exit" ? "pill-tag--bad" : a.action === "book_partial" || a.action === "trail" ? "pill-tag--warn" : "pill-tag--muted";
                          const label = { exit: "Exit", book_partial: "Book half", trail: "Trail stop", hold: "Hold" }[a.action];
                          return (
                            <>
                              <span className={`pill-tag ${tone}`}>{label}</span>
                              <div className="jr-exit-msg">{a.message}</div>
                              <div className="table-subtext">Last ₹{a.lastClose.toFixed(2)}{a.ema20 !== null && ` · 20 EMA ₹${a.ema20.toFixed(2)}`} · day {a.sessionsHeld}</div>
                              {(t.partials ?? []).length > 0 && <div className="table-subtext">Booked {(t.partials ?? []).map((p) => `${p.qty}@₹${p.price}`).join(", ")}</div>}
                              {a.action === "book_partial" && <button className="mini-btn mini-btn--primary" onClick={() => bookPartial(t, a)}>Book half</button>}
                              {a.action === "trail" && <button className="mini-btn mini-btn--primary" onClick={() => applyTrail(t, a)}>Raise stop to ₹{a.suggestedStop}</button>}
                            </>
                          );
                        })()}
                      </td>
                      <td>
                        {c ? (
                          <div className="jr-close">
                            <div className="jr-close-reasons">
                              {(["target", "stop", "manual", "time"] as ExitReason[]).map((r) => (
                                <button key={r} className={`mini-btn ${c.reason === r ? "mini-btn--primary" : ""}`} onClick={() => setReason(t, r)}>{r}</button>
                              ))}
                            </div>
                            <input className="num-input" type="number" value={c.price} placeholder="Exit price"
                              onChange={(x) => setClosing({ ...closing, [t.id]: { ...c, price: x.target.value } })} />
                            <input className="num-input" type="date" value={c.date}
                              onChange={(x) => setClosing({ ...closing, [t.id]: { ...c, date: x.target.value } })} />
                            <div className="jr-row-actions">
                              <button className="mini-btn mini-btn--primary" onClick={() => confirmClose(t)}>Confirm exit</button>
                              <button className="mini-btn" onClick={() => { const rest = { ...closing }; delete rest[t.id]; setClosing(rest); }}>Cancel</button>
                            </div>
                          </div>
                        ) : e ? (
                          <div className="jr-row-actions">
                            <button className="mini-btn mini-btn--primary" onClick={() => saveEdit(t)}>Save</button>
                            <button className="mini-btn" onClick={() => { const rest = { ...editing }; delete rest[t.id]; setEditing(rest); }}>Cancel</button>
                          </div>
                        ) : (
                          <div className="jr-row-actions">
                            <button className="mini-btn mini-btn--primary" onClick={() => startClose(t)}>Close</button>
                            <button className="mini-btn" onClick={() => startEdit(t)}>Edit fill</button>
                            <button className="mini-btn mini-btn--danger" onClick={() => remove(t)}>Delete</button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="jr-2col">
        <section className="panel jr-panel">
          <div className="jr-panel-head">
            <h2>Portfolio risk</h2>
            <button className="mini-btn" onClick={() => void refreshAdvice()} disabled={adviceLoading}>{adviceLoading ? "Updating…" : "Refresh"}</button>
          </div>
          {!risk || risk.holdings.length === 0 ? (
            <p className="muted">Beta and correlation appear here once you have open positions.</p>
          ) : (
            <>
              <div className="jr-risk-top">
                <div><span className="jr-stat-l">Portfolio beta</span><strong className={risk.portfolio_beta !== null && risk.portfolio_beta > 1.3 ? "q-bad" : ""}>{risk.portfolio_beta?.toFixed(2) ?? "—"}</strong></div>
                <div><span className="jr-stat-l">Beta on total capital</span><strong>{risk.capital_weighted_beta?.toFixed(2) ?? "—"}</strong></div>
                <div><span className="jr-stat-l">Avg correlation</span><strong className={risk.average_pairwise_correlation !== null && risk.average_pairwise_correlation >= 0.6 ? "q-bad" : ""}>{risk.average_pairwise_correlation?.toFixed(2) ?? "—"}</strong></div>
              </div>
              <p className="muted jr-risk-note">
                {risk.portfolio_beta !== null
                  ? `If ${risk.benchmark_name} falls 3%, these positions would typically fall about ${(risk.portfolio_beta * 3).toFixed(1)}%.`
                  : ""}
                {risk.high_correlation_pairs.length > 0
                  ? ` ⚠ ${risk.high_correlation_pairs.map((p) => `${p.a.replace(".NS", "")} & ${p.b.replace(".NS", "")} (${p.correlation.toFixed(2)})`).join(", ")} move together — that's one bet, not two.`
                  : " No pair of holdings is highly correlated."}
              </p>
              <table className="jr-tbl">
                <thead><tr><th>Holding</th><th>Beta</th><th>Volatility</th><th>Most correlated with</th></tr></thead>
                <tbody>
                  {risk.holdings.map((h) => (
                    <tr key={h.symbol}>
                      <td>{h.symbol.replace(".NS", "")}</td>
                      <td>{h.beta?.toFixed(2) ?? "—"}</td>
                      <td>{h.volatility_pct !== null ? `${h.volatility_pct.toFixed(0)}%/yr` : "—"}</td>
                      <td className={h.high_correlation ? "q-bad" : ""}>{h.most_correlated_with ? `${h.most_correlated_with.replace(".NS", "")} (${h.max_correlation?.toFixed(2)})` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </section>
        <section className="panel jr-panel">
          <h2>Exit discipline</h2>
          <p className="muted" style={{ marginTop: 0 }}>
            Exits that followed an exit-plan rule (stop, target, 20 EMA, time stop) versus exits made on instinct.
          </p>
          {summary.byDiscipline.length === 0 ? (
            <p className="muted">Close a trade to start tracking this.</p>
          ) : (
            <>
              <table className="jr-tbl">
                <thead><tr><th>Exit type</th><th>Trades</th><th>Win rate</th><th>Avg R</th><th>P&L</th></tr></thead>
                <tbody>
                  {summary.byDiscipline.map((g) => (
                    <tr key={g.key}>
                      <td>{g.key}</td><td>{g.trades}</td><td>{Math.round(g.winRate * 100)}%</td>
                      <td className={g.avgR === null ? "" : g.avgR >= 0 ? "q-ok" : "q-bad"}>{g.avgR === null ? "—" : `${g.avgR.toFixed(2)}R`}</td>
                      <td className={g.pnl >= 0 ? "q-ok" : "q-bad"}>{fmtINR(g.pnl)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="jr-hint">
                {(() => {
                  const ruled = summary.byDiscipline.find((g) => g.key.startsWith("Followed"))?.trades ?? 0;
                  const total = summary.byDiscipline.reduce((a, g) => a + g.trades, 0);
                  return `You followed the plan on ${Math.round((ruled / total) * 100)}% of exits.`;
                })()}
              </p>
            </>
          )}
        </section>
      </div>

      {/* Pattern performance */}
      <section className="panel jr-panel">
        <h2>Performance by pattern</h2>
        <p className="muted" style={{ marginTop: 0 }}>Real results versus the scanner&apos;s backtest for the same setups. Treat anything under 20 trades as noise.</p>
        {summary.byPattern.length === 0 ? (
          <div className="empty-state"><p>Close your first trade to start building this table.</p></div>
        ) : (
          <div className="table-shell">
            <table className="jr-tbl">
              <thead><tr><th>Pattern</th><th>Trades</th><th>Your win rate</th><th>Backtest win rate</th><th>Gap</th><th>Avg R</th><th>P&L</th></tr></thead>
              <tbody>
                {summary.byPattern.map((g) => {
                  const gap = g.backtestWinRate === null ? null : g.winRate - g.backtestWinRate;
                  return (
                    <tr key={g.key}>
                      <td>{fmtPattern(g.key)}{g.trades < 20 && <div className="table-subtext">small sample</div>}</td>
                      <td>{g.trades}</td>
                      <td>{Math.round(g.winRate * 100)}%</td>
                      <td>{g.backtestWinRate === null ? "—" : `${Math.round(g.backtestWinRate * 100)}%`}</td>
                      <td className={gap === null ? "" : gap >= 0 ? "q-ok" : "q-bad"}>{gap === null ? "—" : `${gap >= 0 ? "+" : ""}${Math.round(gap * 100)} pts`}</td>
                      <td className={g.avgR === null ? "" : g.avgR >= 0 ? "q-ok" : "q-bad"}>{g.avgR === null ? "—" : `${g.avgR.toFixed(2)}R`}</td>
                      <td className={g.pnl >= 0 ? "q-ok" : "q-bad"}>{fmtINR(g.pnl)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="jr-2col">
        <section className="panel jr-panel">
          <h2>By sector</h2>
          {summary.bySector.length === 0 ? <p className="muted">No closed trades yet.</p> : (
            <table className="jr-tbl">
              <thead><tr><th>Sector</th><th>Trades</th><th>Win rate</th><th>P&L</th></tr></thead>
              <tbody>
                {summary.bySector.map((g) => (
                  <tr key={g.key}><td>{g.key}</td><td>{g.trades}</td><td>{Math.round(g.winRate * 100)}%</td><td className={g.pnl >= 0 ? "q-ok" : "q-bad"}>{fmtINR(g.pnl)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        <section className="panel jr-panel">
          <h2>By month</h2>
          {summary.monthly.length === 0 ? <p className="muted">No closed trades yet.</p> : (
            <table className="jr-tbl">
              <thead><tr><th>Month</th><th>P&L</th><th>Return on capital</th></tr></thead>
              <tbody>
                {summary.monthly.map(([month, pnl]) => (
                  <tr key={month}>
                    <td>{new Intl.DateTimeFormat("en-IN", { month: "short", year: "numeric" }).format(new Date(`${month}-01`))}</td>
                    <td className={pnl >= 0 ? "q-ok" : "q-bad"}>{fmtINR(pnl)}</td>
                    <td className={pnl >= 0 ? "q-ok" : "q-bad"}>{settings.capital ? `${((pnl / settings.capital) * 100).toFixed(2)}%` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>

      {/* Closed trades */}
      <section className="panel jr-panel">
        <h2>Closed trades ({closed.length})</h2>
        {closed.length === 0 ? <p className="muted">Nothing closed yet.</p> : (
          <div className="table-shell">
            <table className="jr-tbl">
              <thead><tr><th>Stock</th><th>Entry → Exit</th><th>Qty</th><th>R multiple</th><th>P&L</th><th>Held</th><th>Exit</th><th></th></tr></thead>
              <tbody>
                {closed.map((t) => {
                  const m = closedMetrics(t);
                  return (
                    <tr key={t.id}>
                      <td><Link href={`/stocks/${t.symbol}`} className="stock-link">{t.symbol}</Link><div className="table-subtext">{fmtPattern(t.pattern)}</div></td>
                      <td>{fmtINR(t.entry_price)} → {fmtINR(t.exit_price ?? 0)}<div className="table-subtext">{t.entry_date} → {t.exit_date}</div></td>
                      <td>{t.qty}</td>
                      <td className={m.r === null ? "" : m.r >= 0 ? "q-ok" : "q-bad"}><strong>{m.r === null ? "—" : `${m.r >= 0 ? "+" : ""}${m.r.toFixed(2)}R`}</strong></td>
                      <td className={m.pnl >= 0 ? "q-ok" : "q-bad"}>{fmtINR(m.pnl)}</td>
                      <td>{m.holdingDays}d</td>
                      <td><span className={`pill-tag ${t.exit_reason === "target" ? "pill-tag--ok" : t.exit_reason === "stop" ? "pill-tag--bad" : "pill-tag--muted"}`}>{t.exit_reason}</span></td>
                      <td>
                        <div className="jr-row-actions">
                          <button className="mini-btn" onClick={() => reopen(t)}>Reopen</button>
                          <button className="mini-btn mini-btn--danger" onClick={() => remove(t)}>Delete</button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <style>{`
        .jr-head { display:grid; grid-template-columns:1fr 320px; gap:24px; align-items:start; margin-bottom:20px; }
        .jr-title { font-family:var(--font-space-grotesk),sans-serif; font-size:clamp(1.6rem,3.5vw,2.4rem); margin:8px 0 10px; line-height:1.1; }
        .jr-capital { background:var(--panel); border:1px solid var(--line); border-radius:20px; padding:16px; box-shadow:var(--shadow); }
        .jr-io { display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }
        .jr-hint { font-size:0.76rem; color:var(--muted); margin:8px 0 0; }
        .jr-msg { background:var(--accent-soft); color:var(--accent); border-radius:12px; padding:8px 14px; font-size:0.88rem; cursor:pointer; }
        .jr-stats { display:grid; grid-template-columns:repeat(6,1fr); gap:12px; margin-bottom:20px; }
        .jr-stat { background:var(--panel); border:1px solid var(--line); border-radius:18px; padding:14px 16px; min-width:0; }
        .jr-stat-l { display:block; font-size:0.72rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin-bottom:5px; }
        .jr-stat strong { font-family:var(--font-space-grotesk),sans-serif; font-size:1.3rem; white-space:nowrap; }
        .jr-stat-sub { display:block; font-size:0.74rem; color:var(--muted); margin-top:3px; }
        .jr-panel { margin-bottom:20px; }
        .jr-panel h2 { font-size:1.15rem; }
        .jr-panel-head { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:10px; }
        .jr-heat { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-bottom:14px; }
        .jr-tbl { width:100%; border-collapse:collapse; font-size:0.87rem; }
        .jr-tbl th { font-size:0.74rem; padding:10px; white-space:nowrap; }
        .jr-tbl td { padding:10px; vertical-align:top; }
        .jr-row-actions { display:flex; flex-wrap:wrap; gap:6px; }
        .jr-close { display:flex; flex-direction:column; gap:6px; min-width:210px; }
        .jr-close-reasons { display:flex; flex-wrap:wrap; gap:4px; }
        .jr-2col { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
        .jr-exit { min-width:220px; max-width:280px; }
        .jr-exit-msg { font-size:0.82rem; margin:4px 0; line-height:1.4; }
        .jr-exit .mini-btn { margin-top:6px; }
        .jr-risk-top { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-bottom:10px; }
        .jr-risk-top strong { font-family:var(--font-space-grotesk),sans-serif; font-size:1.25rem; }
        .jr-risk-note { font-size:0.85rem; margin:0 0 12px; }
        .jr-add { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; padding:16px; border:1px dashed var(--line); border-radius:16px; margin-bottom:16px; background:rgba(255,255,255,0.45); }
        @media (max-width:960px) {
          .jr-head, .jr-2col { grid-template-columns:1fr; }
          .jr-stats { grid-template-columns:1fr 1fr; }
          .jr-add { grid-template-columns:1fr 1fr; }
        }
      `}</style>
    </main>
  );
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "ok" | "bad" }) {
  return (
    <div className="jr-stat">
      <span className="jr-stat-l">{label}</span>
      <strong className={tone === "ok" ? "q-ok" : tone === "bad" ? "q-bad" : ""}>{value}</strong>
      {sub && <span className="jr-stat-sub">{sub}</span>}
    </div>
  );
}

function AddTradeForm({ onAdd }: { onAdd: (t: JournalTrade) => void }) {
  const [f, setF] = useState({ symbol: "", sector: "", pattern: "manual", date: todayISO(), entry: "", qty: "", stop: "", target: "" });
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => { setF({ ...f, [k]: e.target.value }); setError(null); };

  function submit() {
    const entry = Number(f.entry), qty = Math.floor(Number(f.qty)), stop = Number(f.stop), target = Number(f.target);
    if (!f.symbol.trim()) return setError("Enter a symbol.");
    if (!(entry > 0) || !(qty > 0)) return setError("Entry price and quantity must be positive.");
    if (!(stop > 0) || stop >= entry) return setError("Stop loss must be below the entry price.");
    if (!(target > entry)) return setError("Target must be above the entry price.");
    let symbol = f.symbol.trim().toUpperCase();
    if (!symbol.includes(".")) symbol += ".NS";
    onAdd({
      id: uid(), symbol, company_name: symbol, sector: f.sector.trim() || "Unclassified", pattern: f.pattern,
      status: "open", entry_date: f.date, planned_entry: entry, entry_price: entry, qty, stop_loss: stop, target_price: target,
      backtest_win_rate: null, probability_score: null,
    });
  }

  return (
    <div className="jr-add">
      <label className="field">Symbol<input value={f.symbol} onChange={set("symbol")} placeholder="e.g. CEATLTD" /></label>
      <label className="field">Sector<input value={f.sector} onChange={set("sector")} placeholder="e.g. Automobile" /></label>
      <label className="field">Pattern
        <select value={f.pattern} onChange={set("pattern")}>{PATTERNS.map((p) => <option key={p} value={p}>{fmtPattern(p)}</option>)}</select>
      </label>
      <label className="field">Entry date<input type="date" value={f.date} onChange={set("date")} /></label>
      <label className="field">Entry price<input type="number" value={f.entry} onChange={set("entry")} /></label>
      <label className="field">Quantity<input type="number" value={f.qty} onChange={set("qty")} /></label>
      <label className="field">Stop loss<input type="number" value={f.stop} onChange={set("stop")} /></label>
      <label className="field">Target<input type="number" value={f.target} onChange={set("target")} /></label>
      <div style={{ gridColumn: "1 / -1", display: "flex", alignItems: "center", gap: 12 }}>
        <button className="primary-button" style={{ padding: "9px 20px" }} onClick={submit}>Add position</button>
        {error && <span className="q-bad" style={{ fontSize: "0.85rem" }}>{error}</span>}
      </div>
    </div>
  );
}
