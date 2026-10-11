"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { getAlgo, getAlgoBacktest, getAlgoBacktestStatus, startAlgoBacktest } from "../lib/api";
import { exportWorkbook } from "../lib/excel";
import {
  JournalTrade,
  MAX_OPEN_POSITIONS,
  WATCH_DAYS,
  fmtINR,
  lossStreak,
  positionQty,
  todayISO,
  uid,
  useJournal,
  useTradingSettings,
  useWatchlist,
} from "../lib/store";
import { AlgoBacktest, AlgoLiveResponse, AlgoTrade, ScanUniverse } from "../types";
import { AppNav } from "./app-nav";

const UNIVERSES: { value: ScanUniverse; label: string }[] = [
  { value: "nifty500", label: "Nifty 500" },
  { value: "nifty_smallcap_250", label: "Smallcap 250" },
];
const EXCLUDE_KEY = "swing.algo.exclude.v1";

export function AlgoShell() {
  const [universe, setUniverse] = useState<ScanUniverse>("nifty500");
  const [data, setData] = useState<AlgoLiveResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [excludeText, setExcludeText] = useState("");
  const [journal, setJournal] = useJournal();
  const [watchlist, setWatchlist] = useWatchlist();
  const [settings] = useTradingSettings();
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    try { setExcludeText(window.localStorage.getItem(EXCLUDE_KEY) ?? ""); } catch { /* ignore */ }
  }, []);
  const excludeList = excludeText.split(/[\s,]+/).map((x) => x.trim().toUpperCase().replace(/\.NS$/, "")).filter(Boolean);

  async function run(refresh = false) {
    setLoading(true);
    setError(null);
    try {
      window.localStorage.setItem(EXCLUDE_KEY, excludeText);
    } catch { /* ignore */ }
    try {
      setData(await getAlgo(universe, excludeList, refresh));
    } catch {
      setError("The algo couldn't run. Check that the backend is up and try again.");
    } finally {
      setLoading(false);
    }
  }

  // Streak rule from your real journal
  const { streak, lastLossDate } = lossStreak(journal);
  const daysSinceLoss = lastLossDate ? Math.floor((Date.now() - new Date(lastLossDate).getTime()) / 86400000) : null;
  const paused = streak >= 5 && daysSinceLoss !== null && daysSinceLoss < 14;   // ~10 sessions
  const sizeFactor = streak >= 3 ? 0.5 : 1;
  const openTrades = journal.filter((t) => t.status === "open");
  const slots = Math.max(0, MAX_OPEN_POSITIONS - openTrades.length);

  function logTrade(t: AlgoTrade) {
    const qty = positionQty(t.close, t.stop, settings, sizeFactor);
    const trade: JournalTrade = {
      id: uid(), symbol: t.symbol, company_name: t.company_name, sector: t.sector,
      pattern: t.setup === "base breakout" ? "volatility_contraction" : "ema_pullback", status: "open",
      entry_date: todayISO(), planned_entry: t.close, entry_price: t.close, qty, stop_loss: t.stop,
      target_price: t.partial_target, initial_stop: t.stop, backtest_win_rate: null, probability_score: null,
      exit_profile: "curated",
    };
    setJournal((prev) => [trade, ...prev]);
    setNotice(`Logged ${qty} ${t.symbol.replace(/\.NS$/, "")} with the Algo's exit rules — confirm tomorrow's opening fill in the Journal.`);
  }
  function watch(t: AlgoTrade) {
    const now = new Date();
    setWatchlist((prev) => [{
      id: uid(), symbol: t.symbol, company_name: t.company_name, sector: t.sector, pattern: "ema_pullback",
      trigger_price: t.close, stop_loss: t.stop, target_price: t.partial_target,
      added_at: now.toISOString(), expires_at: new Date(now.getTime() + WATCH_DAYS * 86400000).toISOString(), last_close: t.close,
    }, ...prev]);
    setNotice(`${t.symbol.replace(/\.NS$/, "")} added to the watchlist.`);
  }

  const held = new Set(openTrades.map((t) => t.symbol));
  const watched = new Set(watchlist.map((w) => w.symbol));

  return (
    <main className="page-shell">
      <AppNav />

      <section className="al-head">
        <div>
          <p className="eyebrow">Curated system</p>
          <h1 className="al-title">Five layers. Every trade must pass all of them.</h1>
          <ol className="al-layers">
            <li><b>Market regime</b> — index above 50 DMA above 200 DMA, VIX under 20, breadth over 50%</li>
            <li><b>Quality</b> — ₹10 Cr+ traded value, price over ₹50, no circuits or ASM/GSM, growing earnings</li>
            <li><b>Leadership</b> — top 20% RS, top-5 sector, within 15% of the 52-week high</li>
            <li><b>Setup</b> — trend pullback or base breakout</li>
            <li><b>Confirmation</b> — at least 4 of 5 points</li>
          </ol>
        </div>
        <div className="al-controls panel">
          <label className="field">Universe
            <select value={universe} onChange={(e) => setUniverse(e.target.value as ScanUniverse)}>
              {UNIVERSES.map((u) => <option key={u.value} value={u.value}>{u.label}</option>)}
            </select>
          </label>
          <label className="field">Skip these stocks (ASM/GSM list or your own)
            <textarea rows={2} value={excludeText} onChange={(e) => setExcludeText(e.target.value)}
              placeholder="e.g. ABC, XYZ — copy from NSE's ASM/GSM page" />
          </label>
          <button className="primary-button" onClick={() => void run(!!data)} disabled={loading}>
            {loading ? "Running all 5 layers…" : data ? "↻ Re-run with fresh data" : "▶ Run the algo"}
          </button>
        </div>
      </section>

      {(streak >= 3) && (
        <div className={`al-streak ${paused ? "al-streak--pause" : ""}`}>
          {paused
            ? `⏸ ${streak} losses in a row in your Journal — the system says pause new entries for about 10 sessions (until roughly ${new Date(new Date(lastLossDate!).getTime() + 14 * 86400000).toLocaleDateString("en-IN", { day: "2-digit", month: "short" })}).`
            : `⚠ ${streak} losses in a row in your Journal — position sizes below are cut by 50% until your next winner.`}
        </div>
      )}

      {error && <section className="panel"><p className="error-text">{error}</p></section>}
      {!data && !loading && !error && (
        <section className="panel"><p className="muted" style={{ margin: 0 }}>Press <b>Run the algo</b> to apply the five layers to today&apos;s market. The first run loads about 18 months of prices for every stock and can take a minute.</p></section>
      )}

      {data && (
        <>
          <section className={`al-regime ${data.regime.pass ? "al-regime--on" : "al-regime--off"}`}>
            <div className="al-regime-status">
              <span className="al-regime-light" />
              <div>
                <strong>{data.regime.pass ? "Longs allowed" : "No new longs"}</strong>
                <span>Layer 1 · market regime on {new Date(data.as_of).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}</span>
              </div>
            </div>
            <div className="al-checks">
              {data.regime.checks.map((c) => (
                <div key={c.name} className={`al-check al-check--${c.pass === null ? "na" : c.pass ? "ok" : "bad"}`}>
                  <span>{c.pass === null ? "–" : c.pass ? "✓" : "✗"}</span>
                  <div><b>{c.name}</b><small>{c.value}</small></div>
                </div>
              ))}
            </div>
          </section>

          {data.data_notes.length > 0 && <div className="al-notes">{data.data_notes.map((n) => <p key={n}>ℹ {n}</p>)}</div>}

          <section className="panel al-panel">
            <h2>Funnel</h2>
            <div className="al-funnel">
              {data.funnel.map((f, i) => {
                const max = Math.max(data.funnel[0].count, 1);
                return (
                  <div key={f.layer} className="al-funnel-row">
                    <span className="al-funnel-label">{f.layer}</span>
                    <div className="al-funnel-track"><div className="al-funnel-fill" style={{ width: `${Math.max((f.count / max) * 100, f.count ? 1.5 : 0)}%`, opacity: 1 - i * 0.12 }} /></div>
                    <b>{f.count}</b>
                  </div>
                );
              })}
            </div>
            {data.top_sectors.length > 0 && <p className="muted al-small">Top-5 sectors today: {data.top_sectors.join(", ")}</p>}
          </section>

          <section className="panel al-panel">
            <div className="al-panel-head">
              <h2>{data.longs_allowed ? `Trades that pass all five layers (${data.trades.length})` : "No trades — the regime gate is closed"}</h2>
              <span className="muted al-small">Open positions {openTrades.length}/{MAX_OPEN_POSITIONS} · size {sizeFactor === 1 ? "normal" : "halved"}{paused ? " · paused" : ""}</span>
            </div>
            {notice && <p className="al-notice">{notice}</p>}
            {data.longs_allowed && data.trades.length === 0 && (
              <p className="muted">Nothing passes every layer today. That is normal for a strict system — most days have no trade.</p>
            )}
            {!data.longs_allowed && (
              <p className="muted">Most losing streaks come from good setups in bad markets, so the system takes no new longs until the gate reopens.
                {data.blocked_by_regime.length > 0 && ` ${data.blocked_by_regime.length} stock(s) would otherwise qualify — shown below for your watchlist.`}</p>
            )}
            {(data.longs_allowed ? data.trades : data.blocked_by_regime).length > 0 && (
              <TradeTable
                rows={data.longs_allowed ? data.trades : data.blocked_by_regime}
                canTrade={data.longs_allowed && !paused}
                slots={slots}
                qtyFor={(t) => positionQty(t.close, t.stop, settings, sizeFactor)}
                held={held}
                watched={watched}
                onLog={logTrade}
                onWatch={watch}
              />
            )}
            {data.excluded_by_you && data.excluded_by_you.length > 0 && <p className="muted al-small">Skipped from your list: {data.excluded_by_you.join(", ")}</p>}
          </section>

          {data.near_misses.length > 0 && (
            <details className="panel al-panel">
              <summary><b>Near misses ({data.near_misses.length})</b> — passed layers 1–4 but not the final checks</summary>
              <table className="al-tbl">
                <tbody>
                  {data.near_misses.map((n) => (
                    <tr key={n.symbol}><td><Link className="stock-link" href={`/analyze/${n.symbol.replace(/\.NS$/, "")}`}>{n.symbol.replace(/\.NS$/, "")}</Link></td><td>{n.setup ?? ""}</td><td className="muted">{n.reason}</td></tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}

          <section className="panel al-panel al-exits">
            <h2>Exit rules for these trades</h2>
            <ul>
              <li><b>Stop:</b> the tighter of entry − 1.5 × ATR and the 10-day swing low (already calculated above).</li>
              <li><b>At +1.5R:</b> sell half and move the stop to breakeven.</li>
              <li><b>Trail:</b> exit the rest on a close below the 10 EMA.</li>
              <li><b>Time stop:</b> exit after 10 sessions if +1.5R was never reached.</li>
              <li><b>Streaks:</b> after 3 losses in a row, halve size; after 5, pause for about 10 sessions.</li>
            </ul>
            <p className="muted al-small">Trades logged from this page follow these exact rules in your Journal&apos;s exit plan.</p>
          </section>
        </>
      )}

      <BacktestPanel universe={universe} />

      <style>{`
        .al-head { display:grid; grid-template-columns:1fr 380px; gap:24px; align-items:start; margin-bottom:20px; }
        .al-title { font-family:var(--font-space-grotesk),sans-serif; font-size:clamp(1.5rem,3.2vw,2.3rem); line-height:1.1; margin:8px 0 12px; }
        .al-layers { margin:0; padding-left:20px; line-height:1.7; color:var(--muted); font-size:0.9rem; }
        .al-layers b { color:var(--text); }
        .al-controls { display:grid; gap:12px; }
        .al-controls textarea { width:100%; border-radius:12px; border:1px solid var(--line); padding:8px 12px; font:inherit; font-size:0.86rem; resize:vertical; background:rgba(255,255,255,0.8); }
        .al-streak { border-radius:14px; padding:10px 16px; margin-bottom:16px; background:rgba(214,150,0,0.1); border:1px solid rgba(214,150,0,0.3); font-size:0.9rem; }
        .al-streak--pause { background:rgba(185,75,81,0.08); border-color:rgba(185,75,81,0.3); }
        .al-regime { display:grid; grid-template-columns:260px 1fr; gap:18px; align-items:center; border-radius:22px; padding:18px 20px; margin-bottom:16px; border:1px solid var(--line); box-shadow:var(--shadow); }
        .al-regime--on { background:linear-gradient(135deg,rgba(22,108,90,0.1),rgba(255,255,255,0.75)); border-color:rgba(22,108,90,0.3); }
        .al-regime--off { background:linear-gradient(135deg,rgba(185,75,81,0.1),rgba(255,255,255,0.75)); border-color:rgba(185,75,81,0.3); }
        .al-regime-status { display:flex; gap:14px; align-items:center; }
        .al-regime-light { width:22px; height:22px; border-radius:50%; flex:none; }
        .al-regime--on .al-regime-light { background:#2f8a64; box-shadow:0 0 0 6px rgba(47,138,100,0.18); }
        .al-regime--off .al-regime-light { background:#b94b51; box-shadow:0 0 0 6px rgba(185,75,81,0.18); }
        .al-regime-status strong { display:block; font-family:var(--font-space-grotesk),sans-serif; font-size:1.4rem; }
        .al-regime-status span { font-size:0.8rem; color:var(--muted); }
        .al-checks { display:grid; grid-template-columns:repeat(2,1fr); gap:8px; }
        .al-check { display:flex; gap:10px; align-items:flex-start; background:rgba(255,255,255,0.65); border:1px solid var(--line); border-radius:12px; padding:8px 12px; font-size:0.84rem; }
        .al-check > span { font-weight:700; width:16px; }
        .al-check--ok > span { color:var(--green); } .al-check--bad > span { color:var(--red); } .al-check--na > span { color:var(--muted); }
        .al-check small { display:block; color:var(--muted); }
        .al-notes { background:var(--accent-soft); border-radius:14px; padding:6px 14px; margin-bottom:16px; font-size:0.84rem; }
        .al-notes p { margin:5px 0; }
        .al-panel { margin-bottom:20px; }
        .al-panel h2 { font-size:1.12rem; margin:0 0 12px; }
        .al-panel-head { display:flex; justify-content:space-between; align-items:baseline; gap:12px; flex-wrap:wrap; }
        .al-funnel { display:grid; gap:8px; }
        .al-funnel-row { display:grid; grid-template-columns:minmax(220px,340px) 1fr 50px; gap:12px; align-items:center; font-size:0.86rem; }
        .al-funnel-track { height:12px; background:var(--bg-deep); border-radius:6px; overflow:hidden; }
        .al-funnel-fill { height:100%; background:linear-gradient(90deg,#f16800,#ff9a2f); border-radius:6px; }
        .al-funnel-row b { text-align:right; }
        .al-small { font-size:0.8rem; margin:8px 0 0; }
        .al-notice { background:var(--accent-soft); color:var(--accent); border-radius:10px; padding:6px 12px; font-size:0.86rem; }
        .al-tbl { width:100%; border-collapse:collapse; font-size:0.86rem; }
        .al-tbl th { font-size:0.72rem; padding:8px; white-space:nowrap; }
        .al-tbl td { padding:9px 8px; vertical-align:top; }
        .al-points { display:flex; gap:3px; margin-top:4px; }
        .al-pt { width:18px; height:18px; border-radius:5px; display:grid; place-items:center; font-size:0.7rem; font-weight:700; }
        .al-pt--ok { background:rgba(22,108,90,0.14); color:var(--green); }
        .al-pt--bad { background:rgba(185,75,81,0.12); color:var(--red); }
        .al-pt--na { background:var(--bg-deep); color:var(--muted); }
        .al-actions { display:flex; flex-direction:column; gap:5px; }
        .al-exits ul { margin:0; padding-left:20px; line-height:1.7; font-size:0.9rem; }
        details.al-panel summary { cursor:pointer; }
        @media (max-width:960px) {
          .al-head, .al-regime { grid-template-columns:1fr; }
          .al-checks { grid-template-columns:1fr; }
          .al-funnel-row { grid-template-columns:1fr 60px; }
          .al-funnel-track { grid-column:1 / -1; grid-row:2; }
        }
      `}</style>
    </main>
  );
}

function TradeTable({ rows, canTrade, slots, qtyFor, held, watched, onLog, onWatch }: {
  rows: AlgoTrade[]; canTrade: boolean; slots: number; qtyFor: (t: AlgoTrade) => number;
  held: Set<string>; watched: Set<string>; onLog: (t: AlgoTrade) => void; onWatch: (t: AlgoTrade) => void;
}) {
  return (
    <div className="table-shell">
      <table className="al-tbl">
        <thead>
          <tr><th>Stock</th><th>Setup</th><th>Score</th><th>Close</th><th>Stop</th><th>Sell half at (+1.5R)</th><th>Qty</th><th>Leadership</th><th></th></tr>
        </thead>
        <tbody>
          {rows.map((t, i) => {
            const qty = qtyFor(t);
            // free-slot rank counts only stocks you don't already hold
            const freeRank = rows.slice(0, i).filter((r) => !held.has(r.symbol)).length;
            const noSlot = !held.has(t.symbol) && freeRank >= slots;
            const base = t.symbol.replace(/\.NS$/, "");
            return (
              <tr key={t.symbol}>
                <td>
                  <Link className="stock-link" href={`/analyze/${base}`}>{base}</Link>
                  <div className="table-subtext">{t.company_name} · {t.sector}</div>
                  <div className="table-subtext">
                    Earnings: {t.earnings_growth.pass === null ? `unverified (${t.earnings_growth.method})` : t.earnings_growth.pass ? `growing (${t.earnings_growth.method})` : "falling"}
                  </div>
                </td>
                <td>{t.setup}</td>
                <td>
                  <b>{t.score}/5</b>
                  <div className="al-points">
                    {t.points.map((p) => (
                      <span key={p.name} title={`${p.name}${p.detail ? ` — ${p.detail}` : ""}`} className={`al-pt al-pt--${p.pass === null ? "na" : p.pass ? "ok" : "bad"}`}>
                        {p.pass === null ? "?" : p.pass ? "✓" : "✗"}
                      </span>
                    ))}
                  </div>
                </td>
                <td>{fmtINR(t.close)}<div className="table-subtext">enter at next open</div></td>
                <td className="q-bad">{fmtINR(t.stop)}<div className="table-subtext">−{t.risk_pct.toFixed(1)}%</div></td>
                <td className="q-ok">{fmtINR(t.partial_target)}<div className="table-subtext">then stop → breakeven</div></td>
                <td><b>{qty}</b><div className="table-subtext">{fmtINR(qty * t.close)}</div></td>
                <td>
                  RS {t.rs_percentile.toFixed(0)}th pct
                  <div className="table-subtext">Sector #{t.sector_rank ?? "—"} · {t.pct_from_52w_high.toFixed(1)}% off high</div>
                  <div className="table-subtext">₹{t.avg_traded_value_cr.toFixed(0)} Cr/day traded</div>
                </td>
                <td>
                  <div className="al-actions">
                    <button className="mini-btn mini-btn--primary" onClick={() => onLog(t)}
                      disabled={!canTrade || held.has(t.symbol) || noSlot || qty <= 0}>
                      {held.has(t.symbol) ? "Held" : noSlot ? "No free slot" : "Log trade"}
                    </button>
                    <button className="mini-btn" onClick={() => onWatch(t)} disabled={watched.has(t.symbol)}>{watched.has(t.symbol) ? "Watching" : "Watch"}</button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function BacktestPanel({ universe }: { universe: ScanUniverse }) {
  const [result, setResult] = useState<AlgoBacktest | null>(null);
  const [status, setStatus] = useState<{ running: boolean; stage: string; progress?: number; error?: string } | null>(null);
  const [start, setStart] = useState("2015-01-01");
  const [confirmations, setConfirmations] = useState(3);
  const [full, setFull] = useState(false);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getAlgoBacktest(universe).then(setResult).catch(() => setResult(null));
    getAlgoBacktestStatus().then((s) => { setStatus(s); if (s.running) beginPolling(); }).catch(() => undefined);
    return () => { if (poll.current) clearInterval(poll.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [universe]);

  function beginPolling() {
    if (poll.current) clearInterval(poll.current);
    poll.current = setInterval(async () => {
      const s = await getAlgoBacktestStatus().catch(() => null);
      if (!s) return;
      setStatus(s);
      if (!s.running) {
        if (poll.current) clearInterval(poll.current);
        getAlgoBacktest(universe).then(setResult).catch(() => undefined);
      }
    }, 3000);
  }
  async function run() {
    const r = await startAlgoBacktest(universe, start, confirmations, full).catch(() => null);
    if (r) { setStatus(r.state); beginPolling(); }
  }

  const b = result;
  const target = (ok: boolean) => <span className={`pill-tag ${ok ? "pill-tag--ok" : "pill-tag--bad"}`}>{ok ? "Pass" : "Fail"}</span>;
  function exportExcel() {
    if (!b) return;
    void exportWorkbook(`algo-backtest-${universe}`, [
      { name: "Summary", rows: [
        { Metric: "Period", Value: `${b.start} → ${b.end}` }, { Metric: "Trades", Value: b.overall.trades },
        { Metric: "Win rate %", Value: b.overall.win_rate === null ? null : Math.round(b.overall.win_rate * 1000) / 10 },
        { Metric: "Expectancy (R)", Value: b.overall.expectancy_r }, { Metric: "Profit factor", Value: b.overall.profit_factor },
        { Metric: "CAGR %", Value: b.cagr_pct }, { Metric: "Max drawdown %", Value: b.max_drawdown_pct },
        { Metric: `${b.index_name} CAGR %`, Value: b.benchmark_cagr_pct }, { Metric: "Passes all targets", Value: b.passes_all ? "Yes" : "No" },
        ...b.assumptions.map((a) => ({ Metric: "Assumption", Value: a })),
      ] },
      { name: "By year", rows: b.per_year.map((y) => ({ Year: y.year, Trades: y.trades, "Win %": y.win_rate === null ? null : Math.round(y.win_rate * 1000) / 10, "Expectancy R": y.expectancy_r, "Profit factor": y.profit_factor, "Return %": y.return_pct })) },
      { name: "Recent trades", rows: b.recent_trades.map((t) => ({ Symbol: t.symbol, Setup: t.setup, Entry: t.entry_date, Exit: t.exit_date, "Entry ₹": t.entry, "Exit ₹": t.exit, R: t.r, Reason: t.reason, "Size": t.size_factor })) },
    ]);
  }

  return (
    <section className="panel al-panel bt">
      <div className="al-panel-head">
        <h2>Is it real? Verification backtest</h2>
        {b?.available && <button className="mini-btn" onClick={exportExcel}>Export to Excel</button>}
      </div>
      <p className="muted bt-intro">Replays these exact rules from your start date to today, year by year, with STT, charges and slippage. Targets: expectancy above 0.3R, profit factor above 1.5, and at least 200 trades.</p>
      <div className="bt-controls">
        <label className="field">From
          <select value={start} onChange={(e) => setStart(e.target.value)}>
            {["2015-01-01", "2018-01-01", "2020-01-01", "2022-01-01"].map((d) => <option key={d} value={d}>{d.slice(0, 4)}</option>)}
          </select>
        </label>
        <label className="field">Testable confirmations required
          <select value={confirmations} onChange={(e) => setConfirmations(Number(e.target.value))}>
            <option value={3}>3 of 3 (strict ≈ 4/5)</option>
            <option value={2}>2 of 3 (lenient)</option>
          </select>
        </label>
        <label className="bt-check"><input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} /> Every stock (slow; default tests an even sample)</label>
        <button className="primary-button" onClick={() => void run()} disabled={!!status?.running}>
          {status?.running ? `${status.stage}… ${status.progress ?? 0}%` : "▶ Run backtest"}
        </button>
      </div>
      {status?.stage === "error" && <p className="error-text">Backtest failed: {status.error}</p>}

      {!b && !status?.running && <p className="muted">No backtest yet for this universe. The first run downloads about 11 years of prices and can take several minutes.</p>}
      {b?.available && (
        <>
          <div className={`bt-verdict ${b.passes_all ? "bt-verdict--ok" : "bt-verdict--warn"}`}>
            <strong>{b.passes_all ? "Meets all three targets." : "Does not meet all targets yet — don't trade it with full size."}</strong>{" "}
            {b.start} → {b.end}, {b.symbols_tested} stocks, {b.overall.trades} trades. Positive expectancy in {b.positive_years} of {b.years_with_trades} years with trades.
          </div>
          <div className="bt-targets">
            <div><span>Expectancy per trade</span><strong>{b.overall.expectancy_r?.toFixed(2) ?? "—"}R</strong><small>target &gt; {b.targets.expectancy_r}R</small>{target(b.checks.expectancy_r)}</div>
            <div><span>Profit factor</span><strong>{b.overall.profit_factor?.toFixed(2) ?? "—"}</strong><small>target &gt; {b.targets.profit_factor}</small>{target(b.checks.profit_factor)}</div>
            <div><span>Trades in sample</span><strong>{b.overall.trades}</strong><small>target ≥ {b.targets.min_trades}</small>{target(b.checks.min_trades)}</div>
            <div><span>Win rate</span><strong>{b.overall.win_rate === null ? "—" : `${Math.round(b.overall.win_rate * 100)}%`}</strong><small>avg win {b.overall.avg_win_r ?? "—"}R · loss {b.overall.avg_loss_r ?? "—"}R</small></div>
            <div><span>CAGR (1% risk/trade)</span><strong>{b.cagr_pct > 0 ? "+" : ""}{b.cagr_pct}%</strong><small>{b.index_name} {b.benchmark_cagr_pct ?? "—"}%</small></div>
            <div><span>Max drawdown</span><strong>{b.max_drawdown_pct}%</strong><small>regime closed {b.regime_off_pct}% of days</small></div>
          </div>
          <div className="bt-grid">
            <div>
              <h3>Year by year (walk-forward)</h3>
              <table className="al-tbl">
                <thead><tr><th>Year</th><th>Trades</th><th>Win</th><th>Exp. R</th><th>PF</th><th>Return</th></tr></thead>
                <tbody>
                  {b.per_year.map((y) => (
                    <tr key={y.year} className={y.year === 2020 || y.year === 2022 ? "bt-stress" : ""}>
                      <td>{y.year}{(y.year === 2020 || y.year === 2022) && <small> stress year</small>}</td>
                      <td>{y.trades}</td>
                      <td>{y.win_rate === null ? "—" : `${Math.round(y.win_rate * 100)}%`}</td>
                      <td className={(y.expectancy_r ?? 0) >= 0 ? "q-ok" : "q-bad"}>{y.expectancy_r?.toFixed(2) ?? "—"}</td>
                      <td>{y.profit_factor?.toFixed(2) ?? "—"}</td>
                      <td className={(y.return_pct ?? 0) >= 0 ? "q-ok" : "q-bad"}>{y.return_pct === null ? "—" : `${y.return_pct > 0 ? "+" : ""}${y.return_pct}%`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <h3>Equity vs {b.index_name}</h3>
              <Curve points={b.curve} />
              <h3 style={{ marginTop: 14 }}>By setup and exit</h3>
              <table className="al-tbl">
                <tbody>
                  {Object.entries(b.by_setup).map(([k, v]) => <tr key={k}><td>{k}</td><td>{v.trades} trades</td><td className={(v.expectancy_r ?? 0) >= 0 ? "q-ok" : "q-bad"}>{v.expectancy_r?.toFixed(2)}R</td></tr>)}
                  {Object.entries(b.by_exit).map(([k, v]) => <tr key={k} className="muted"><td>exit: {k}</td><td>{v.trades}</td><td>{v.expectancy_r?.toFixed(2)}R</td></tr>)}
                </tbody>
              </table>
            </div>
          </div>
          <details className="bt-assume">
            <summary>Assumptions and limits</summary>
            <ul>{b.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
          </details>
        </>
      )}
      <style>{`
        .bt-intro { margin:0 0 12px; font-size:0.88rem; max-width:85ch; }
        .bt-controls { display:flex; flex-wrap:wrap; gap:12px; align-items:flex-end; margin-bottom:14px; }
        .bt-controls .field { min-width:180px; }
        .bt-check { display:flex; gap:8px; align-items:center; font-size:0.85rem; padding-bottom:10px; }
        .bt-verdict { border-radius:14px; padding:12px 16px; margin-bottom:14px; font-size:0.9rem; line-height:1.55; }
        .bt-verdict--ok { background:rgba(22,108,90,0.08); border:1px solid rgba(22,108,90,0.25); }
        .bt-verdict--warn { background:rgba(214,150,0,0.09); border:1px solid rgba(214,150,0,0.3); }
        .bt-targets { display:grid; grid-template-columns:repeat(6,1fr); gap:10px; margin-bottom:16px; }
        .bt-targets > div { background:rgba(255,255,255,0.6); border:1px solid var(--line); border-radius:14px; padding:10px 12px; display:grid; gap:3px; align-content:start; }
        .bt-targets span { font-size:0.74rem; color:var(--muted); }
        .bt-targets strong { font-family:var(--font-space-grotesk),sans-serif; font-size:1.25rem; }
        .bt-targets small { font-size:0.74rem; color:var(--muted); }
        .bt-targets .pill-tag { justify-self:start; }
        .bt-grid { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
        .bt-grid h3 { font-size:0.95rem; margin:0 0 8px; }
        .bt-stress td { background:rgba(214,150,0,0.07); }
        .bt-stress small { color:#8a5c00; }
        .bt-assume { margin-top:14px; font-size:0.85rem; }
        .bt-assume summary { cursor:pointer; font-weight:600; }
        .bt-assume ul { color:var(--muted); line-height:1.6; }
        .bt-curve { width:100%; height:190px; background:rgba(255,255,255,0.55); border:1px solid var(--line); border-radius:12px; }
        .bt-legend { display:flex; gap:14px; font-size:0.76rem; color:var(--muted); margin-top:4px; }
        .bt-legend i { display:inline-block; width:12px; height:3px; margin-right:5px; vertical-align:middle; }
        @media (max-width:960px) { .bt-targets { grid-template-columns:1fr 1fr; } .bt-grid { grid-template-columns:1fr; } }
      `}</style>
    </section>
  );
}

function Curve({ points }: { points: { date: string; equity: number; benchmark: number | null }[] }) {
  if (points.length < 2) return null;
  const W = 600, H = 190, P = 8;
  const vals = points.flatMap((p) => [p.equity, p.benchmark ?? p.equity]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const x = (i: number) => P + (i / (points.length - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - ((v - lo) / Math.max(hi - lo, 1e-9)) * (H - 2 * P);
  const line = (get: (p: (typeof points)[number]) => number | null) =>
    points.map((p, i) => (get(p) === null ? null : `${x(i)},${y(get(p) as number)}`)).filter(Boolean).join(" ");
  return (
    <div>
      <svg className="bt-curve" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Algo equity versus index">
        <polyline points={line((p) => p.benchmark)} fill="none" stroke="#9a948c" strokeWidth="1.5" />
        <polyline points={line((p) => p.equity)} fill="none" stroke="#f16800" strokeWidth="2.5" />
      </svg>
      <div className="bt-legend"><span><i style={{ background: "#f16800" }} />Algo (growth of 100)</span><span><i style={{ background: "#9a948c" }} />Index</span><span>{points[0].date} → {points[points.length - 1].date}</span></div>
    </div>
  );
}
