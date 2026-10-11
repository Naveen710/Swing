"use client";

import Link from "next/link";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getMultibagger,
  getMultibaggerBacktest,
  getMultibaggerLedger,
  getMultibaggerStatus,
  getMultibaggerStock,
  startMultibagger,
  startMultibaggerBacktest,
} from "../lib/api";
import { CHECKLIST, THESIS_BROKEN, adjustedScore, sizing, starterAllowed } from "../lib/multibagger";
import {
  ChecklistAnswer,
  fmtINR,
  useMultibaggerChecklist,
  useMultibaggerExclude,
  useMultibaggerSettings,
  useTradingSettings,
} from "../lib/store";
import {
  MultibaggerBacktest,
  MultibaggerLayerKey,
  MultibaggerLedger,
  MultibaggerResult,
  MultibaggerRow,
  MultibaggerStatus,
  MultibaggerStockResult,
  ScanUniverse,
} from "../types";
import { AppNav } from "./app-nav";

const UNIVERSES: { value: ScanUniverse; label: string }[] = [
  { value: "nifty_smallcap_250", label: "Smallcap 250" },
  { value: "nifty500", label: "Nifty 500 (ex Nifty 100)" },
  { value: "mid_small_2000_plus", label: "Mid & Small 2000+" },
];
const LAYERS: { key: MultibaggerLayerKey; label: string; short: string }[] = [
  { key: "growth", label: "Growth & inflection", short: "Growth" },
  { key: "quality", label: "Quality", short: "Quality" },
  { key: "valuation", label: "Valuation sanity", short: "Value" },
  { key: "tailwind", label: "Tailwind & leadership", short: "Tailwind" },
  { key: "price", label: "Price confirmation", short: "Price" },
];
const TIMING_TONE: Record<string, string> = { entry_zone: "ok", watch: "muted", extended: "warn", downtrend: "bad" };
const TIER_TONE: Record<string, string> = { A: "ok", B: "ok", C: "warn", D: "muted", "✕": "bad" };
const ELIM_LABELS: Record<string, string> = {
  large_cap: "Nifty 100", penny: "Price < ₹20", illiquid: "< ₹1 Cr/day", short_history: "< 2 yrs history",
  circuit: "Circuit-prone", asm_gsm: "ASM/GSM or excluded", size_band: "Outside ₹500–20,000 Cr", size_unknown: "Size unknown",
  financial: "Bank/NBFC/insurer", loss_making: "Loss-making", high_debt: "D/E > 1", negative_cfo: "Cash burn",
  dilution: "Dilution > 25%", insufficient_data: "Insufficient data",
};
const SIZING_RULES = [
  "Starter = ⅓ of a full position, only for tier A/B in the Entry zone or Watch.",
  "Add ⅓ after the next quarterly result confirms (revenue and profit YoY ≥ 15%) and price is above the 200-day average.",
  "Add the final ⅓ on a new Entry-zone signal (base breakout) while the tier is still A or B.",
  "Cap each idea at 12% of the bucket at cost; trim back toward 15% when it grows past 20%. At most 3 ideas per sector and 15 in total.",
  "Starter stop: −30% from starter cost before any add — the thesis is unproven, so the starter is exited.",
];
const THESIS_RULES = [
  "TB1 · Revenue down YoY two quarters running → break",
  "TB2 · Latest quarter profit down > 20% YoY → warning (two quarters → break)",
  "TB3 · ROCE below 12% after being ≥ 18% two years earlier → break",
  "TB4 · Latest year operating cash flow negative → warning",
  "TB5 · Debt/equity above 1.0 → break",
  "TB6 · 4 weekly closes below the 200-day average and RS line at a 52-week low → warning, cut to starter size",
  "TB7 · Market prices > 15 pp more growth than delivered → warning, trim to half",
  "TB8 · A manual hard stop ticked → break",
  "Tier D in two consecutive monthly snapshots → warning",
];

const pct = (v: number | null | undefined, dp = 1) => (v === null || v === undefined ? "—" : `${v.toFixed(dp)}%`);
const num = (v: number | null | undefined, dp = 2) => (v === null || v === undefined ? "—" : v.toFixed(dp));
const base = (s: string) => s.replace(/\.NS$/, "");

function LayerBar({ score }: { score: number }) {
  const tone = score >= 70 ? "ok" : score >= 45 ? "mid" : "low";
  return (
    <span className="mb-bar" title={`${score.toFixed(0)} / 100`}>
      <span className={`mb-bar-fill mb-bar-fill--${tone}`} style={{ width: `${Math.max(3, Math.min(100, score))}%` }} />
      <span className="mb-bar-num">{score.toFixed(0)}</span>
    </span>
  );
}

