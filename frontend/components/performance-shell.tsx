"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import {
  evaluateLedger,
  getPerformance,
  getPortfolioBacktest,
  getPortfolioBacktestStatus,
  startPortfolioBacktest,
} from "../lib/api";
import { exportWorkbook } from "../lib/excel";
import { fmtINR, fmtPattern } from "../lib/store";
import {
  BacktestRunState,
  PerformanceSummary,
  PortfolioBacktestResult,
  ScanUniverse,
} from "../types";
import { AppNav } from "./app-nav";

const UNIVERSES: { value: ScanUniverse; label: string }[] = [
  { value: "nifty500", label: "Nifty 500" },
  { value: "nifty_smallcap_250", label: "Smallcap 250" },
  { value: "mid_small_2000_plus", label: "Mid & Small 2000+" },
];

const STATUS_UI: Record<string, { label: string; cls: string }> = {
  pending: { label: "Waiting for trigger", cls: "pill-tag--muted" },
  open: { label: "Filled, open", cls: "pill-tag--warn" },
  expired: { label: "Never triggered", cls: "pill-tag--muted" },
  target: { label: "Hit target", cls: "pill-tag--ok" },
  stop: { label: "Stopped out", cls: "pill-tag--bad" },
  time: { label: "Time exit", cls: "pill-tag--warn" },
};

const pct = (v: number | null | undefined, dp = 0) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(dp)}%`);
const num = (v: number | null | undefined, dp = 2, suffix = "") => (v === null || v === undefined ? "—" : `${v.toFixed(dp)}${suffix}`);
const signed = (v: number | null | undefined, dp = 1, suffix = "%") =>
  v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(dp)}${suffix}`;
const tone = (v: number | null | undefined, good = 0) => (v === null || v === undefined ? "" : v > good ? "q-ok" : v < good ? "q-bad" : "");

