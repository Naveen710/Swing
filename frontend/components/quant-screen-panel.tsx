"use client";

import Link from "next/link";
import { useState } from "react";

import { getQuantScreen } from "../lib/api";
import { exportWorkbook } from "../lib/excel";
import { fmtINR, useTradingSettings } from "../lib/store";
import { QuantScreenResponse, QuantStats, ScanUniverse } from "../types";

const UNIVERSES: { value: ScanUniverse; label: string }[] = [
  { value: "nifty500", label: "Nifty 500" },
  { value: "nifty_smallcap_250", label: "Smallcap 250" },
  { value: "mid_small_2000_plus", label: "Mid & Small 2000+" },
];

export function QuantScreenPanel() {
  const [settings] = useTradingSettings();
  const [universe, setUniverse] = useState<ScanUniverse>("nifty500");
  const [top, setTop] = useState(20);
  const [data, setData] = useState<QuantScreenResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(refresh = false) {
    setLoading(true);
    setError(null);
    try {
      setData(await getQuantScreen(universe, top, refresh));
    } catch {
      setError("The quant screen couldn't run. Check that the backend is up and try again.");
    } finally {
      setLoading(false);
    }
  }

  const factors = data ? Object.keys(data.factor_weights) : [];
  const v = data?.validation;

  function exportExcel() {
    if (!data) return;
    void exportWorkbook(`quant-screen-${data.universe}`, [
      {
        name: "Ranked picks",
        rows: data.picks.map((p) => ({
          Rank: p.rank, Symbol: p.symbol.replace(/\.NS$/, ""), Company: p.company_name, Sector: p.sector,
          "Price (₹)": p.price, "Composite score": p.score, "Percentile": p.percentile, "Weight %": p.weight_pct,
          "Allocation (₹)": Math.round((settings.capital * p.weight_pct) / 100), "Volatility %/yr": p.volatility_pct,
          ...Object.fromEntries(factors.map((f) => [`${data.factor_labels[f]} (z)`, p.factors[f]?.z ?? null])),
        })),
      },
      {
        name: "Validation",
        rows: v?.available ? [
          { Metric: "Period", Strategy: `${v.start} → ${v.end}`, Universe: null, Nifty: null },
          ...(["cagr_pct", "volatility_pct", "sharpe", "max_drawdown_pct", "total_return_pct"] as (keyof QuantStats)[]).map((k) => ({
            Metric: k, Strategy: v.strategy?.[k] ?? null, Universe: v.universe?.[k] ?? null, Nifty: v.benchmark?.[k] ?? null,
          })),
          { Metric: "Information coefficient (t-stat)", Strategy: v.ic_t_stat ?? null, Universe: null, Nifty: null },
          { Metric: "Excess return t-stat", Strategy: v.excess_t_stat ?? null, Universe: null, Nifty: null },
          { Metric: "Statistically significant", Strategy: v.significant ? "Yes" : "No", Universe: null, Nifty: null },
        ] : [{ Metric: "Validation", Strategy: v?.reason ?? "Unavailable", Universe: null, Nifty: null }],
      },
    ]);
  }

  return (
    <section className="panel qs-panel">
      <div className="qs-head">
        <div>
          <h2>Quant factor screen</h2>
          <p className="muted qs-intro">
            Ranks every stock in the universe on five well-researched factors at once, then tests how this exact ranking
            would have performed over the last two years. Inspired by how systematic funds work — combining many weak
            signals and validating them — not a copy of any fund&apos;s secret strategy.
          </p>
        </div>
      </div>

      <div className="qs-controls">
        <label className="field">Universe
          <select value={universe} onChange={(e) => setUniverse(e.target.value as ScanUniverse)}>
            {UNIVERSES.map((u) => <option key={u.value} value={u.value}>{u.label}</option>)}
          </select>
        </label>
        <label className="field">Stocks to hold
          <select value={top} onChange={(e) => setTop(Number(e.target.value))}>
            {[10, 15, 20, 30].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <button className="primary-button qs-run" onClick={() => void run(false)} disabled={loading}>
          {loading ? "Ranking the universe…" : "▶ Run quant screen"}
        </button>
        {data && !loading && <button className="mini-btn" onClick={() => void run(true)}>Recompute</button>}
        {data?.available && <button className="mini-btn" onClick={exportExcel}>Export to Excel</button>}
      </div>
      {loading && <p className="muted">Loading two years of prices for every stock — the first run can take a minute or two.</p>}
      {error && <p className="error-text">{error}</p>}
      {data && !data.available && <p className="error-text">{data.reason}</p>}

      {data?.available && (
        <>
          {v?.available ? (
            <div className={`qs-verdict ${v.significant ? "qs-verdict--ok" : "qs-verdict--warn"}`}>
              <strong>{v.significant ? "The ranking has shown a real edge." : "No proven edge yet — treat the list as research, not a buy list."}</strong>{" "}
              {v.significant
                ? `Over ${v.rebalances} weekly rebalances (${v.start} → ${v.end}) the top ${data.top_n} beat the equal-weight universe by a margin unlikely to be luck (t = ${v.excess_t_stat}).`
                : `Over ${v.rebalances} weekly rebalances the top picks returned ${v.strategy?.cagr_pct ?? "—"}%/yr after costs versus ${v.universe?.cagr_pct ?? "—"}%/yr for the whole universe; the difference is within what chance produces (t = ${v.excess_t_stat}). Factors go through long flat spells — check again after more data.`}
            </div>
          ) : (
            <div className="qs-verdict qs-verdict--warn">{v?.reason ?? "Validation unavailable."}</div>
          )}

          {v?.available && v.strategy && v.universe && (
            <div className="qs-val">
              <table className="qs-stats">
                <thead><tr><th></th><th>Quant top {data.top_n}</th><th>All {data.eligible} stocks</th><th>Nifty</th></tr></thead>
                <tbody>
                  {([["CAGR", "cagr_pct", "%+"], ["Volatility", "volatility_pct", "%"], ["Sharpe", "sharpe", ""], ["Max drawdown", "max_drawdown_pct", "%"]] as [string, keyof QuantStats, string][]).map(([label, k, unit]) => (
                    <tr key={k}>
                      <td>{label}</td>
                      <td><b>{fmt(v.strategy?.[k], unit)}</b></td>
                      <td>{fmt(v.universe?.[k], unit)}</td>
                      <td>{fmt(v.benchmark?.[k], unit)}</td>
                    </tr>
                  ))}
                  <tr><td>Beat the universe</td><td colSpan={3}>{Math.round((v.hit_rate ?? 0) * 100)}% of weeks · ranking accuracy (IC) {v.ic_mean?.toFixed(3)} (t = {v.ic_t_stat})</td></tr>
                </tbody>
              </table>
              {v.curve && <Curve points={v.curve} />}
            </div>
          )}

          <div className="table-shell">
            <table className="qs-tbl">
              <thead>
                <tr>
                  <th>#</th><th>Stock</th><th>Price</th><th>Percentile</th>
                  {factors.map((f) => <th key={f} title={`Weight ${Math.round(data.factor_weights[f] * 100)}%`}>{data.factor_labels[f]}</th>)}
                  <th>Weight</th><th>Allocation</th>
                </tr>
              </thead>
              <tbody>
                {data.picks.map((p) => (
                  <tr key={p.symbol}>
                    <td>{p.rank}</td>
                    <td>
                      <Link className="stock-link" href={`/analyze/${p.symbol.replace(/\.NS$/, "")}`}>{p.symbol.replace(/\.NS$/, "")}</Link>
                      <div className="table-subtext">{p.company_name} · {p.sector}</div>
                    </td>
                    <td>{fmtINR(p.price)}</td>
                    <td><b>{p.percentile.toFixed(0)}</b></td>
                    {factors.map((f) => <td key={f}><ZBar z={p.factors[f]?.z ?? null} /></td>)}
                    <td>{p.weight_pct.toFixed(1)}%<div className="table-subtext">vol {p.volatility_pct.toFixed(0)}%</div></td>
                    <td>{fmtINR((settings.capital * p.weight_pct) / 100)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <details className="qs-rules">
            <summary>How the score is built</summary>
            <ul>
              {factors.map((f) => <li key={f}><b>{data.factor_labels[f]}</b> — {Math.round(data.factor_weights[f] * 100)}% of the score</li>)}
              {data.rules.map((r) => <li key={r}>{r}</li>)}
              <li>Each factor is converted to a z-score across all eligible stocks every day (capped at ±3), so a bar to the right means better than the average stock.</li>
              <li>Validation includes {v?.cost_pct_round_trip ?? 0.25}% costs per round trip on turnover; only currently listed stocks are tested (survivorship bias flatters results).</li>
              <li>Allocations assume your journal capital of {fmtINR(settings.capital)} split across all picks — this is a basket approach, separate from the swing-trade system.</li>
            </ul>
          </details>
          <p className="muted qs-foot">As of {data.as_of} · {data.loaded} stocks loaded, {data.eligible} passed the liquidity filter. Not investment advice.</p>
        </>
      )}

      <style>{`
        .qs-panel { margin-top:24px; }
        .qs-intro { max-width:80ch; margin:4px 0 14px; font-size:0.9rem; line-height:1.6; }
        .qs-controls { display:flex; flex-wrap:wrap; align-items:flex-end; gap:12px; margin-bottom:14px; }
        .qs-controls .field { min-width:170px; }
        .qs-run { padding:10px 22px; }
        .qs-verdict { border-radius:14px; padding:12px 16px; font-size:0.9rem; line-height:1.55; margin-bottom:14px; }
        .qs-verdict--ok { background:rgba(22,108,90,0.08); border:1px solid rgba(22,108,90,0.25); }
        .qs-verdict--warn { background:rgba(214,150,0,0.09); border:1px solid rgba(214,150,0,0.3); }
        .qs-val { display:grid; grid-template-columns:minmax(300px,1fr) 1.2fr; gap:18px; align-items:start; margin-bottom:18px; }
        .qs-stats { width:100%; border-collapse:collapse; font-size:0.87rem; }
        .qs-stats th, .qs-stats td { padding:7px 8px; border-bottom:1px solid var(--line); text-align:right; }
        .qs-stats th:first-child, .qs-stats td:first-child { text-align:left; color:var(--muted); }
        .qs-curve { width:100%; height:200px; background:rgba(255,255,255,0.55); border:1px solid var(--line); border-radius:14px; }
        .qs-legend { display:flex; gap:14px; font-size:0.78rem; color:var(--muted); margin-top:6px; }
        .qs-legend i { display:inline-block; width:12px; height:3px; margin-right:5px; vertical-align:middle; }
        .qs-tbl { width:100%; border-collapse:collapse; font-size:0.86rem; }
        .qs-tbl th { font-size:0.72rem; padding:9px 8px; white-space:nowrap; }
        .qs-tbl td { padding:9px 8px; vertical-align:top; }
        .qs-z { display:flex; align-items:center; gap:6px; min-width:92px; }
        .qs-z-track { position:relative; width:56px; height:6px; background:var(--bg-deep); border-radius:3px; }
        .qs-z-mid { position:absolute; left:50%; top:-2px; width:1px; height:10px; background:var(--muted); opacity:0.5; }
        .qs-z-fill { position:absolute; top:0; height:6px; border-radius:3px; }
        .qs-z-num { font-size:0.75rem; color:var(--muted); width:30px; }
        .qs-rules { margin-top:14px; font-size:0.86rem; }
        .qs-rules summary { cursor:pointer; font-weight:600; }
        .qs-rules ul { line-height:1.65; color:var(--muted); }
        .qs-foot { font-size:0.78rem; margin:10px 0 0; }
        @media (max-width:960px) { .qs-val { grid-template-columns:1fr; } }
      `}</style>
    </section>
  );
}

function fmt(v: number | null | undefined, unit: string) {
  if (v === null || v === undefined) return "—";
  const signed = unit === "%+";
  return `${signed && v > 0 ? "+" : ""}${v.toFixed(unit ? 1 : 2)}${unit ? "%" : ""}`;
}

function ZBar({ z }: { z: number | null }) {
  if (z === null) return <span className="table-subtext">—</span>;
  const width = (Math.min(Math.abs(z), 3) / 3) * 50;
  const color = z >= 0 ? "#2f8a64" : "#b94b51";
  return (
    <div className="qs-z" title={`z = ${z}`}>
      <div className="qs-z-track">
        <div className="qs-z-mid" />
        <div className="qs-z-fill" style={{ left: z >= 0 ? "50%" : `${50 - width}%`, width: `${width}%`, background: color }} />
      </div>
      <span className="qs-z-num">{z > 0 ? "+" : ""}{z.toFixed(1)}</span>
    </div>
  );
}

function Curve({ points }: { points: { date: string; strategy: number; universe: number; benchmark: number }[] }) {
  const W = 600, H = 200, P = 8;
  const all = points.flatMap((p) => [p.strategy, p.universe, p.benchmark]).filter((x) => Number.isFinite(x));
  const lo = Math.min(...all), hi = Math.max(...all);
  const x = (i: number) => P + (i / Math.max(points.length - 1, 1)) * (W - 2 * P);
  const y = (v: number) => H - P - ((v - lo) / Math.max(hi - lo, 1e-9)) * (H - 2 * P);
  const line = (key: "strategy" | "universe" | "benchmark") => points.map((p, i) => `${x(i)},${y(p[key])}`).join(" ");
  return (
    <div>
      <svg className="qs-curve" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Growth of 100: quant top picks vs universe vs Nifty">
        <polyline points={line("benchmark")} fill="none" stroke="#9a948c" strokeWidth="1.5" />
        <polyline points={line("universe")} fill="none" stroke="#3659a2" strokeWidth="1.5" strokeDasharray="4 3" />
        <polyline points={line("strategy")} fill="none" stroke="#f16800" strokeWidth="2.5" />
      </svg>
      <div className="qs-legend">
        <span><i style={{ background: "#f16800" }} />Quant top picks</span>
        <span><i style={{ background: "#3659a2" }} />All eligible stocks</span>
        <span><i style={{ background: "#9a948c" }} />Nifty</span>
        <span>Growth of ₹100, {points[0]?.date} → {points[points.length - 1]?.date}</span>
      </div>
    </div>
  );
}