function StatusChip({ status }: { status: "ok" | "neutral" | "penalised" }) {
  if (status === "ok") return <span className="pill-tag pill-tag--ok">ok</span>;
  if (status === "neutral") return <span className="pill-tag pill-tag--muted">no data · 50</span>;
  return <span className="pill-tag pill-tag--bad">no data · 25</span>;
}

function Checklist({ symbol, score, capped }: { symbol: string; score: number; capped: boolean }) {
  const [checklist, setChecklist] = useMultibaggerChecklist();
  const items = checklist[symbol]?.items ?? {};
  const adj = adjustedScore(score, items, capped);
  function set(id: string, answer: ChecklistAnswer) {
    setChecklist((prev) => ({
      ...prev,
      [symbol]: { items: { ...(prev[symbol]?.items ?? {}), [id]: answer }, updated: new Date().toISOString() },
    }));
  }
  return (
    <div className="mb-check">
      <div className="mb-check-head">
        <h4>Manual checklist</h4>
        <span>
          Adjusted score <b>{adj.score.toFixed(1)}</b>{" "}
          <span className={`pill-tag pill-tag--${adj.hardStop ? "bad" : TIER_TONE[adj.tier]}`}>{adj.hardStop ? THESIS_BROKEN : `${adj.tier} · ${adj.label}`}</span>
        </span>
      </div>
      <p className="muted mb-small">
        Things the app can&apos;t fetch for free. Look them up on NSE → Corporate filings → Shareholding pattern / Annual report.
        Changes only the adjusted score; the system score stays as computed. Saved in this browser.
      </p>
      <ul className="mb-check-list">
        {CHECKLIST.map((c) => {
          const a = items[c.id] ?? "unknown";
          return (
            <li key={c.id} className={`mb-check-item mb-check-item--${c.kind}`}>
              <span className="mb-check-text">
                {c.text}{" "}
                <span className="muted">({c.effect === "hard_stop" ? "hard stop" : c.effect > 0 ? `+${c.effect}` : c.effect})</span>
              </span>
              <span className="mb-tri" role="group" aria-label={c.text}>
                {(["yes", "no", "unknown"] as ChecklistAnswer[]).map((v) => (
                  <button key={v} type="button" className={`mb-tri-btn ${a === v ? "mb-tri-btn--on" : ""}`} onClick={() => set(c.id, v)}>
                    {v === "unknown" ? "?" : v === "yes" ? "Yes" : "No"}
                  </button>
                ))}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function StockCard({ row }: { row: MultibaggerRow }) {
  return (
    <div className="mb-card">
      <div className="mb-card-grid">
        {LAYERS.map((L) => {
          const layer = row.layers[L.key];
          return (
            <div key={L.key} className="mb-layer">
              <div className="mb-layer-head">
                <b>{L.label}</b>
                <span className="muted">weight {layer.weight} · {layer.coverage_pct.toFixed(0)}% data</span>
                <LayerBar score={layer.score} />
              </div>
              <table className="mb-metrics">
                <tbody>
                  {layer.metrics.map((m) => (
                    <tr key={m.key}>
                      <td>{m.label}{m.note ? <div className="table-subtext">{m.note}</div> : null}</td>
                      <td className="mb-num">{m.display}</td>
                      <td className="mb-num">{m.score.toFixed(0)}</td>
                      <td><StatusChip status={m.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })}
      </div>
      <div className="mb-card-grid mb-card-grid--3">
        <div><h4>Why it qualifies</h4>{row.qualifies.length ? <ul>{row.qualifies.map((q) => <li key={q} className="q-ok">{q}</li>)}</ul> : <p className="muted">Nothing scores 75+ yet.</p>}</div>
        <div><h4>Red flags</h4>{row.red_flags.length ? <ul>{row.red_flags.map((q) => <li key={q} className="q-bad">{q}</li>)}</ul> : <p className="muted">None found by the rules.</p>}</div>
        <div><h4>Data gaps</h4>{row.data_gaps.length ? <ul>{row.data_gaps.map((q) => <li key={q} className="muted">{q}</li>)}</ul> : <p className="muted">Full data.</p>}</div>
      </div>
      <div className="mb-card-grid mb-card-grid--2">
        <div>
          <h4>Thesis check</h4>
          <ul className="mb-thesis">
            {row.thesis_check.map((t) => (
              <li key={t.code}>
                <span className={`pill-tag pill-tag--${t.status === "ok" ? "ok" : t.status === "warning" ? "warn" : t.status === "break" ? "bad" : "muted"}`}>{t.code} · {t.status === "na" ? "n/a" : t.status}</span> {t.text}
              </li>
            ))}
          </ul>
          <p className="muted mb-small">Timing: <b>{row.timing_label}</b> — {row.timing_reason}{row.pivot ? ` · VCP pivot ₹${row.pivot}` : ""}.</p>
          <div className="mb-links">
            <Link className="mini-btn" href={`/valuation/${base(row.symbol)}`}>Open DCF</Link>
            <Link className="mini-btn" href={`/analyze/${base(row.symbol)}`}>Full analysis</Link>
          </div>
        </div>
        <Checklist symbol={row.symbol} score={row.score} capped={row.capped_by_weak_layer} />
      </div>
    </div>
  );
}

function AdjustedCell({ row }: { row: MultibaggerRow }) {
  const [checklist] = useMultibaggerChecklist();
  const items = checklist[row.symbol]?.items;
  if (!items) return <span className="muted">—</span>;
  const adj = adjustedScore(row.score, items, row.capped_by_weak_layer);
  return <span className={adj.hardStop ? "q-bad" : ""} title={adj.label}>{adj.hardStop ? "✕" : adj.score.toFixed(1)}</span>;
}

export function MultibaggerShell() {
  const [universe, setUniverse] = useState<ScanUniverse>("nifty_smallcap_250");
  const [data, setData] = useState<MultibaggerResult | null>(null);
  const [noRun, setNoRun] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<MultibaggerStatus | null>(null);
  const [refresh, setRefresh] = useState(false);
  const [exclude, setExclude] = useMultibaggerExclude();
  const [tier, setTier] = useState("all");
  const [timing, setTiming] = useState("all");
  const [sector, setSector] = useState("all");
  const [hideGaps, setHideGaps] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [checkSymbol, setCheckSymbol] = useState("");
  const [checked, setChecked] = useState<MultibaggerStockResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [bt, setBt] = useState<MultibaggerBacktest | null>(null);
  const [ledger, setLedger] = useState<MultibaggerLedger | null>(null);
  const [settings] = useTradingSettings();
  const [mbSettings, setMbSettings] = useMultibaggerSettings();
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (u: ScanUniverse) => {
    setError(null);
    try {
      const res = await getMultibagger(u);
      setData(res);
      setNoRun(res === null);
    } catch {
      setError("Couldn't reach the backend. On the free plan it can take a minute to wake up — try again shortly.");
    }
    getMultibaggerBacktest(u).then(setBt).catch(() => setBt(null));
    getMultibaggerLedger(u).then(setLedger).catch(() => setLedger(null));
  }, []);

  const stopPolling = () => { if (poll.current) { clearInterval(poll.current); poll.current = null; } };
  const startPolling = useCallback(() => {
    stopPolling();
    poll.current = setInterval(async () => {
      try {
        const s = await getMultibaggerStatus();
        setStatus(s);
        if (!s.running) {
          stopPolling();
          if (s.stage === "error") setError(`The ${s.job === "backtest" ? "backtest" : "screen"} failed: ${s.error ?? "unknown error"}`);
          if (s.universe) void load(s.universe);
        }
      } catch { /* backend waking up — keep polling */ }
    }, 3000);
  }, [load]);

  useEffect(() => {
    void load(universe);
    getMultibaggerStatus().then((s) => { setStatus(s); if (s.running) startPolling(); }).catch(() => undefined);
    return stopPolling;
  }, [universe, load, startPolling]);

  async function run() {
    setError(null);
    try {
      const excl = exclude.split(/[\s,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
      const res = await startMultibagger(universe, excl, refresh);
      setStatus(res.state);
      if (!res.started) setError(res.state.reason ?? "Another multibagger job is already running.");
      startPolling();
    } catch {
      setError("Couldn't start the screen. Check that the backend is up.");
    }
  }
  async function runBacktest() {
    setError(null);
    try {
      const res = await startMultibaggerBacktest(universe);
      setStatus(res.state);
      if (!res.started) setError(res.state.reason ?? "Another multibagger job is already running.");
      startPolling();
    } catch {
      setError("Couldn't start the backtest.");
    }
  }
  async function checkStock() {
    if (!checkSymbol.trim()) return;
    setChecking(true);
    setCheckError(null);
    setChecked(null);
    try {
      setChecked(await getMultibaggerStock(checkSymbol));
    } catch (e) {
      const msg = e instanceof Error ? e.message : "";
      setCheckError(msg.endsWith("404") || msg.endsWith("400")
        ? `No price data for ${checkSymbol.toUpperCase()}. Use the NSE symbol, e.g. DIXON.`
        : "The check didn't finish — the backend may be waking up or busy with a screen. Try again in a minute.");
    } finally {
      setChecking(false);
    }
  }

  const sectors = useMemo(() => Array.from(new Set((data?.results ?? []).map((r) => r.sector))).sort(), [data]);
  const rows = (data?.results ?? []).filter((r) =>
    (tier === "all" || r.tier === tier) && (timing === "all" || r.timing === timing) &&
    (sector === "all" || r.sector === sector) && (!hideGaps || r.coverage_pct >= 100));
  const size = sizing(settings.capital, mbSettings.bucketPct, mbSettings.ideas);
  const running = !!status?.running;

  return (
    <main className="page-shell">
      <AppNav />
      <section className="mb-head">
        <div>
          <p className="eyebrow">Multibagger framework {data?.demo && <span className="pill-tag pill-tag--warn">demo data</span>}</p>
          <h1 className="mb-title">3–5 year ideas: small, clean, compounding, early.</h1>
          <p className="muted mb-sub">
            Kill-switches remove companies that are too small or too big, loss-making, over-borrowed, burning cash or diluting.
            The rest get a 0–100 score from five layers, plus a separate timing flag. This is a long-term bucket,
            kept apart from your swing trades.
          </p>
        </div>
        <div className="panel mb-controls">
          <label className="field">Universe
            <select value={universe} onChange={(e) => { setUniverse(e.target.value as ScanUniverse); setOpen(null); }} disabled={running}>
              {UNIVERSES.map((u) => <option key={u.value} value={u.value}>{u.label}</option>)}
            </select>
          </label>
          <label className="field">Exclude (ASM/GSM or your own list)
            <textarea rows={2} value={exclude} onChange={(e) => setExclude(e.target.value)} placeholder="SYMBOL1, SYMBOL2" />
          </label>
          <label className="mb-inline"><input type="checkbox" checked={refresh} onChange={(e) => setRefresh(e.target.checked)} /> Refresh fundamentals (ignore the 7-day cache)</label>
          <button className="primary-button" onClick={() => void run()} disabled={running}>
            {running && status?.job === "run" ? "Screening…" : data ? "↻ Re-run screen" : "▶ Run screen"}
          </button>
        </div>
      </section>

      <section className="mb-disclaimer">
        <b>Educational research tool, not investment advice.</b> A high score means a stock matches these rules today, not that it will rise. Do your own research.
      </section>

      {running && status && (
        <section className="panel mb-progress">
          <div className="mb-progress-row">
            <span>{status.job === "backtest" ? "Price backtest" : "Screen"} · {status.stage}{status.detail ? ` · ${status.detail}` : ""}</span>
            <b>{status.progress ?? 0}%</b>
          </div>
          <div className="mb-progress-bar"><span style={{ width: `${status.progress ?? 0}%` }} /></div>
          <p className="muted mb-small">The first run fetches company financials for up to 120 stocks (about 3–4 minutes). Later runs reuse a 7-day cache.</p>
        </section>
      )}
      {error && <section className="panel"><p className="error-text" style={{ margin: 0 }}>{error}</p></section>}
      {noRun && !running && <section className="panel"><p className="muted" style={{ margin: 0 }}>No run yet for this universe. Press <b>Run screen</b> (about 3–4 minutes the first time).</p></section>}

      {data && (
        <>
          <section className="panel mb-panel">
            <div className="mb-funnel">
              {[
                ["Universe", data.funnel.universe_count],
                ["Ex Nifty 100", data.funnel.after_size_bucket],
                ["Pass price filter", data.funnel.after_prefilter],
                ["Fundamentals checked", data.funnel.deep_pass],
                ["Pass kill-switches", data.funnel.passed_kill_switches],
                ["Scored", data.funnel.tiered],
              ].map(([label, n], i) => (
                <div key={label as string} className="mb-funnel-step">
                  <span>{label}</span><strong>{n}</strong>{i < 5 && <i aria-hidden>›</i>}
                </div>
              ))}
            </div>
            <div className="q-badges">
              {Object.entries(data.eliminated_by).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
                <span key={k} className="q-badge q-badge--bad">{ELIM_LABELS[k] ?? k}: {v}</span>
              ))}
            </div>
            <p className="muted mb-small">As of {data.as_of}. {data.notes.join(" ")}</p>
          </section>

          <section className="panel mb-panel">
            <div className="mb-filters">
              <div className="mb-tabs">
                {["all", "A", "B", "C", "D"].map((t) => (
                  <button key={t} className={`mb-tab ${tier === t ? "mb-tab--on" : ""}`} onClick={() => setTier(t)}>
                    {t === "all" ? `All (${data.results.length})` : `${t} (${data.results.filter((r) => r.tier === t).length})`}
                  </button>
                ))}
              </div>
              <div className="mb-tabs">
                {[["all", "Any timing"], ["entry_zone", "Entry zone"], ["watch", "Watch"], ["extended", "Extended"], ["downtrend", "Downtrend"]].map(([k, l]) => (
                  <button key={k} className={`mb-tab ${timing === k ? "mb-tab--on" : ""}`} onClick={() => setTiming(k)}>{l}</button>
                ))}
              </div>
              <div className="mb-filter-row">
                <select value={sector} onChange={(e) => setSector(e.target.value)} aria-label="Sector">
                  <option value="all">All sectors</option>
                  {sectors.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
                <label className="mb-inline"><input type="checkbox" checked={hideGaps} onChange={(e) => setHideGaps(e.target.checked)} /> Hide rows with data gaps</label>
              </div>
            </div>
            {rows.length === 0 ? <p className="muted">No stocks match these filters.</p> : (
              <div className="table-shell mb-tshell">
                <table className="mb-tbl">
                  <thead>
                    <tr>
                      <th>#</th><th>Stock</th><th>Mcap ₹Cr</th><th>Score</th><th>Adj.</th><th>Tier</th><th>Timing</th>
                      {LAYERS.map((L) => <th key={L.key}>{L.short}</th>)}
                      <th>Rev 3y</th><th>PAT 3y</th><th>ROCE</th><th>D/E</th><th>Priced vs delivered</th><th>Below high</th><th>Data</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <Fragment key={r.symbol}>
                        <tr className={`mb-row ${open === r.symbol ? "mb-row--open" : ""}`} onClick={() => setOpen(open === r.symbol ? null : r.symbol)}>
                          <td>{r.rank}</td>
                          <td>
                            <Link className="stock-link" href={`/analyze/${base(r.symbol)}`} onClick={(e) => e.stopPropagation()}>{base(r.symbol)}</Link>
                            <div className="table-subtext">{r.company_name} · {r.sector}</div>
                          </td>
                          <td className="mb-num">{r.market_cap_cr === null ? "—" : r.market_cap_cr.toLocaleString("en-IN")}</td>
                          <td className="mb-num"><b>{r.score.toFixed(1)}</b></td>
                          <td className="mb-num"><AdjustedCell row={r} /></td>
                          <td>
                            <span className={`pill-tag pill-tag--${TIER_TONE[r.tier]}`} title={r.tier_label}>{r.tier}</span>
                            {r.capped_by_weak_layer && <div className="table-subtext">capped: weak {r.weak_layer}</div>}
                          </td>
                          <td><span className={`pill-tag pill-tag--${TIMING_TONE[r.timing]}`} title={r.timing_reason}>{r.timing_label}</span></td>
                          {LAYERS.map((L) => <td key={L.key}><LayerBar score={r.layers[L.key].score} /></td>)}
                          <td className="mb-num">{pct(r.key.rev_cagr_pct)}</td>
                          <td className="mb-num">{pct(r.key.pat_cagr_pct)}</td>
                          <td className="mb-num">{pct(r.key.roce_pct)}</td>
                          <td className="mb-num">{num(r.key.de)}</td>
                          <td className="mb-num">
                            {r.reverse_dcf.implied_growth_pct === null ? "—" : `${r.reverse_dcf.implied_growth_pct.toFixed(0)}% vs ${r.reverse_dcf.delivered_growth_pct?.toFixed(0) ?? "—"}%`}
                          </td>
                          <td className="mb-num">{pct(r.distance_to_52w_high_pct, 0)}</td>
                          <td className="mb-num">
                            {r.coverage_pct >= 100 ? "100%" : <span title={r.data_gaps.join("\n")}><span className="mb-dot" />{r.coverage_pct.toFixed(0)}%</span>}
                          </td>
                        </tr>
                        {open === r.symbol && (
                          <tr className="mb-expand"><td colSpan={19}><StockCard row={r} /></td></tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="muted mb-small">Click a row for the full breakdown, red flags, thesis check and the manual checklist.</p>
          </section>

          {(data.excluded.length > 0 || data.insufficient.length > 0) && (
            <section className="panel mb-panel">
              {data.excluded.length > 0 && (
                <details>
                  <summary><b>Excluded by kill-switch ({data.excluded.length})</b></summary>
                  <div className="table-shell"><table className="mb-tbl mb-tbl--plain">
                    <thead><tr><th>Stock</th><th>Rule</th><th>Detail</th></tr></thead>
                    <tbody>{data.excluded.map((x) => (
                      <tr key={x.symbol}><td><b>{base(x.symbol)}</b><div className="table-subtext">{x.company_name}</div></td>
                        <td><span className="q-badge q-badge--bad">{ELIM_LABELS[x.code] ?? x.code}</span></td><td>{x.detail}</td></tr>
                    ))}</tbody>
                  </table></div>
                </details>
              )}
              {data.insufficient.length > 0 && (
                <details>
                  <summary><b>Insufficient data ({data.insufficient.length})</b></summary>
                  <div className="table-shell"><table className="mb-tbl mb-tbl--plain">
                    <thead><tr><th>Stock</th><th>Reason</th><th>Gaps</th></tr></thead>
                    <tbody>{data.insufficient.map((x) => (
                      <tr key={x.symbol}><td><b>{base(x.symbol)}</b><div className="table-subtext">{x.company_name}</div></td>
                        <td>{x.reason}</td><td className="muted">{x.data_gaps.join("; ") || "—"}</td></tr>
                    ))}</tbody>
                  </table></div>
                </details>
              )}
            </section>
          )}
        </>
      )}

      <section className="panel mb-panel">
        <h2>Check any stock</h2>
        <p className="muted mb-small" style={{ marginTop: 0 }}>Scores a single stock with the same rules, even if the screen didn&apos;t reach it (it only checks the top 120 by momentum). Kill-switch failures are shown, not hidden.</p>
        <form className="mb-check-form" onSubmit={(e) => { e.preventDefault(); void checkStock(); }}>
          <input value={checkSymbol} onChange={(e) => setCheckSymbol(e.target.value)} placeholder="NSE symbol, e.g. DIXON" aria-label="NSE symbol" />
          <button className="primary-button" type="submit" disabled={checking}>{checking ? "Checking…" : "Check"}</button>
        </form>
        {checkError && <p className="error-text">{checkError}</p>}
        {checked && (
          <div className="mb-checked">
            <p>
              <b>{base(checked.symbol)}</b> · {checked.company_name}{" "}
              {checked.status === "scored" && <>— score <b>{checked.score.toFixed(1)}</b>{" "}
                <span className={`pill-tag pill-tag--${TIER_TONE[checked.tier]}`}>{checked.tier} · {checked.tier_label}</span>{" "}
                <span className={`pill-tag pill-tag--${TIMING_TONE[checked.timing]}`}>{checked.timing_label}</span></>}
              {checked.status === "excluded" && <> — <span className="q-badge q-badge--bad">{ELIM_LABELS[checked.code] ?? checked.code}</span> {checked.detail}</>}
              {checked.status === "insufficient" && <> — not scored: {checked.reason}</>}
            </p>
            {checked.notes.length > 0 && <p className="muted mb-small">{checked.notes.join(" ")}</p>}
            {checked.status === "scored" && <StockCard row={checked} />}
          </div>
        )}
      </section>

      <section className="mb-side">
        <details className="panel" open>
          <summary><b>How the score works</b></summary>
          <ul className="mb-rules">{(data?.rules ?? [
            "Run the screen to load the rules from the server.",
          ]).map((r) => <li key={r}>{r}</li>)}</ul>
        </details>
        <div className="panel">
          <h2>Position sizing — long-term bucket</h2>
          <div className="mb-size-inputs">
            <label className="field">Bucket % of capital
              <input type="number" min={0} max={100} value={mbSettings.bucketPct} onChange={(e) => setMbSettings({ ...mbSettings, bucketPct: Number(e.target.value) || 0 })} />
            </label>
            <label className="field">Target ideas (8–15)
              <input type="number" min={8} max={15} value={mbSettings.ideas} onChange={(e) => setMbSettings({ ...mbSettings, ideas: Number(e.target.value) || 10 })} />
            </label>
          </div>
          <div className="mb-size-grid">
            <div><span>Capital</span><b>{fmtINR(settings.capital)}</b></div>
            <div><span>Bucket</span><b>{fmtINR(size.bucket)}</b></div>
            <div><span>Full position</span><b>{fmtINR(size.fullPosition)}</b></div>
            <div><span>Starter (⅓)</span><b>{fmtINR(size.starter)}</b></div>
            <div><span>Max per idea at cost</span><b>{fmtINR(size.maxAtCost)}</b></div>
            <div><span>Trim above</span><b>{fmtINR(size.trimAbove)}</b></div>
          </div>
          <ul className="mb-rules">{SIZING_RULES.map((r) => <li key={r}>{r}</li>)}</ul>
          {data && <p className="muted mb-small">{data.results.filter((r) => starterAllowed(r.tier, r.timing)).length} stocks currently meet the starter rule (tier A/B, Entry zone or Watch) — rules, not recommendations. Capital comes from your Journal settings.</p>}
        </div>
        <details className="panel">
          <summary><b>Thesis-break rules</b></summary>
          <ul className="mb-rules">{THESIS_RULES.map((r) => <li key={r}>{r}</li>)}</ul>
        </details>
      </section>

      <section className="panel mb-panel">
        <div className="mb-val-head">
          <h2>Validation</h2>
          <button className="mini-btn mini-btn--primary" onClick={() => void runBacktest()} disabled={running}>
            {running && status?.job === "backtest" ? "Backtesting…" : bt ? "↻ Re-run price backtest" : "▶ Run price backtest"}
          </button>
        </div>
        <p className="muted mb-small" style={{ marginTop: 0 }}>
          Only the price half of the score (Tailwind + Price) can be backtested; the fundamental layers have no free point-in-time history.
          Thresholds were fixed before any result was seen. The forward ledger below is the real test.
        </p>
        {!bt ? <p className="muted">No price backtest yet for this universe. It loads prices back to 2015 (a few minutes).</p> : (
          <div className="mb-bt">
            <p>
              <span className={`pill-tag pill-tag--${bt.verdict === "PASS" ? "ok" : bt.verdict === "FAIL" ? "bad" : "warn"}`}>{bt.verdict}</span>{" "}
              {bt.role === "secondary" ? "Secondary universe (relaxed thresholds)" : "Primary universe"} · {bt.cohorts_total} monthly cohorts from {bt.start} · {bt.symbols} stocks
              {bt.demo && <> · <span className="pill-tag pill-tag--warn">demo data</span></>}
            </p>
            {bt.verdict === "FAIL" && <p className="mb-warn">Price layers did not validate on this universe. Per the spec, their weight drops from 30 to 10 in a follow-up change; treat the timing flag as information only.</p>}
            <div className="table-shell"><table className="mb-tbl mb-tbl--plain">
              <thead><tr><th>Criterion</th><th>Threshold</th><th>Result</th><th></th></tr></thead>
              <tbody>{bt.criteria.map((c) => (
                <tr key={c.name}><td>{c.name}</td><td>{c.threshold}</td><td className="mb-num">{c.value ?? "—"}</td>
                  <td><span className={`pill-tag pill-tag--${c.pass ? "ok" : "bad"}`}>{c.pass ? "pass" : "fail"}</span></td></tr>
              ))}</tbody>
            </table></div>
            <div className="table-shell"><table className="mb-tbl mb-tbl--plain">
              <thead><tr><th>Horizon</th><th>Cohorts</th><th>Median top-20</th><th>Median universe</th><th>Median excess</th><th>Excess @ 1% cost</th><th>Beat universe</th><th>vs {bt.index_name}</th></tr></thead>
              <tbody>{(["1y", "2y", "3y"] as const).map((h) => {
                const x = bt.horizons[h];
                return (
                  <tr key={h}><td>{h}</td><td>{x.cohorts}</td><td>{pct(x.median_return)}</td><td>{pct(x.median_universe)}</td>
                    <td className={x.median_excess !== undefined && x.median_excess > 0 ? "q-ok" : "q-bad"}>{x.median_excess === undefined ? "—" : `${x.median_excess > 0 ? "+" : ""}${x.median_excess.toFixed(1)} pp`}</td>
                    <td>{x.median_excess_high_cost === undefined ? "—" : `${x.median_excess_high_cost.toFixed(1)} pp`}</td>
                    <td>{pct(x.beat_universe_pct, 0)}</td><td>{x.median_excess_vs_nifty == null ? "—" : `${x.median_excess_vs_nifty.toFixed(1)} pp`}</td></tr>
                );
              })}</tbody>
            </table></div>
            {bt.quintiles && (
              <div className="mb-quint">
                <span className="muted mb-small">Mean 12-month return by price-score quintile (1 = lowest):</span>
                {bt.quintiles.map((q) => {
                  const max = Math.max(...bt.quintiles!.map((z) => Math.abs(z.mean_12m_return)), 1);
                  return (
                    <div key={q.quintile} className="mb-quint-row">
                      <span>Q{q.quintile}</span>
                      <span className="mb-quint-bar"><span style={{ width: `${Math.abs(q.mean_12m_return) / max * 100}%` }} className={q.mean_12m_return >= 0 ? "" : "neg"} /></span>
                      <b>{q.mean_12m_return.toFixed(1)}%</b>
                    </div>
                  );
                })}
              </div>
            )}
            <p className="muted mb-small">
              IC {bt.ic.mean ?? "—"} (positive in {bt.ic.positive_pct ?? "—"}% of {bt.ic.months} months) · 2× in 3 years: {pct(bt.two_x.cohort_rate_pct)} of picks vs {pct(bt.two_x.universe_rate_pct)} of the universe ·
              2018-H1 cohorts {bt.stress["2018_h1_excess_24m"] ?? "—"} pp, 2020-H1 {bt.stress["2020_h1_excess_24m"] ?? "—"} pp · {bt.data_breaks_dropped} split-like jumps dropped.
            </p>
            <p className="mb-warn">{bt.survivorship_warning}</p>
            <ul className="mb-rules">{bt.notes.map((n) => <li key={n}>{n}</li>)}</ul>
            <details>
              <summary><b>Hindsight check</b></summary>
              <p className="muted mb-small">{bt.hindsight.label}</p>
              <div className="table-shell"><table className="mb-tbl mb-tbl--plain">
                <thead><tr><th>Stock</th><th>First top-20 month</th><th>Months in top 20</th><th>Best 3-year run</th><th>Avg price score 12m before</th><th>Fell &gt; 50% after entry</th></tr></thead>
                <tbody>{bt.hindsight.rows.map((h) => (
                  <tr key={h.symbol}><td>{base(h.symbol)}</td>
                    {!h.in_universe ? <td colSpan={5} className="muted">Not in this universe</td> : <>
                      <td>{h.first_top20_month ?? "never"}</td><td>{h.months_in_top20}</td>
                      <td>{h.best_3y_multiple ? `${h.best_3y_multiple}× from ${h.best_3y_run_start}` : "—"}</td>
                      <td>{h.avg_price_score_12m_before_run ?? "—"}</td>
                      <td>{h.drawdown_over_50pct_after_entry === null || h.drawdown_over_50pct_after_entry === undefined ? "—" : h.drawdown_over_50pct_after_entry ? "Yes" : "No"}</td>
                    </>}
                  </tr>
                ))}</tbody>
              </table></div>
            </details>
          </div>
        )}

        <h3 className="mb-sub-h">Forward ledger</h3>
        {!ledger ? <p className="muted">Ledger unavailable.</p> : (
          <>
            <p>
              <span className={`pill-tag pill-tag--${ledger.decision.verdict === "PASS" ? "ok" : ledger.decision.verdict === "FAIL" ? "bad" : "muted"}`}>{ledger.decision.verdict}</span>{" "}
              {ledger.decision.text}. Each month&apos;s first run is saved and graded after 6, 12, 24 and 36 months. Pass at 24 months: tiers A+B beat the low-score control group by ≥ 10 pp across ≥ 12 snapshots.
            </p>
            {ledger.snapshots.length > 0 && (
              <div className="table-shell"><table className="mb-tbl mb-tbl--plain">
                <thead><tr><th>Snapshot</th><th>Stocks</th>{(["6m", "12m", "24m", "36m"] as const).map((h) => <th key={h}>{h}: A+B / C / D / control</th>)}</tr></thead>
                <tbody>{ledger.snapshots.map((s) => (
                  <tr key={s.month}><td>{s.month}</td><td>{s.stocks}</td>
                    {(["6m", "12m", "24m", "36m"] as const).map((h) => {
                      const x = s.horizons[h];
                      return <td key={h}>{!x ? <span className="muted">Pending</span> : `${pct(x.ab, 0)} / ${pct(x.c, 0)} / ${pct(x.d, 0)} / ${pct(x.x, 0)}${x.ic !== null ? ` · IC ${x.ic}` : ""}`}</td>;
                    })}
                  </tr>
                ))}</tbody>
              </table></div>
            )}
          </>
        )}
      </section>
    </main>
  );
}