export function PerformanceShell() {
  const [perf, setPerf] = useState<PerformanceSummary | null>(null);
  const [perfError, setPerfError] = useState(false);
  const [grading, setGrading] = useState(false);
  const [universe, setUniverse] = useState<ScanUniverse>("nifty500");
  const [years, setYears] = useState(2);
  const [bt, setBt] = useState<PortfolioBacktestResult | null>(null);
  const [run, setRun] = useState<BacktestRunState>({ running: false });
  const [message, setMessage] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function loadPerf() {
    try {
      setPerf(await getPerformance());
      setPerfError(false);
    } catch {
      setPerfError(true);
    }
  }

  useEffect(() => {
    void loadPerf();
    void getPortfolioBacktestStatus().then((s) => {
      setRun(s);
      if (s.running) startPolling();
    }).catch(() => undefined);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { void getPortfolioBacktest(universe).then(setBt); }, [universe]);

  function startPolling() {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const s = await getPortfolioBacktestStatus();
        setRun(s);
        if (!s.running) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          if (s.error) setMessage(`Backtest failed: ${s.error}`);
          else {
            setMessage("Backtest finished and scores were recalibrated.");
            setBt(await getPortfolioBacktest((s.universe as ScanUniverse) ?? universe));
            void loadPerf();
          }
        }
      } catch { /* keep polling */ }
    }, 4000);
  }

  async function runBacktest() {
    try {
      const res = await startPortfolioBacktest(universe, years);
      setRun(res.state);
      if (!res.started) setMessage("A backtest is already running — showing its progress.");
      startPolling();
    } catch {
      setMessage("Could not start the backtest. Check that the backend is running.");
    }
  }

  async function grade() {
    setGrading(true);
    try {
      const r = await evaluateLedger();
      setMessage(r.skipped ? "Grading is already running." : `Graded ${r.checked ?? 0} open signals.`);
      await loadPerf();
    } catch {
      setMessage("Could not grade signals right now.");
    } finally {
      setGrading(false);
    }
  }

  async function exportAll() {
    const sheets = [];
    if (perf) {
      sheets.push({
        name: "Live signals",
        rows: perf.recent.map((r) => ({
          "Signal date": r.signal_date, Symbol: r.symbol.replace(/\.NS$/, ""), Sector: r.sector, Pattern: fmtPattern(r.pattern),
          Regime: r.regime, "Entry (₹)": r.entry, "Stop (₹)": r.stop, "Target (₹)": r.target,
          "Confidence %": r.probability === null ? null : Math.round(r.probability * 1000) / 10,
          Status: STATUS_UI[r.status]?.label ?? r.status, "Fill date": r.fill_date, "Fill (₹)": r.fill_price,
          "Exit date": r.exit_date, "Exit (₹)": r.exit_price, "Net return %": r.return_pct_net, "R (net)": r.r_multiple_net,
          Sessions: r.sessions_held,
        })),
      });
      sheets.push({ name: "Live by pattern", rows: perf.by_pattern.map((g) => ({ Pattern: fmtPattern(g.key), Signals: g.signals, Trades: g.trades, "Fill rate %": g.fill_rate === null ? null : g.fill_rate * 100, "Win rate %": g.win_rate === null ? null : g.win_rate * 100, "Avg R": g.average_r, "Profit factor": g.profit_factor })) });
    }
    if (bt) {
      sheets.push({ name: "Backtest summary", rows: Object.entries(bt.metrics).map(([k, v]) => ({ Metric: k, Value: v as number | string | null })) });
      sheets.push({ name: "Backtest monthly", rows: bt.monthly_returns.map((m) => ({ Month: m.month, "Return %": m.return_pct })) });
      sheets.push({ name: "Backtest trades", rows: bt.trades.map((t) => ({ Symbol: t.symbol.replace(/\.NS$/, ""), Sector: t.sector, Pattern: fmtPattern(t.pattern), Regime: t.regime, Entry: t.entry_date, Exit: t.exit_date, "Entry (₹)": t.entry, "Exit (₹)": t.exit, Qty: t.qty, Reason: t.reason, "P&L (₹)": t.pnl, "Return %": t.return_pct, R: t.r, Sessions: t.sessions })) });
      sheets.push({ name: "Backtest assumptions", rows: bt.assumptions.map((a) => ({ Assumption: a })) });
    }
    if (perf?.calibration) {
      sheets.push({ name: "Calibration", rows: perf.calibration.bands.map((b) => ({ Band: b.band, "Score from": b.score_from, "Score to": b.score_to, Trades: b.trades, "Win rate %": Math.round(b.win_rate * 1000) / 10 })) });
    }
    await exportWorkbook("system-performance", sheets);
  }

  const m = bt?.metrics;
  const cal = perf?.calibration;
  const calSpread = cal?.spread ?? (cal ? Math.max(...cal.bands.map((b) => b.win_rate)) - Math.min(...cal.bands.map((b) => b.win_rate)) : 0);

  return (
    <main className="page-shell">
      <AppNav />
      <section className="pf-head">
        <div>
          <p className="eyebrow">System performance</p>
          <h1 className="pf-title">Does the system actually make money?</h1>
          <p className="muted">
            Two independent answers: every live pick graded against what the market did next, and a replay of the
            whole system over past years with your portfolio rules and trading costs.
          </p>
        </div>
        <button className="secondary-button" onClick={() => void exportAll()} disabled={!perf && !bt}>Export to Excel</button>
      </section>

      {message && <p className="pf-msg" onClick={() => setMessage(null)}>{message}</p>}

      {/* ── Live ledger ─────────────────────────────────────────── */}
      <section className="panel pf-panel">
        <div className="pf-panel-head">
          <div>
            <h2>Live signal ledger</h2>
            <p className="muted" style={{ margin: 0 }}>
              Every published pick, graded with the exact entry, stop, target, 20-session time stop and costs used in the backtest.
            </p>
          </div>
          <button className="mini-btn mini-btn--primary" onClick={() => void grade()} disabled={grading}>
            {grading ? "Grading…" : "Grade open signals"}
          </button>
        </div>

        {perf && !perf.persistent_storage && (
          <div className="pf-warn">
            The ledger is on the server&apos;s temporary disk and resets whenever the backend restarts. Set a
            <code> DATABASE_URL </code> for a free Postgres database to keep it permanently.
          </div>
        )}
        {perfError && <p className="error-text">Could not load the ledger. Check the backend.</p>}

        {perf && perf.total_signals === 0 ? (
          <div className="empty-state">
            <p>No signals recorded yet. Every scan you run adds its picks here automatically.</p>
            <Link href="/" className="text-link">Run a scan →</Link>
          </div>
        ) : perf ? (
          <>
            <div className="pf-stats">
              <Stat label="Signals tracked" value={String(perf.total_signals)} sub={perf.first_signal ? `since ${perf.first_signal}` : undefined} />
              <Stat label="Still live" value={String(perf.pending + perf.open)} sub={`${perf.open} filled · ${perf.pending} waiting`} />
              <Stat label="Fill rate" value={pct(perf.fill_rate)} sub={`${perf.expired} never triggered`} />
              <Stat label="Closed trades" value={String(perf.closed)} sub={perf.closed < 30 ? "too few to judge yet" : undefined} />
              <Stat label="Win rate" value={pct(perf.win_rate)} cls={tone(perf.win_rate, 0.45)} />
              <Stat label="Avg R (net)" value={signed(perf.average_r, 2, "R")} cls={tone(perf.average_r)} />
            </div>

            <div className="pf-2col">
              <GroupTable title="By pattern" rows={perf.by_pattern} labelOf={(k) => fmtPattern(k)} />
              <GroupTable title="By market regime" rows={perf.by_regime} labelOf={(k) => k} />
            </div>

            {perf.by_probability_band.length > 0 && (
              <>
                <h3 className="pf-h3">Predicted vs realised</h3>
                <p className="muted pf-note">If the confidence score means anything, the realised win rate should rise down this table.</p>
                <div className="table-shell">
                  <table className="pf-tbl">
                    <thead><tr><th>Confidence band</th><th>Avg predicted</th><th>Trades</th><th>Realised win rate</th><th>Avg R</th></tr></thead>
                    <tbody>
                      {perf.by_probability_band.map((b) => (
                        <tr key={b.band}><td>{b.band}</td><td>{pct(b.predicted)}</td><td>{b.trades}</td><td className={tone(b.win_rate, 0.45)}>{pct(b.win_rate)}</td><td className={tone(b.average_r)}>{signed(b.average_r, 2, "R")}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}

            <h3 className="pf-h3">Recent signals</h3>
            <div className="table-shell pf-scroll">
              <table className="pf-tbl">
                <thead><tr><th>Date</th><th>Stock</th><th>Pattern</th><th>Entry → exit</th><th>Status</th><th>Net</th><th>R</th></tr></thead>
                <tbody>
                  {perf.recent.slice(0, 60).map((r) => (
                    <tr key={r.id}>
                      <td>{r.signal_date}</td>
                      <td><Link href={`/stocks/${r.symbol}`} className="stock-link">{r.symbol.replace(/\.NS$/, "")}</Link><div className="table-subtext">{r.regime ?? ""}</div></td>
                      <td>{fmtPattern(r.pattern)}</td>
                      <td>{fmtINR(r.fill_price ?? r.entry)}{r.exit_price ? ` → ${fmtINR(r.exit_price)}` : ""}<div className="table-subtext">stop {fmtINR(r.stop)} · target {fmtINR(r.target)}</div></td>
                      <td><span className={`pill-tag ${STATUS_UI[r.status]?.cls ?? ""}`}>{STATUS_UI[r.status]?.label ?? r.status}</span></td>
                      <td className={tone(r.return_pct_net)}>{signed(r.return_pct_net, 2)}</td>
                      <td className={tone(r.r_multiple_net)}>{signed(r.r_multiple_net, 2, "R")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : !perfError ? <p className="muted">Loading…</p> : null}
      </section>

      {/* ── Portfolio backtest ─────────────────────────────────── */}
      <section className="panel pf-panel">
        <div className="pf-panel-head">
          <div>
            <h2>Portfolio backtest</h2>
            <p className="muted" style={{ margin: 0 }}>
              Replays the scanner day by day and trades it with your rules: max 5 positions, 2 per sector, 1.5% risk, regime-scaled size, costs included.
            </p>
          </div>
          <div className="pf-run">
            <select className="pf-select" value={universe} onChange={(e) => setUniverse(e.target.value as ScanUniverse)} disabled={run.running}>
              {UNIVERSES.map((u) => <option key={u.value} value={u.value}>{u.label}</option>)}
            </select>
            <select className="pf-select" value={years} onChange={(e) => setYears(Number(e.target.value))} disabled={run.running}>
              {[1, 2, 3].map((y) => <option key={y} value={y}>{y} year{y > 1 ? "s" : ""}</option>)}
            </select>
            <button className="primary-button pf-btn" onClick={() => void runBacktest()} disabled={run.running}>
              {run.running ? "Running…" : "Run backtest"}
            </button>
          </div>
        </div>

        {run.running && (
          <div className="pf-progress">
            <div className="pf-track"><div className="pf-fill" style={{ width: `${Math.round((run.progress ?? 0) * 100)}%` }} /></div>
            <span className="muted">{run.stage} · {Math.round((run.progress ?? 0) * 100)}% — runs from the website test an evenly spread 120-stock sample (a few minutes); you can leave this page.</span>
          </div>
        )}

        {!bt && !run.running && (
          <div className="empty-state"><p>No backtest for this universe yet. Run one to see how the system would have performed.</p></div>
        )}

        {bt && m && (
          <>
            <p className="muted pf-note">
              {m.start} → {m.end} · {bt.symbols_tested} stocks tested{bt.symbols_skipped ? `, ${bt.symbols_skipped} skipped for bad data` : ""} · run {new Date(bt.generated_at).toLocaleString("en-IN")}
            </p>
            <div className="pf-stats">
              <Stat label="CAGR" value={signed(m.cagr_pct)} cls={tone(m.cagr_pct)} sub={`Total ${signed(m.total_return_pct)}`} />
              <Stat label={`vs ${"Nifty"} (buy & hold)`} value={signed(m.benchmark_return_pct)} sub={m.total_return_pct >= m.benchmark_return_pct ? "System ahead" : "System behind"} cls={m.total_return_pct >= m.benchmark_return_pct ? "q-ok" : "q-bad"} />
              <Stat label="Max drawdown" value={signed(m.max_drawdown_pct)} cls={m.max_drawdown_pct < -15 ? "q-bad" : ""} />
              <Stat label="Avg month" value={signed(m.avg_monthly_return_pct, 2)} cls={tone(m.avg_monthly_return_pct)} sub={m.positive_months_pct != null ? `${m.positive_months_pct.toFixed(0)}% of months positive` : undefined} />
              <Stat label="Win rate · avg R" value={`${pct(m.win_rate)} · ${signed(m.average_r, 2, "R")}`} sub={`${m.trades} trades, profit factor ${num(m.profit_factor)}`} />
              <Stat label="Time invested" value={`${m.exposure_pct.toFixed(0)}%`} sub={`avg hold ${num(m.average_sessions, 1)} sessions`} />
            </div>

            <EquityChart points={bt.equity_curve} />

            <div className="pf-2col">
              <div>
                <h3 className="pf-h3">By pattern</h3>
                <SimpleTable rows={bt.by_pattern.map((g) => [fmtPattern(g.key), g.trades, pct(g.win_rate), signed(g.average_r, 2, "R"), fmtINR(g.pnl)])}
                  head={["Pattern", "Trades", "Win", "Avg R", "P&L"]} />
              </div>
              <div>
                <h3 className="pf-h3">By regime at entry</h3>
                <SimpleTable rows={bt.by_regime.map((g) => [g.key, g.trades, pct(g.win_rate), signed(g.average_r, 2, "R"), fmtINR(g.pnl)])}
                  head={["Regime", "Trades", "Win", "Avg R", "P&L"]} />
              </div>
            </div>

            <h3 className="pf-h3">Monthly returns</h3>
            <div className="pf-months">
              {bt.monthly_returns.map((mo) => (
                <div key={mo.month} className={`pf-month ${mo.return_pct >= 0 ? "pf-month--up" : "pf-month--down"}`}>
                  <span>{mo.month}</span><strong>{signed(mo.return_pct)}</strong>
                </div>
              ))}
            </div>

            <details className="pf-details">
              <summary>Assumptions and limits</summary>
              <ul>{bt.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
            </details>
          </>
        )}
      </section>

      {/* ── Calibration ───────────────────────────────────────── */}
      <section className="panel pf-panel">
        <h2>Score calibration</h2>
        <p className="muted pf-note">
          Maps the scanner&apos;s ranking score to how often similar setups actually won. Picks then show a
          &ldquo;historical win rate&rdquo; instead of a capped confidence number.
        </p>
        {!cal ? (
          <div className="empty-state"><p>Not calibrated yet — running a portfolio backtest fits the model automatically.</p></div>
        ) : (
          <>
            <div className={`pf-verdict ${cal.significant ? "pf-verdict--ok" : "pf-verdict--warn"}`}>
              {cal.z_score !== undefined && cal.z_score !== null && cal.z_score <= -1.96
                ? `The score is working backwards: the highest-scored 40% of setups won ${pct(cal.high_score_win_rate)} versus ${pct(cal.low_score_win_rate)} for the lowest 40%, and the gap is too large to be chance (z = ${num(cal.z_score)}). Don't prefer high-scoring picks — review which score components (52-week high, momentum, RS) are rewarding stocks that are already extended.`
                : cal.significant === undefined
                ? `Win rates range ${(calSpread * 100).toFixed(0)} points across score bands. Re-run a backtest to test whether that difference is real.`
                : cal.significant
                  ? `The score works: the highest-scored 40% of setups won ${pct(cal.high_score_win_rate)} versus ${pct(cal.low_score_win_rate)} for the lowest 40% — a gap very unlikely to be chance (z = ${num(cal.z_score)}).`
                  : `No reliable edge in the score yet: the highest-scored 40% of setups won ${pct(cal.high_score_win_rate)} versus ${pct(cal.low_score_win_rate)} for the lowest 40%, a difference that chance alone can easily produce over ${cal.samples} trades (z = ${num(cal.z_score)}). Treat qualifying picks as roughly equal and let position sizing and stops do the work.`}
            </div>
            <SimpleTable
              head={["Score band", "Score range", "Trades", "Win rate"]}
              rows={cal.bands.map((b) => [b.band, `${b.score_from.toFixed(2)} – ${b.score_to.toFixed(2)}`, b.trades, pct(b.win_rate)])}
            />
            <p className="muted pf-note">Fitted on {cal.source}. Overall win rate {pct(cal.base_win_rate)}.</p>
          </>
        )}
      </section>

      <style>{`
        .pf-head { display:grid; grid-template-columns:1fr auto; gap:24px; align-items:end; margin-bottom:20px; }
        .pf-title { font-family:var(--font-space-grotesk),sans-serif; font-size:clamp(1.6rem,3.5vw,2.4rem); margin:8px 0 10px; line-height:1.1; }
        .pf-msg { background:var(--accent-soft); color:var(--accent); border-radius:12px; padding:8px 14px; font-size:0.88rem; cursor:pointer; }
        .pf-panel { margin-bottom:20px; }
        .pf-panel h2 { font-size:1.2rem; }
        .pf-panel-head { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:16px; flex-wrap:wrap; }
        .pf-warn { background:rgba(214,150,0,0.1); border:1px solid rgba(214,150,0,0.3); color:#7a5200; border-radius:12px; padding:10px 14px; font-size:0.86rem; margin-bottom:16px; }
        .pf-stats { display:grid; grid-template-columns:repeat(6,1fr); gap:10px; margin-bottom:18px; }
        .pf-stat { background:rgba(255,255,255,0.6); border:1px solid var(--line); border-radius:16px; padding:12px 14px; min-width:0; }
        .pf-stat-l { display:block; font-size:0.72rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin-bottom:4px; }
        .pf-stat strong { font-family:var(--font-space-grotesk),sans-serif; font-size:1.2rem; }
        .pf-stat-sub { display:block; font-size:0.74rem; color:var(--muted); margin-top:3px; }
        .pf-2col { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
        .pf-h3 { font-size:0.98rem; font-family:var(--font-space-grotesk),sans-serif; margin:18px 0 8px; }
        .pf-note { font-size:0.84rem; margin:0 0 10px; }
        .pf-tbl { width:100%; border-collapse:collapse; font-size:0.86rem; }
        .pf-tbl th { font-size:0.72rem; padding:8px 10px; white-space:nowrap; }
        .pf-tbl td { padding:8px 10px; vertical-align:top; }
        .pf-scroll { max-height:520px; overflow-y:auto; }
        .pf-run { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
        .pf-select { border:1px solid var(--line); border-radius:12px; background:#fff; padding:9px 12px; font:inherit; font-size:0.88rem; }
        .pf-btn { padding:9px 18px; }
        .pf-progress { margin-bottom:16px; }
        .pf-track { height:6px; background:var(--bg-deep); border-radius:3px; overflow:hidden; margin-bottom:6px; }
        .pf-fill { height:100%; background:linear-gradient(90deg,#f16800,#ff9a2f); transition:width .4s ease; }
        .pf-months { display:grid; grid-template-columns:repeat(auto-fill,minmax(96px,1fr)); gap:6px; }
        .pf-month { border-radius:10px; padding:6px 8px; font-size:0.78rem; display:flex; flex-direction:column; }
        .pf-month strong { font-size:0.92rem; }
        .pf-month--up { background:rgba(22,108,90,0.09); color:var(--green); }
        .pf-month--down { background:rgba(185,75,81,0.09); color:var(--red); }
        .pf-details { margin-top:16px; font-size:0.86rem; color:var(--muted); }
        .pf-details summary { cursor:pointer; font-weight:600; color:var(--text); }
        .pf-verdict { border-radius:12px; padding:10px 14px; font-size:0.9rem; font-weight:600; margin-bottom:12px; }
        .pf-verdict--ok { background:rgba(22,108,90,0.08); color:var(--green); }
        .pf-verdict--warn { background:rgba(214,150,0,0.1); color:#7a5200; }
        .pf-chart { width:100%; height:auto; background:rgba(255,255,255,0.5); border:1px solid var(--line); border-radius:16px; margin:4px 0 8px; }
        .pf-legend { display:flex; gap:16px; font-size:0.8rem; color:var(--muted); }
        .pf-legend i { display:inline-block; width:14px; height:3px; border-radius:2px; margin-right:6px; vertical-align:middle; }
        @media (max-width:960px) { .pf-head, .pf-2col { grid-template-columns:1fr; } .pf-stats { grid-template-columns:1fr 1fr; } }
      `}</style>
    </main>
  );
}

function Stat({ label, value, sub, cls = "" }: { label: string; value: string; sub?: string; cls?: string }) {
  return (
    <div className="pf-stat">
      <span className="pf-stat-l">{label}</span>
      <strong className={cls}>{value}</strong>
      {sub && <span className="pf-stat-sub">{sub}</span>}
    </div>
  );
}

function GroupTable({ title, rows, labelOf }: { title: string; rows: PerformanceSummary["by_pattern"]; labelOf: (k: string) => string }) {
  return (
    <div>
      <h3 className="pf-h3">{title}</h3>
      {rows.length === 0 ? <p className="muted pf-note">Nothing resolved yet.</p> : (
        <SimpleTable head={["", "Signals", "Filled", "Win", "Avg R"]}
          rows={rows.map((g) => [labelOf(g.key), g.signals, pct(g.fill_rate), pct(g.win_rate), signed(g.average_r, 2, "R")])} />
      )}
    </div>
  );
}

function SimpleTable({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return (
    <div className="table-shell">
      <table className="pf-tbl">
        <thead><tr>{head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function EquityChart({ points }: { points: PortfolioBacktestResult["equity_curve"] }) {
  if (points.length < 2) return null;
  const W = 800, H = 220, P = 36;
  const values = points.flatMap((p) => [p.equity, p.benchmark]);
  const min = Math.min(...values), max = Math.max(...values);
  const x = (i: number) => P + (i / (points.length - 1)) * (W - P * 2);
  const y = (v: number) => H - P + ((min - v) / (max - min || 1)) * (H - P * 2);
  const line = (key: "equity" | "benchmark") => points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ");
  const lakh = (v: number) => `₹${(v / 100000).toFixed(1)}L`;
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="pf-chart" role="img" aria-label="Equity curve of the system versus Nifty">
        <line x1={P} x2={W - P} y1={y(points[0].equity)} y2={y(points[0].equity)} stroke="rgba(69,45,18,0.15)" strokeDasharray="4 4" />
        <path d={line("benchmark")} fill="none" stroke="#9a8a7c" strokeWidth={1.5} />
        <path d={line("equity")} fill="none" stroke="#f16800" strokeWidth={2.5} />
        <text x={P} y={16} fontSize={11} fill="#7b6352">{lakh(max)}</text>
        <text x={P} y={H - 8} fontSize={11} fill="#7b6352">{lakh(min)}</text>
        <text x={W - P} y={H - 8} fontSize={11} fill="#7b6352" textAnchor="end">{points[points.length - 1].date}</text>
        <text x={P + 70} y={H - 8} fontSize={11} fill="#7b6352">{points[0].date}</text>
      </svg>
      <div className="pf-legend"><span><i style={{ background: "#f16800" }} />System</span><span><i style={{ background: "#9a8a7c" }} />Nifty buy &amp; hold (same capital)</span></div>
    </div>
  );
}
