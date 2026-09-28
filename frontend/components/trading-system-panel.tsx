"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { getLatestSignals, getScanStatus, runScan } from "../lib/api";
import {
  MAX_OPEN_POSITIONS,
  MAX_PER_SECTOR,
  fmtINR,
  fmtPattern,
  openSectorCounts,
  positionQty,
  tradeFromSetup,
  useJournal,
  useTradingSettings,
  useWatchlist,
  watchFromSetup,
} from "../lib/store";
import { exportWorkbook, regimeRows, setupRow } from "../lib/excel";
import { ScanUniverse, TradeSetup } from "../types";
import { GATE_LABEL, GateReason, QualityBadges, gateFailures } from "./quality-badges";
import { RegimeBanner, useMarketRegime } from "./regime-banner";

type Tab = "picks" | "gated" | "rules" | "risk";
type ScanPhase = "idle" | "scanning" | "polling" | "done" | "error";

const UNIVERSE_OPTIONS: { value: ScanUniverse; label: string; stocks: string }[] = [
  { value: "nifty500", label: "Nifty 500", stocks: "~500 stocks" },
  { value: "nifty_smallcap_250", label: "Smallcap 250", stocks: "~250 stocks" },
  { value: "mid_small_2000_plus", label: "Mid & Small 2000+", stocks: "2000+ stocks" },
];

const BASE = { prob: 0.65, rr: 2.5, size: 1 };

function fmtDate(s: string) {
  return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short" }).format(new Date(s));
}

