"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getVcpMonitor, getVcpScan } from "../lib/api";
import { exportWorkbook } from "../lib/excel";
import {
  JournalTrade,
  WATCH_DAYS,
  fmtINR,
  positionQty,
  todayISO,
  uid,
  useJournal,
  useTradingSettings,
  useWatchlist,
} from "../lib/store";
import { ScanUniverse, VcpMonitor, VcpResult, VcpScanResponse } from "../types";
import { AppNav } from "./app-nav";

const UNIVERSES: { value: ScanUniverse; label: string }[] = [
  { value: "nifty500", label: "Nifty 500" },
  { value: "nifty_smallcap_250", label: "Smallcap 250" },
  { value: "mid_small_2000_plus", label: "Mid & Small 2000+" },
];
const STATUS_TONE: Record<string, string> = {
  "breakout today": "ok", breakout: "ok", "near pivot": "warn", building: "muted", extended: "bad",
};
const TT_LABELS: Record<string, string> = {
  price_above_30: "Price ≥ ₹30",
  ma200_rising_3m: "200 MA rising 3 months",
  ma50_above_ma200: "50 MA above 200 MA",
  price_above_ma200: "Price above 200 MA",
  doubled_from_52w_low: "+100% from 52-week low",
  new_52w_high_6m: "52-week high in last 6 months",
};
const Q_LABELS: Record<string, string> = {
  three_plus_contractions: "3+ contractions",
  each_about_half: "Each ~50% smaller",
  last_contraction_tight: "Last contraction ≤ 10%",
  volume_dry_up: "Volume drying up",
  more_up_volume: "More volume on up weeks",
  base_4_to_8_weeks: "Base 4–8 weeks",
  daily_tightening: "Daily range tightening",
};
const SIGNAL_LABELS: Record<string, string> = {
  follow_up_buying: "Follow-up buying",
  more_green_days: "Green days ≥ red days",
  more_up_volume: "Up volume ≥ down volume",
  tennis_ball_action: "Tennis-ball action",
  shallow_pullbacks: "Shallow pullbacks",
};