export function TradingSystemPanel() {
  const [activeTab, setActiveTab] = useState<Tab>("picks");
  const [settings, setSettings] = useTradingSettings();
  const [journal, setJournal] = useJournal();
  const [watchlist, setWatchlist] = useWatchlist();
  const { regime, loading: regimeLoading, error: regimeError, reload } = useMarketRegime();

  const [universe, setUniverse] = useState<ScanUniverse>("nifty500");
  const [maxResults, setMaxResults] = useState(10);
  const [useRegime, setUseRegime] = useState(true);
  const [strictGates, setStrictGates] = useState(true);

  const [phase, setPhase] = useState<ScanPhase>("idle");
  const [raw, setRaw] = useState<TradeSetup[]>([]);
  const [progress, setProgress] = useState({ scanned: 0, total: 0 });
  const [statusMsg, setStatusMsg] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [rejected, setRejected] = useState<{ count: number; examples: string[] }>({ count: 0, examples: [] });
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 2600);
    return () => clearTimeout(id);
  }, [toast]);

  const thresholds = useRegime && regime
    ? { prob: regime.recommended_min_probability, rr: regime.recommended_min_risk_reward, size: regime.position_size_multiplier }
    : BASE;

  // Portfolio state from the journal
  const openTrades = useMemo(() => journal.filter((t) => t.status === "open"), [journal]);
  const heldSymbols = useMemo(() => new Set(openTrades.map((t) => t.symbol)), [openTrades]);
  const sectorCounts = useMemo(() => openSectorCounts(journal), [journal]);
  const slotsLeft = Math.max(0, MAX_OPEN_POSITIONS - openTrades.length);
  const watchedSymbols = new Set(watchlist.map((w) => w.symbol));

  // Threshold + gate filtering happens client-side so toggles apply instantly
  const { picks, gated } = useMemo(() => {
    const eligible = raw.filter((t) => t.probability_score >= thresholds.prob && t.risk_reward_ratio >= thresholds.rr);
    const passed: TradeSetup[] = [];
    const failed: { setup: TradeSetup; reasons: GateReason[] }[] = [];
    eligible.forEach((setup) => {
      const reasons = gateFailures(setup, heldSymbols, sectorCounts, MAX_PER_SECTOR);
      if (strictGates && reasons.length) failed.push({ setup, reasons });
      else passed.push(setup);
    });
    return { picks: passed.slice(0, maxResults), gated: failed };
  }, [raw, thresholds.prob, thresholds.rr, strictGates, heldSymbols, sectorCounts, maxResults]);

  function stopPolling() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }

  function finish(results: TradeSetup[]) {
    setRaw(results);
    setPhase("done");
    setStatusMsg(`Scan complete — ${results.length} candidate${results.length === 1 ? "" : "s"} returned before system filters.`);
    getScanStatus()
      .then((s) => setRejected({ count: s.data_rejected ?? 0, examples: s.data_rejected_examples ?? [] }))
      .catch(() => undefined);
  }

  async function pollStatus() {
    try {
      const s = await getScanStatus();
      setProgress({ scanned: s.scanned_symbols ?? 0, total: s.universe_size ?? 0 });
      if (!s.scan_in_progress) {
        stopPolling();
        finish(await getLatestSignals(universe));
        void reload(true); // breadth from the fresh scan
      }
    } catch { /* keep polling */ }
  }

  async function handleScan() {
    stopPolling();
    setPhase("scanning");
    setRaw([]);
    setProgress({ scanned: 0, total: 0 });
    setStatusMsg("");
    setActiveTab("picks");
    try {
      const data = await runScan({
        universe,
        // Pull a wider pool than we show so gates still leave enough picks.
        max_results: Math.min(40, maxResults * 3),
        min_probability: thresholds.prob,
        min_risk_reward: thresholds.rr,
        investment_amount: settings.capital,
      });
      if (!data.scan_in_progress) {
        finish(data.results ?? []);
        void reload(true);
        return;
      }
      setPhase("polling");
      setStatusMsg("Scan running in background — checking every 5 seconds…");
      pollRef.current = setInterval(() => { void pollStatus(); }, 5000);
      setTimeout(() => { void pollStatus(); }, 3000);
    } catch (e: unknown) {
      setPhase("error");
      setStatusMsg(e instanceof Error ? e.message : "Could not reach the backend.");
    }
  }

  const qtyFor = (s: TradeSetup) => positionQty(s.entry_price, s.stop_loss, settings, thresholds.size);

  function logTrade(s: TradeSetup) {
    if (heldSymbols.has(s.symbol)) { setToast(`${s.symbol} is already in your open positions`); return; }
    setJournal((prev) => [tradeFromSetup(s, qtyFor(s)), ...prev]);
    setToast(`${s.symbol} logged — update the actual fill price in the Journal`);
  }
  function watch(s: TradeSetup) {
    if (watchedSymbols.has(s.symbol)) { setToast(`${s.symbol} is already on your watchlist`); return; }
    setWatchlist((prev) => [watchFromSetup(s), ...prev]);
    setToast(`${s.symbol} added to watchlist for 7 days`);
  }

  // Only the picks that fit your free position slots count towards deployment.
  const actionable = picks.slice(0, slotsLeft);
  const totalDeployed = actionable.reduce((a, r) => a + qtyFor(r) * r.entry_price, 0);
  const totalProfit = actionable.reduce((a, r) => a + qtyFor(r) * (r.target_price - r.entry_price), 0);
  const totalRisk = actionable.reduce((a, r) => a + qtyFor(r) * (r.entry_price - r.stop_loss), 0);
  const avgConf = actionable.length ? Math.round(actionable.reduce((a, r) => a + r.probability_score, 0) / actionable.length * 100) : 0;
  async function exportExcel() {
    const pickRows = picks.map((r, i) => {
      const q = qtyFor(r);
      return setupRow(r, {
        Rank: i + 1,
        Status: i < slotsLeft ? "Actionable" : "Backup",
        "Qty": q,
        "Deploy (₹)": Math.round(q * r.entry_price),
        "Risk at stop (₹)": Math.round(q * (r.entry_price - r.stop_loss)),
        "Profit at target (₹)": Math.round(q * (r.target_price - r.entry_price)),
      });
    });
    const gatedRows = gated.map(({ setup, reasons }) =>
      setupRow(setup, { "Blocked because": reasons.map((g) => GATE_LABEL[g]).join("; ") })
    );
    await exportWorkbook("system-picks", [
      { name: "Trade picks", rows: pickRows },
      { name: "Blocked by gates", rows: gatedRows },
      { name: "Market regime", rows: regimeRows(regime) },
      {
        name: "Settings",
        rows: [
          { Setting: "Universe", Value: UNIVERSE_OPTIONS.find((o) => o.value === universe)?.label ?? universe },
          { Setting: "Capital (₹)", Value: settings.capital },
          { Setting: "Risk per trade %", Value: settings.riskPct },
          { Setting: "Min confidence %", Value: Math.round(thresholds.prob * 100) },
          { Setting: "Min R:R", Value: thresholds.rr },
          { Setting: "Position size %", Value: Math.round(thresholds.size * 100) },
          { Setting: "Regime-adjusted", Value: useRegime ? "Yes" : "No" },
          { Setting: "Strict quality gates", Value: strictGates ? "Yes" : "No" },
          { Setting: "Open positions", Value: openTrades.length },
        ],
      },
    ]);
  }

  const progressPct = progress.total > 0 ? Math.min(100, Math.round(progress.scanned / progress.total * 100)) : 0;
  const isRunning = phase === "scanning" || phase === "polling";

  return (
    <section className="panel ts-panel">
      <div className="ts-header">
        <h2 style={{ margin: "0 0 4px" }}>System trading algo</h2>
        <p className="ts-sub">
          Market regime first, then the 4-signal filter, then hard quality gates (earnings blackout, weekly trend,
          fundamentals, your open positions). Only setups that survive all three layers become picks.
        </p>
      </div>

      <RegimeBanner regime={regime} loading={regimeLoading} error={regimeError} onRefresh={() => void reload(true)} compact />

      {/* Controls */}
      <div className="ts-controls">
        <div className="ts-cg">
          <label className="ts-label">Capital (₹)</label>
          <input className="ts-input" type="number" min={100000} step={100000} value={settings.capital}
            onChange={(e) => setSettings({ ...settings, capital: Number(e.target.value) })} />
        </div>
        <div className="ts-cg">
          <label className="ts-label">Risk per trade (%)</label>
          <input className="ts-input" type="number" min={0.25} max={3} step={0.25} value={settings.riskPct}
            onChange={(e) => setSettings({ ...settings, riskPct: Number(e.target.value) })} />
        </div>
        <div className="ts-cg">
          <label className="ts-label">Universe</label>
          <select className="ts-input" value={universe} onChange={(e) => setUniverse(e.target.value as ScanUniverse)}>
            {UNIVERSE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label} ({o.stocks})</option>)}
          </select>
        </div>
        <div className="ts-cg">
          <label className="ts-label">Max picks</label>
          <input className="ts-input" type="number" min={3} max={20} step={1} value={maxResults}
            onChange={(e) => setMaxResults(Number(e.target.value))} />
        </div>
        <button className="primary-button ts-run-btn" onClick={() => void handleScan()} disabled={isRunning}>
          {phase === "scanning" ? "Starting scan…" : phase === "polling" ? "Scanning…" : "▶ Run system scan"}
        </button>
      </div>

      <div className="ts-toggles">
        <label className="ts-toggle">
          <input type="checkbox" checked={useRegime} onChange={(e) => setUseRegime(e.target.checked)} />
          Regime-adjusted thresholds
          <span className="ts-toggle-val">
            {Math.round(thresholds.prob * 100)}% confidence · {thresholds.rr}× R:R · {Math.round(thresholds.size * 100)}% size
          </span>
        </label>
        <label className="ts-toggle">
          <input type="checkbox" checked={strictGates} onChange={(e) => setStrictGates(e.target.checked)} />
          Strict quality gates
        </label>
        <span className="ts-portfolio">
          Open positions {openTrades.length}/{MAX_OPEN_POSITIONS}
          {slotsLeft === 0 ? " — portfolio full, close a trade before adding" : ` — room for ${slotsLeft} more`}
        </span>
      </div>

      {phase === "polling" && (
        <div className="ts-prog-outer">
          <div className="ts-prog-track">
            <div className="ts-prog-fill" style={{ width: progressPct > 0 ? `${progressPct}%` : "40%" }} />
          </div>
          <span className="ts-prog-text">
            {progress.total > 0 ? `Scanned ${progress.scanned} / ${progress.total} stocks (${progressPct}%)` : "Scan running in background…"}
          </span>
        </div>
      )}
      {statusMsg && <p className={phase === "error" ? "ts-error" : "ts-notice"}>{statusMsg}</p>}
      {phase === "done" && rejected.count > 0 && (
        <details className="ts-dq">
          <summary>{rejected.count} stock{rejected.count === 1 ? "" : "s"} skipped for stale or suspect price data</summary>
          <ul>{rejected.examples.map((e) => <li key={e}>{e}</li>)}</ul>
        </details>
      )}

      {phase === "done" && picks.length > 0 && (
        <div className="ts-summary">
          <div className="ts-stat">
            <span className="ts-stat-l">Actionable now</span>
            <strong>{actionable.length} of {picks.length}</strong>
            <span className="ts-stat-sub">{slotsLeft === 0 ? "No free slots" : `Fills your ${slotsLeft} free slot${slotsLeft === 1 ? "" : "s"}`}</span>
          </div>
          <div className="ts-stat">
            <span className="ts-stat-l">Capital to deploy</span>
            <strong>{fmtINR(totalDeployed)}</strong>
            <span className="ts-stat-sub">{settings.capital ? Math.round(totalDeployed / settings.capital * 100) : 0}% of capital</span>
          </div>
          <div className="ts-stat">
            <span className="ts-stat-l">Combined risk at stops</span>
            <strong className="q-bad">{fmtINR(totalRisk)}</strong>
            <span className="ts-stat-sub">{settings.capital ? (totalRisk / settings.capital * 100).toFixed(1) : 0}% of capital</span>
          </div>
          <div className="ts-stat ts-stat--g">
            <span className="ts-stat-l">Profit if all targets hit</span>
            <strong>{fmtINR(totalProfit)}</strong>
            <span className="ts-stat-sub">Avg confidence {avgConf}%</span>
          </div>
        </div>
      )}

      <div className="ts-tabs">
        <button className={`ts-tab ${activeTab === "picks" ? "ts-tab--on" : ""}`} onClick={() => setActiveTab("picks")}>
          Trade picks{phase === "done" ? ` (${picks.length})` : ""}
        </button>
        <button className={`ts-tab ${activeTab === "gated" ? "ts-tab--on" : ""}`} onClick={() => setActiveTab("gated")}>
          Blocked by gates{phase === "done" ? ` (${gated.length})` : ""}
        </button>
        <button className={`ts-tab ${activeTab === "rules" ? "ts-tab--on" : ""}`} onClick={() => setActiveTab("rules")}>Signal rules</button>
        <button className={`ts-tab ${activeTab === "risk" ? "ts-tab--on" : ""}`} onClick={() => setActiveTab("risk")}>Risk limits</button>
        {phase === "done" && (picks.length > 0 || gated.length > 0) && (
          <button className="mini-btn ts-export" onClick={() => void exportExcel()}>Export to Excel</button>
        )}
      </div>

      {activeTab === "picks" && (
        <div className="ts-fade">
          {phase === "idle" && (
            <div className="empty-state"><p>Check the regime above, then click <strong>▶ Run system scan</strong>.</p></div>
          )}
          {isRunning && (
            <div className="empty-state"><p>Scanning {UNIVERSE_OPTIONS.find((o) => o.value === universe)?.stocks} — results appear automatically when complete.</p></div>
          )}
          {phase === "error" && <div className="empty-state"><p>Could not complete the scan. Check the backend and retry.</p></div>}
          {phase === "done" && picks.length === 0 && (
            <div className="empty-state">
              <p>No setups passed every layer right now.</p>
              <p className="ts-sub" style={{ marginTop: 6 }}>
                {regime?.regime === "bear"
                  ? "The market regime is risk-off — sitting in cash is a position. This is the system working as intended."
                  : gated.length
                    ? `${gated.length} setup${gated.length === 1 ? " was" : "s were"} blocked by quality gates — see the "Blocked by gates" tab.`
                    : "Try another universe, or rerun after the next session."}
              </p>
            </div>
          )}
          {phase === "done" && picks.length > 0 && (
            <div className="table-shell">
              <table className="ts-tbl">
                <thead>
                  <tr>
                    <th>Stock</th><th>Pattern</th><th>Entry / CMP</th><th>Stop</th><th>Target</th>
                    <th>R:R</th><th>Confidence</th><th>Qty</th><th>Deploy</th><th>Profit at target</th><th>By</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {picks.map((r, i) => {
                    const q = qtyFor(r);
                    const backup = i >= slotsLeft;
                    const slPct = Math.abs((r.entry_price - r.stop_loss) / r.entry_price * 100);
                    return (
                      <tr key={r.symbol} className={`${i === 0 && !backup ? "ts-top" : ""} ${backup ? "ts-backup" : ""}`}>
                        <td>
                          <Link href={`/stocks/${r.symbol}`} className="stock-link">{i === 0 && !backup ? "★ " : ""}{r.symbol}</Link>
                          {backup && <div className="table-subtext">Backup — use if an earlier pick doesn&apos;t trigger</div>}
                          <div className="table-subtext">{r.company_name} · {r.sector}</div>
                          <QualityBadges setup={r} />
                          {!strictGates && gateFailures(r, heldSymbols, sectorCounts, MAX_PER_SECTOR).length > 0 && (
                            <div className="table-subtext q-bad">
                              ⚠ {gateFailures(r, heldSymbols, sectorCounts, MAX_PER_SECTOR).map((g) => GATE_LABEL[g]).join(", ")}
                            </div>
                          )}
                        </td>
                        <td>{fmtPattern(r.pattern)}<div className="table-subtext">RS {Math.round(r.relative_strength.score * 100)}</div></td>
                        <td><strong>{fmtINR(r.entry_price)}</strong><div className="table-subtext">CMP {fmtINR(r.current_price)}</div></td>
                        <td><span className="q-bad">{fmtINR(r.stop_loss)}</span><div className="table-subtext">−{slPct.toFixed(1)}%</div></td>
                        <td><span className="q-ok">{fmtINR(r.target_price)}</span><div className="table-subtext">+{r.expected_return_pct.toFixed(1)}%</div></td>
                        <td><strong>{r.risk_reward_ratio.toFixed(1)}×</strong></td>
                        <td>
                          <div className="ts-pb-row">
                            <div className="ts-pb"><div className="ts-pb-fill" style={{ width: Math.round(r.probability_score * 100) + "%" }} /></div>
                            <span>{Math.round(r.probability_score * 100)}%</span>
                          </div>
                          {r.historical_win_rate != null && (
                            <div className="table-subtext ts-hist" title="How often setups with a similar score actually won, from backtest and live results">
                              Historically won {Math.round(r.historical_win_rate * 100)}%
                            </div>
                          )}
                          {r.backtest.total_trades > 0 && (
                            <div className="table-subtext">
                              This stock: {Math.round(r.backtest.win_rate * 100)}% win
                              {r.backtest.average_r !== undefined ? ` · ${r.backtest.average_r >= 0 ? "+" : ""}${r.backtest.average_r.toFixed(2)}R` : ""} ({r.backtest.total_trades})
                            </div>
                          )}
                        </td>
                        <td style={{ fontWeight: 600 }}>{q > 0 ? q : "—"}</td>
                        <td>{q > 0 ? fmtINR(q * r.entry_price) : "—"}</td>
                        <td className="q-ok" style={{ fontWeight: 600 }}>{q > 0 ? `+${fmtINR(q * (r.target_price - r.entry_price))}` : "—"}</td>
                        <td>{fmtDate(r.estimated_target_date)}<div className="table-subtext">{r.estimated_target_sessions} sessions</div></td>
                        <td>
                          <div className="ts-actions">
                            <button className="mini-btn mini-btn--primary" onClick={() => logTrade(r)}
                              disabled={slotsLeft === 0 || heldSymbols.has(r.symbol)}>
                              {heldSymbols.has(r.symbol) ? "Held" : "Log trade"}
                            </button>
                            <button className="mini-btn" onClick={() => watch(r)} disabled={watchedSymbols.has(r.symbol)}>
                              {watchedSymbols.has(r.symbol) ? "Watching" : "Watch"}
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {activeTab === "gated" && (
        <div className="ts-fade">
          {!strictGates ? (
            <div className="empty-state"><p>Strict quality gates are off — blocked setups are shown in the picks list with a ⚠ warning.</p></div>
          ) : gated.length === 0 ? (
            <div className="empty-state"><p>{phase === "done" ? "Nothing was blocked — every eligible setup passed the gates." : "Run a scan to see which setups the gates block."}</p></div>
          ) : (
            <div className="table-shell">
              <table className="ts-tbl">
                <thead><tr><th>Stock</th><th>Pattern</th><th>Confidence</th><th>Blocked because</th><th></th></tr></thead>
                <tbody>
                  {gated.map(({ setup, reasons }) => (
                    <tr key={setup.symbol}>
                      <td>
                        <Link href={`/stocks/${setup.symbol}`} className="stock-link">{setup.symbol}</Link>
                        <div className="table-subtext">{setup.sector}</div>
                      </td>
                      <td>{fmtPattern(setup.pattern)}</td>
                      <td>{Math.round(setup.probability_score * 100)}%</td>
                      <td>
                        {reasons.map((g) => <div key={g} className="q-bad" style={{ fontSize: "0.85rem" }}>• {GATE_LABEL[g]}</div>)}
                        {setup.peer_rank?.sector_laggard && setup.peer_rank.top_peers.length > 0 && (
                          <div className="table-subtext">Stronger peers: {setup.peer_rank.top_peers.join(", ")}</div>
                        )}
                      </td>
                      <td>
                        <button className="mini-btn" onClick={() => watch(setup)} disabled={watchedSymbols.has(setup.symbol)}>
                          {watchedSymbols.has(setup.symbol) ? "Watching" : "Watch"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {activeTab === "rules" && (
        <div className="ts-fade">
          <p className="ts-intro">Every scan applies these layers in order. Layers 1–4 score the setup; layer 5 decides whether it is tradeable at all.</p>
          {([
            { n: "0", title: "Market regime", badge: "Sets thresholds", col: "amber", rules: [
              { m: "Nifty vs 50 / 200 EMA, 20-day return", s: "Is the index itself in an uptrend?" },
              { m: "Breadth: % of stocks above their 50 EMA", s: "Is the rally broad or carried by a few names?" },
              { m: "India VIX", s: "High fear means gaps that ignore stop losses" },
            ]},
            { n: "1", title: "Trend & structure", badge: "Scored", col: "green", rules: [
              { m: "EMA 20 > 50 > 200 on the daily chart", s: "Bullish structure — no buys in downtrends" },
              { m: "Weekly chart: above 20-week EMA, weekly RSI > 50, rising volume", s: "Needs 2 of 3 — daily breakouts against the weekly trend fail" },
              { m: "Near or at a 52-week high", s: "Leaders make new highs; price discovery earns a bonus" },
            ]},
            { n: "2", title: "Momentum & relative strength", badge: "Scored", col: "green", rules: [
              { m: "RSI 14 in the momentum zone", s: "Bullish, not overbought" },
              { m: "Outperforming Nifty over 20 / 50 / 120 sessions", s: "Relative strength score" },
              { m: "Top quartile inside its own sector", s: "Buy the strongest horse, not the average one" },
            ]},
            { n: "3", title: "Volume & accumulation", badge: "Scored", col: "green", rules: [
              { m: "Volume above the 20-day average on the trigger", s: "Breakouts need conviction" },
              { m: "NSE delivery % and up/down volume", s: "Real buyers, not intraday churn" },
            ]},
            { n: "4", title: "Price structure & R:R", badge: "Threshold", col: "green", rules: [
              { m: "Defined trigger, stop below structure — max 8% below entry", s: "Setups needing a wider stop are skipped: their targets aren't reachable in a swing window" },
              { m: `Minimum ${thresholds.rr}× risk:reward (regime-adjusted)`, s: "Winners must pay for the losers" },
            ]},
            { n: "5", title: "Hard quality gates", badge: "Pass / fail", col: "amber", rules: [
              { m: "No results within 5 days (or 2 days after)", s: "Earnings gaps blow through stops" },
              { m: "Fundamentals not failing (growth, margin, debt, ROE)", s: "Avoid sinking ships with good charts" },
              { m: `Max ${MAX_OPEN_POSITIONS} open positions, ${MAX_PER_SECTOR} per sector`, s: "Read live from your Journal" },
            ]},
          ] as const).map((b) => (
            <div key={b.n} className="ts-sb">
              <div className="ts-sh">
                <span className="ts-st">{b.n}. {b.title}</span>
                <span className={`ts-badge ts-badge--${b.col}`}>{b.badge}</span>
              </div>
              {b.rules.map((r, i) => (
                <div key={i} className="ts-rr">
                  <span className="ts-arr">→</span>
                  <div><div className="ts-rm">{r.m}</div><div className="ts-rs">{r.s}</div></div>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {activeTab === "risk" && (
        <div className="ts-fade">
          {([
            { title: "Daily limits", rules: [
              { n: "1", m: `Stop trading if down ${fmtINR(settings.capital * 0.015)} in a day`, s: "1.5% of capital. Log the reason, review next morning." },
              { n: "2", m: "Max 3 new entries per day", s: "Overtrading dilutes your best setups." },
            ]},
            { title: "Weekly limits", rules: [
              { n: "1", m: `Weekly drawdown limit: ${fmtINR(settings.capital * 0.03)}`, s: "Hit it → 2-day break." },
              { n: "2", m: "Review the Journal every Sunday", s: "Compare real win rate by pattern to the backtest." },
            ]},
            { title: "Portfolio limits", rules: [
              { n: "1", m: `Max ${MAX_OPEN_POSITIONS} open positions, max 20% of capital each`, s: "Enforced by the gates above." },
              { n: "2", m: `Max ${MAX_PER_SECTOR} positions per sector`, s: "Correlated positions are hidden concentration." },
              { n: "3", m: `Monthly drawdown cap: ${fmtINR(settings.capital * 0.06)}`, s: "Hit it → paper trade for 2 weeks." },
            ]},
            { title: "Regime rule", rules: [
              { n: "→", m: "Risk-on: full size · Selective: 75% size · Risk-off: 50% size, A+ setups only", s: "Applied automatically to the quantities above." },
            ]},
          ] as const).map((b) => (
            <div key={b.title} className="ts-rb">
              <p className="ts-rt">{b.title}</p>
              <div className="ts-rr-wrap">
                {b.rules.map((r) => (
                  <div key={r.m} className="ts-rr ts-rr-pad">
                    <span className="ts-arr">{r.n}</span>
                    <div><div className="ts-rm">{r.m}</div><div className="ts-rs">{r.s}</div></div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {toast && <div className="toast">{toast}</div>}

      <style>{`
        .ts-panel { margin-top: 24px; }
        .ts-header { margin-bottom: 18px; }
        .ts-sub { color: var(--muted); font-size: 0.88rem; line-height: 1.6; margin: 0; max-width: 80ch; }
        .ts-controls { display:flex; flex-wrap:wrap; align-items:flex-end; gap:14px; padding:18px; background:rgba(255,255,255,0.52); border:1px solid var(--line); border-radius:18px; margin-bottom:12px; }
        .ts-cg { display:flex; flex-direction:column; gap:6px; }
        .ts-label { font-size:0.82rem; color:var(--muted); }
        .ts-input { border:1px solid var(--line); border-radius:12px; background:rgba(255,255,255,0.8); color:var(--text); padding:9px 12px; font:inherit; width:150px; }
        select.ts-input { width:200px; }
        .ts-run-btn { align-self:flex-end; padding:10px 24px; font-size:0.95rem; min-width:164px; }
        .ts-toggles { display:flex; flex-wrap:wrap; gap:10px 22px; align-items:center; margin-bottom:14px; font-size:0.86rem; }
        .ts-toggle { display:inline-flex; align-items:center; gap:8px; cursor:pointer; font-weight:600; }
        .ts-toggle input { accent-color: var(--accent); width:16px; height:16px; }
        .ts-toggle-val { font-weight:400; color:var(--muted); }
        .ts-portfolio { color:var(--muted); }
        .ts-prog-outer { margin-bottom:14px; }
        .ts-prog-track { height:6px; background:var(--bg-deep); border-radius:3px; overflow:hidden; margin-bottom:6px; }
        .ts-prog-fill { height:100%; background:linear-gradient(90deg,#f16800,#ff9a2f); border-radius:3px; transition:width .4s ease; }
        .ts-prog-text { font-size:0.82rem; color:var(--muted); }
        .ts-notice { color:var(--muted); font-size:0.88rem; margin:0 0 14px; }
        .ts-error { color:var(--red); font-size:0.88rem; margin:0 0 14px; }
        .ts-summary { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:18px; }
        .ts-stat { background:rgba(255,255,255,0.58); border:1px solid var(--line); border-radius:16px; padding:14px 16px; }
        .ts-stat--g { background:rgba(22,108,90,0.06); border-color:rgba(22,108,90,0.18); }
        .ts-stat-l { display:block; font-size:0.76rem; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; margin-bottom:5px; }
        .ts-stat strong { font-size:1.3rem; font-family:var(--font-space-grotesk),sans-serif; }
        .ts-stat--g strong { color:var(--green); }
        .ts-stat-sub { display:block; font-size:0.76rem; color:var(--muted); margin-top:3px; }
        .ts-backup td { opacity:0.62; }
        .ts-hist { color:var(--blue); font-weight:600; }
        .ts-dq { font-size:0.84rem; color:var(--muted); margin:-6px 0 14px; }
        .ts-dq summary { cursor:pointer; }
        .ts-dq ul { margin:6px 0 0; padding-left:18px; }
        .ts-backup td:last-child { opacity:1; }
        .ts-tabs { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:18px; }
        .ts-tab { padding:8px 18px; border-radius:999px; border:1px solid var(--line); background:rgba(255,255,255,0.55); color:var(--muted); font-size:0.88rem; font-weight:500; cursor:pointer; }
        .ts-tab:hover { border-color:rgba(241,104,0,0.28); color:var(--text); }
        .ts-export { margin-left:auto; align-self:center; }
        .ts-tab--on { background:linear-gradient(135deg,#f16800,#ff9a2f); color:#fff7ef; border-color:transparent; }
        .ts-fade { animation: ts-in 180ms ease; }
        @keyframes ts-in { from{opacity:0;transform:translateY(5px)} to{opacity:1;transform:translateY(0)} }
        .ts-tbl { width:100%; border-collapse:collapse; font-size:0.87rem; }
        .ts-tbl th { color:var(--muted); font-size:0.74rem; text-transform:uppercase; letter-spacing:.08em; padding:10px; border-bottom:1px solid var(--line); text-align:left; font-weight:500; white-space:nowrap; }
        .ts-tbl td { padding:11px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
        .ts-tbl tr:last-child td { border-bottom:none; }
        .ts-top td { background:rgba(255,122,0,0.04); }
        .ts-actions { display:flex; flex-direction:column; gap:6px; }
        .ts-pb-row { display:flex; align-items:center; gap:6px; font-size:0.85rem; }
        .ts-pb { flex:1; height:5px; background:var(--bg-deep); border-radius:3px; min-width:50px; overflow:hidden; }
        .ts-pb-fill { height:100%; background:linear-gradient(90deg,#f16800,#ff9a2f); border-radius:3px; }
        .ts-intro { color:var(--muted); font-size:0.88rem; line-height:1.6; margin:0 0 16px; }
        .ts-sb { background:rgba(255,255,255,0.52); border:1px solid var(--line); border-radius:16px; padding:14px 16px; margin-bottom:12px; }
        .ts-sh { display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; }
        .ts-st { font-weight:600; font-size:0.94rem; }
        .ts-badge { font-size:0.74rem; font-weight:600; padding:3px 10px; border-radius:999px; }
        .ts-badge--green { background:rgba(22,108,90,0.1); color:var(--green); }
        .ts-badge--amber { background:rgba(180,120,0,0.1); color:#8a5c00; }
        .ts-rr { display:flex; gap:10px; padding:7px 0; border-bottom:1px solid var(--line); }
        .ts-rr:last-child { border-bottom:none; padding-bottom:0; }
        .ts-rr-pad { padding:12px 14px; }
        .ts-arr { color:var(--accent); font-weight:700; font-size:0.8rem; padding-top:2px; min-width:18px; }
        .ts-rm { font-size:0.9rem; font-weight:500; }
        .ts-rs { font-size:0.82rem; color:var(--muted); margin-top:2px; }
        .ts-rb { margin-bottom:16px; }
        .ts-rt { font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:.1em; color:var(--muted); margin:0 0 8px; }
        .ts-rr-wrap { background:rgba(255,255,255,0.52); border:1px solid var(--line); border-radius:16px; overflow:hidden; }
        @media (max-width:960px) {
          .ts-summary { grid-template-columns:1fr 1fr; }
          .ts-controls { flex-direction:column; align-items:stretch; }
          .ts-input { width:100%; }
        }
      `}</style>
    </section>
  );
}