export function VcpShell() {
  const [universe, setUniverse] = useState<ScanUniverse>("nifty500");
  const [data, setData] = useState<VcpScanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("all");
  const [notice, setNotice] = useState<string | null>(null);
  const [journal, setJournal, ready] = useJournal();
  const [watchlist, setWatchlist] = useWatchlist();
  const [settings] = useTradingSettings();
  const [monitor, setMonitor] = useState<VcpMonitor[] | null>(null);
  const [monitorLoading, setMonitorLoading] = useState(false);

  const open = journal.filter((t) => t.status === "open");
  const held = new Set(open.map((t) => t.symbol));
  const watched = new Set(watchlist.map((w) => w.symbol));

  async function run(refresh = false) {
    setLoading(true);
    setError(null);
    try {
      setData(await getVcpScan(universe, refresh));
    } catch {
      setError("The VCP scan couldn't run. Check that the backend is up and try again.");
    } finally {
      setLoading(false);
    }
  }

  async function loadMonitor() {
    if (!open.length) { setMonitor([]); return; }
    setMonitorLoading(true);
    try {
      setMonitor(await getVcpMonitor(open.map((t) => ({ symbol: t.symbol, entry_date: t.entry_date, entry_price: t.entry_price }))));
    } catch {
      setMonitor(null);
    } finally {
      setMonitorLoading(false);
    }
  }
  useEffect(() => { if (ready) void loadMonitor(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [ready, open.length]);

  function watch(r: VcpResult) {
    const now = new Date();
    setWatchlist((prev) => [{
      id: uid(), symbol: r.symbol, company_name: r.company_name, sector: r.sector, pattern: "volatility_contraction",
      trigger_price: r.entry, stop_loss: r.stop, target_price: Math.round(r.entry * (1 + 3 * r.risk_pct / 100) * 100) / 100,
      added_at: now.toISOString(), expires_at: new Date(now.getTime() + WATCH_DAYS * 86400000).toISOString(), last_close: r.close,
    }, ...prev]);
    setNotice(`${r.symbol.replace(/\.NS$/, "")} on the watchlist — it alerts when the pivot ₹${r.pivot} is taken out.`);
  }
  function logTrade(r: VcpResult) {
    const qty = positionQty(r.entry, r.stop, settings);
    const t: JournalTrade = {
      id: uid(), symbol: r.symbol, company_name: r.company_name, sector: r.sector, pattern: "volatility_contraction",
      status: "open", entry_date: todayISO(), planned_entry: r.entry, entry_price: r.entry, qty, stop_loss: r.stop,
      initial_stop: r.stop, target_price: Math.round(r.entry * (1 + 3 * r.risk_pct / 100) * 100) / 100,
      backtest_win_rate: null, probability_score: null,
    };
    setJournal((prev) => [t, ...prev]);
    setNotice(`Logged ${qty} ${r.symbol.replace(/\.NS$/, "")} — stop ${fmtINR(r.stop)} (2×ATR, max 10%). Confirm the fill in the Journal.`);
  }

  const rows = (data?.results ?? []).filter((r) => filter === "all" || (filter === "breakout" ? r.status.startsWith("breakout") : r.status === filter));

  function exportExcel() {
    if (!data) return;
    void exportWorkbook(`vcp-${data.universe}`, [{
      name: "VCP candidates",
      rows: data.results.map((r) => ({
        Symbol: r.symbol.replace(/\.NS$/, ""), Company: r.company_name, Sector: r.sector, Status: r.status,
        "Close (₹)": r.close, "Pivot (₹)": r.pivot, "Distance to pivot %": r.distance_to_pivot_pct,
        Contractions: r.vcp.contractions, "Depths %": r.vcp.depths_pct.join(" → "), "Base weeks": r.vcp.base_weeks,
        "Up/down volume": r.vcp.up_down_volume, "Volume drying up": r.vcp.volume_dry_up ? "Yes" : "No",
        "Entry (₹)": r.entry, "Stop (₹)": r.stop, "Risk %": r.risk_pct, "Score": `${r.score}/${r.score_max}`,
        "% above 52w low": r.trend_template.pct_above_52w_low, "% below 52w high": r.trend_template.pct_below_52w_high,
      })),
    }]);
  }

  return (
    <main className="page-shell">
      <AppNav />
      <section className="vc-head">
        <div>
          <p className="eyebrow">VCP scanner</p>
          <h1 className="vc-title">Strong trends, tightening bases, buy the pivot.</h1>
          <p className="muted vc-sub">
            Eliminates every stock that fails the trend template, then looks on the <b>weekly</b> chart for a volatility contraction
            pattern — each pullback shallower than the last, on lighter volume. Built for a weekly routine: scan after the weekly
            close, then watch for the pivot to be taken out.
          </p>
        </div>
        <div className="vc-controls panel">
          <label className="field">Universe
            <select value={universe} onChange={(e) => setUniverse(e.target.value as ScanUniverse)}>
              {UNIVERSES.map((u) => <option key={u.value} value={u.value}>{u.label}</option>)}
            </select>
          </label>
          <button className="primary-button" onClick={() => void run(!!data)} disabled={loading}>
            {loading ? "Scanning…" : data ? "↻ Re-scan" : "▶ Run VCP scan"}
          </button>
          {data && <button className="mini-btn" onClick={exportExcel}>Export to Excel</button>}
        </div>
      </section>

      <details className="panel vc-rules">
        <summary><b>The rules this scanner applies</b></summary>
        <div className="vc-rules-grid">
          <div><h3>Eliminate unless</h3><ul>{Object.values(TT_LABELS).map((l) => <li key={l}>{l}</li>)}<li>Preferably above the 50 MA too (shown, not required)</li></ul></div>
          <div><h3>VCP (weekly)</h3><ul>
            <li>Contraction = swing low ÷ swing high − 1</li><li>Ideally 3 contractions, each ~50% smaller</li>
            <li>With only 2, the second must be 70%+ smaller</li><li>Advances on volume, pullbacks on lighter volume</li>
            <li>Breakout within 4–8 weeks of the base starting</li><li>Buy when the last swing high (pivot) is taken out</li>
          </ul></div>
          <div><h3>Stop &amp; expectations</h3><ul>
            <li>Stop = entry − 2 × ATR(20), never more than 10%</li><li>Daily charts only to time the entry</li>
            <li>Prefer adding to winners over finding new names daily</li><li>Win rate is about 50% — size so losses are survivable</li>
          </ul></div>
        </div>
      </details>

      {error && <section className="panel"><p className="error-text">{error}</p></section>}
      {!data && !loading && !error && <section className="panel"><p className="muted" style={{ margin: 0 }}>Run the scan to build this week&apos;s VCP list.</p></section>}

      {data && (
        <>
          <section className="panel vc-panel">
            <div className="vc-funnel">
              <div><span>Stocks scanned</span><strong>{data.counts.loaded}</strong></div>
              <div><span>Pass trend template</span><strong>{data.counts.trend_template}</strong></div>
              <div><span>Valid VCP</span><strong>{data.counts.valid_vcp}</strong></div>
              <div><span>Breakouts</span><strong className="q-ok">{(data.by_status["breakout today"] ?? 0) + (data.by_status["breakout"] ?? 0)}</strong></div>
              <div><span>Near pivot (≤ 5%)</span><strong>{data.by_status["near pivot"] ?? 0}</strong></div>
            </div>
            <p className="muted vc-small">
              Weekly structure as of the week ending {data.week_ending ?? "—"}; breakouts checked on the latest daily close.
              Most common eliminations: {Object.entries(data.eliminated_by).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, v]) => `${TT_LABELS[k] ?? k} (${v})`).join(", ")}.
            </p>
          </section>

          <section className="panel vc-panel">
            <div className="vc-filter">
              {[["all", "All"], ["breakout", "Breakouts"], ["near pivot", "Near pivot"], ["building", "Building"], ["extended", "Extended"]].map(([k, l]) => (
                <button key={k} className={`vc-tab ${filter === k ? "vc-tab--on" : ""}`} onClick={() => setFilter(k)}>{l}</button>
              ))}
            </div>
            {notice && <p className="vc-notice">{notice}</p>}
            {rows.length === 0 ? (
              <p className="muted">{data.counts.valid_vcp === 0 ? "No valid VCPs this week. That's common — proper bases are rare. Re-scan after next week's close." : "No stocks in this group."}</p>
            ) : (
              <div className="table-shell">
                <table className="vc-tbl">
                  <thead><tr><th>Stock</th><th>Status</th><th>Contractions</th><th>Pivot</th><th>Entry / stop</th><th>Volume</th><th>Quality</th><th></th></tr></thead>
                  <tbody>
                    {rows.map((r) => {
                      const base = r.symbol.replace(/\.NS$/, "");
                      const breakout = r.status.startsWith("breakout");
                      return (
                        <tr key={r.symbol}>
                          <td>
                            <Link className="stock-link" href={`/analyze/${base}`}>{base}</Link>
                            <div className="table-subtext">{r.company_name} · {r.sector}</div>
                            <div className="table-subtext">+{r.trend_template.pct_above_52w_low}% off 52w low · {r.trend_template.pct_below_52w_high}% below high{r.trend_template.above_ma50 ? "" : " · below 50 MA"}</div>
                          </td>
                          <td><span className={`pill-tag pill-tag--${STATUS_TONE[r.status]}`}>{r.status}</span>
                            {breakout && r.breakout_volume_ratio !== null && <div className="table-subtext">{r.breakout_volume_ratio.toFixed(1)}× avg volume</div>}
                          </td>
                          <td>
                            <div className="vc-depths">
                              {r.vcp.depths_pct.map((d, i) => (
                                <span key={i} className="vc-depth" style={{ width: `${Math.min(Math.abs(d) * 3, 90)}px` }} title={`${d}% over ${r.vcp.durations_weeks[i]} weeks`}>{d.toFixed(1)}%</span>
                              ))}
                            </div>
                            <div className="table-subtext">{r.vcp.contractions} contractions · base {r.vcp.base_weeks} weeks</div>
                          </td>
                          <td><b>{fmtINR(r.pivot)}</b><div className="table-subtext">close {fmtINR(r.close)} ({r.distance_to_pivot_pct > 0 ? "+" : ""}{r.distance_to_pivot_pct.toFixed(1)}%)</div></td>
                          <td>{fmtINR(r.entry)}<div className="table-subtext q-bad">stop {fmtINR(r.stop)} (−{r.risk_pct.toFixed(1)}%)</div></td>
                          <td>
                            <span className={r.vcp.volume_dry_up ? "q-ok" : "q-bad"}>{r.vcp.volume_dry_up ? "Drying up" : "Not drying up"}</span>
                            <div className="table-subtext">up/down {r.vcp.up_down_volume ?? "—"}×</div>
                          </td>
                          <td>
                            <b>{r.score}/{r.score_max}</b>
                            <div className="vc-q">{Object.entries(r.quality).map(([k, v]) => <span key={k} className={`al-pt al-pt--${v ? "ok" : "bad"}`} title={Q_LABELS[k] ?? k}>{v ? "✓" : "✗"}</span>)}</div>
                          </td>
                          <td>
                            <div className="al-actions">
                              {breakout && <button className="mini-btn mini-btn--primary" onClick={() => logTrade(r)} disabled={held.has(r.symbol)}>{held.has(r.symbol) ? "Held" : "Log trade"}</button>}
                              <button className="mini-btn" onClick={() => watch(r)} disabled={watched.has(r.symbol)}>{watched.has(r.symbol) ? "Watching" : "Watch pivot"}</button>
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
        </>
      )}

      <section className="panel vc-panel">
        <div className="vc-mon-head">
          <h2>Post-buy monitoring</h2>
          <button className="mini-btn" onClick={() => void loadMonitor()} disabled={monitorLoading}>{monitorLoading ? "Checking…" : "Refresh"}</button>
        </div>
        <p className="muted vc-small" style={{ marginTop: 0 }}>How your open Journal positions are behaving since entry: follow-up buying, green vs red days, up vs down volume, tennis-ball recoveries and shallow pullbacks.</p>
        {!open.length ? <p className="muted">No open positions in your Journal.</p> : !monitor ? <p className="muted">{monitorLoading ? "Checking positions…" : "Couldn't load position data."}</p> : (
          <div className="table-shell">
            <table className="vc-tbl">
              <thead><tr><th>Position</th><th>Health</th><th>Green / red</th><th>Up/down vol</th><th>Follow-up</th><th>Max pullback</th><th>Recoveries</th><th>Checks</th></tr></thead>
              <tbody>
                {monitor.map((m) => (
                  <tr key={m.symbol}>
                    <td><Link className="stock-link" href={`/analyze/${m.symbol.replace(/\.NS$/, "")}`}>{m.symbol.replace(/\.NS$/, "")}</Link>
                      {m.available && <div className="table-subtext">{m.sessions} sessions · {m.gain_pct! >= 0 ? "+" : ""}{m.gain_pct}%</div>}</td>
                    {!m.available ? <td colSpan={7} className="muted">{m.reason}</td> : (
                      <>
                        <td><span className={`pill-tag pill-tag--${m.health === "strong" ? "ok" : m.health === "mixed" ? "warn" : "bad"}`}>{m.health}</span><div className="table-subtext">{m.score}/{m.applicable} checks</div></td>
                        <td>{m.green_days} / {m.red_days}</td>
                        <td>{m.up_down_volume ?? "—"}×</td>
                        <td>{m.follow_up_days} strong day{m.follow_up_days === 1 ? "" : "s"}<div className="table-subtext">first 5 sessions</div></td>
                        <td className={m.signals?.shallow_pullbacks ? "q-ok" : "q-bad"}>{m.max_pullback_pct}%<div className="table-subtext">shallow ≤ {m.shallow_limit_pct}%</div></td>
                        <td>{m.pullbacks_counted ? `${(m.pullbacks_counted ?? 0) - (m.slow_recoveries ?? 0)}/${m.pullbacks_counted} fast` : "no dips yet"}<div className="table-subtext">{m.avg_recovery_sessions != null ? `avg ${m.avg_recovery_sessions} sessions` : ""}</div></td>
                        <td><div className="vc-q">{Object.entries(m.signals ?? {}).map(([k, v]) => <span key={k} className={`al-pt al-pt--${v === null ? "na" : v ? "ok" : "bad"}`} title={SIGNAL_LABELS[k] ?? k}>{v === null ? "–" : v ? "✓" : "✗"}</span>)}</div></td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <style>{`
        .vc-head { display:grid; grid-template-columns:1fr 320px; gap:24px; align-items:start; margin-bottom:18px; }
        .vc-title { font-family:var(--font-space-grotesk),sans-serif; font-size:clamp(1.5rem,3.2vw,2.3rem); line-height:1.1; margin:8px 0 10px; }
        .vc-sub { max-width:72ch; line-height:1.6; margin:0; }
        .vc-controls { display:grid; gap:10px; }
        .vc-rules { margin-bottom:18px; }
        .vc-rules summary { cursor:pointer; }
        .vc-rules-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:18px; margin-top:12px; }
        .vc-rules h3 { font-size:0.92rem; margin:0 0 6px; }
        .vc-rules ul { margin:0; padding-left:18px; line-height:1.65; font-size:0.86rem; color:var(--muted); }
        .vc-panel { margin-bottom:20px; }
        .vc-panel h2 { font-size:1.1rem; margin:0 0 8px; }
        .vc-funnel { display:grid; grid-template-columns:repeat(5,1fr); gap:10px; }
        .vc-funnel div { background:rgba(255,255,255,0.6); border:1px solid var(--line); border-radius:14px; padding:10px 14px; }
        .vc-funnel span { display:block; font-size:0.74rem; color:var(--muted); }
        .vc-funnel strong { font-family:var(--font-space-grotesk),sans-serif; font-size:1.5rem; }
        .vc-small { font-size:0.8rem; margin:10px 0 0; }
        .vc-filter { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }
        .vc-tab { padding:7px 16px; border-radius:999px; border:1px solid var(--line); background:rgba(255,255,255,0.6); color:var(--muted); font:inherit; font-size:0.86rem; font-weight:600; cursor:pointer; }
        .vc-tab:hover { color:var(--text); border-color:rgba(241,104,0,0.3); }
        .vc-tab--on { background:linear-gradient(135deg,#f16800,#ff9a2f); color:#fff7ef; border-color:transparent; }
        .vc-notice { background:var(--accent-soft); color:var(--accent); border-radius:10px; padding:6px 12px; font-size:0.86rem; }
        .vc-tbl { width:100%; border-collapse:collapse; font-size:0.86rem; }
        .vc-tbl th { font-size:0.72rem; padding:8px; white-space:nowrap; }
        .vc-tbl td { padding:9px 8px; vertical-align:top; }
        .vc-depths { display:flex; flex-direction:column; gap:3px; }
        .vc-depth { display:block; min-width:42px; font-size:0.72rem; font-weight:600; color:#fff; background:linear-gradient(90deg,#f16800,#ff9a2f); border-radius:5px; padding:1px 6px; white-space:nowrap; }
        .vc-q { display:flex; gap:3px; margin-top:4px; flex-wrap:wrap; }
        .vc-mon-head { display:flex; justify-content:space-between; align-items:center; }
        .al-pt { width:18px; height:18px; border-radius:5px; display:grid; place-items:center; font-size:0.7rem; font-weight:700; }
        .al-pt--ok { background:rgba(22,108,90,0.14); color:var(--green); }
        .al-pt--bad { background:rgba(185,75,81,0.12); color:var(--red); }
        .al-pt--na { background:var(--bg-deep); color:var(--muted); }
        .al-actions { display:flex; flex-direction:column; gap:5px; }
        @media (max-width:960px) { .vc-head, .vc-rules-grid { grid-template-columns:1fr; } .vc-funnel { grid-template-columns:1fr 1fr; } }
      `}</style>
    </main>
  );
}
