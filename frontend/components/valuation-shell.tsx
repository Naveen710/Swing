"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { getValuation } from "../lib/api";
import { exportWorkbook } from "../lib/excel";
import { fmtINR } from "../lib/store";
import { ValuationAssumptions, ValuationResponse } from "../types";
import { AppNav } from "./app-nav";
import { StockSearch } from "./stock-search";

const cr = (v: number | null | undefined) => {
  if (v === null || v === undefined) return "—";
  const c = Math.abs(v) / 1e7;
  return `${v < 0 ? "−" : ""}₹${c.toLocaleString("en-IN", { maximumFractionDigits: c >= 100 ? 0 : 1 })} Cr`;
};
const pct = (v: number | null | undefined, dp = 1) => (v === null || v === undefined ? "—" : `${v.toFixed(dp)}%`);

type Draft = { method: "fcf" | "earnings"; base_cr: string; growth: string; terminal: string; discount: string; mos: string };

function toDraft(a: ValuationAssumptions): Draft {
  return {
    method: a.method, base_cr: (a.base_cash_flow / 1e7).toFixed(1), growth: String(a.growth_pct),
    terminal: String(a.terminal_growth_pct), discount: String(a.discount_rate_pct), mos: String(a.margin_of_safety_pct),
  };
}

export function ValuationShell({ symbol }: { symbol: string | null }) {
  const [data, setData] = useState<ValuationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [custom, setCustom] = useState(false);
  const [manualSharesCr, setManualSharesCr] = useState("");
  const [sharesOverride, setSharesOverride] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  async function load(overrides: Record<string, string | number | null> = {}, shares: number | null = sharesOverride) {
    if (!symbol) return;
    if (shares) overrides = { ...overrides, shares };
    setLoading(true);
    setError(null);
    try {
      const d = await getValuation(symbol, overrides);
      setData(d);
      const onlyShares = Object.keys(overrides).every((k) => k === "shares");
      if (d.valuation.available && onlyShares) setDraft(toDraft(d.valuation.assumptions));
    } catch {
      setError(`Couldn't value ${symbol.toUpperCase()}. Check the NSE symbol and try again.`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setData(null); setDraft(null); setCustom(false); setSharesOverride(null); setManualSharesCr("");
    void load({}, null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  function applyShares() {
    const crore = Number(manualSharesCr);
    if (!(crore > 0)) return;
    const shares = crore * 1e7;
    setSharesOverride(shares);
    setCustom(false);
    void load({}, shares);
  }

  function edit(patch: Partial<Draft>) {
    if (!draft) return;
    const next = { ...draft, ...patch };
    if (patch.method && patch.method !== draft.method) next.base_cr = "";   // let the server pick the new base
    setDraft(next);
    setCustom(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      void load({
        method: next.method,
        base_cash_flow: next.base_cr === "" ? null : Number(next.base_cr) * 1e7,
        growth: next.growth, terminal: next.terminal, discount: next.discount, mos: next.mos,
      });
    }, 450);
  }
  function resetDefaults() {
    setCustom(false);
    void load();
  }
  const sharesNote = sharesOverride
    ? "You entered the share count manually."
    : data?.shares_source && !data.shares_source.startsWith("reported") && data.source !== "demo"
      ? `Share count wasn't published directly — estimated from ${data.shares_source}.`
      : null;

  useEffect(() => {
    // After a method switch the server chooses a new base cash flow — show it in the form.
    if (data?.valuation.available && draft && draft.base_cr === "") {
      setDraft({ ...draft, base_cr: (data.valuation.assumptions.base_cash_flow / 1e7).toFixed(1) });
    }
  }, [data, draft]);

  const v = data?.valuation;
  const tone = v?.available ? (v.verdict === "Undervalued" ? "under" : v.verdict === "Overvalued" ? "over" : "fair") : "na";

  function exportExcel() {
    if (!data || !v?.available) return;
    const base = data.symbol.replace(/\.NS$/, "");
    void exportWorkbook(`dcf-${base}`, [
      {
        name: "Summary",
        rows: [
          { Item: "Company", Value: data.company_name }, { Item: "Price (₹)", Value: data.price },
          { Item: "Intrinsic value (₹)", Value: v.intrinsic_value }, { Item: "Verdict", Value: v.verdict },
          { Item: "Upside %", Value: v.upside_pct }, { Item: "Buy below (₹)", Value: v.buy_below },
          { Item: "Method", Value: v.assumptions.method === "fcf" ? "Free cash flow" : "Net profit" },
          { Item: "Base cash flow (₹ Cr)", Value: Math.round(v.assumptions.base_cash_flow / 1e5) / 100 },
          { Item: "Growth yrs 1-5 %", Value: v.assumptions.growth_pct }, { Item: "Terminal growth %", Value: v.assumptions.terminal_growth_pct },
          { Item: "Discount rate %", Value: v.assumptions.discount_rate_pct }, { Item: "Margin of safety %", Value: v.assumptions.margin_of_safety_pct },
          { Item: "Reverse DCF: implied growth %", Value: v.reverse_dcf.implied_growth_pct },
          { Item: "Historical growth %", Value: v.reverse_dcf.historical_growth_pct },
          { Item: "Bear / base / bull (₹)", Value: `${v.scenarios.bear} / ${v.scenarios.base} / ${v.scenarios.bull}` },
          { Item: "Data source", Value: data.source },
        ],
      },
      {
        name: "DCF",
        rows: [
          ...v.dcf.rows.map((r) => ({ Year: r.year, "Growth %": r.growth_pct, "Cash flow (₹ Cr)": r.cash_flow / 1e7, "Discount factor": r.discount_factor, "PV (₹ Cr)": r.present_value / 1e7 })),
          { Year: "Terminal", "Growth %": v.assumptions.terminal_growth_pct, "Cash flow (₹ Cr)": v.dcf.terminal_value / 1e7, "Discount factor": null, "PV (₹ Cr)": v.dcf.pv_terminal / 1e7 },
        ],
      },
      {
        name: "Sensitivity",
        rows: v.sensitivity.discount_rates_pct.map((d, i) => ({
          "Discount rate %": d,
          ...Object.fromEntries(v.sensitivity.terminal_growth_pct.map((t, j) => [`Terminal ${t}%`, v.sensitivity.values[i][j]])),
        })),
      },
      {
        name: "Financials",
        rows: data.history.map((h) => ({
          Year: h.year, "Revenue (₹ Cr)": h.revenue === null ? null : h.revenue / 1e7, "Net profit (₹ Cr)": h.net_income === null ? null : h.net_income / 1e7,
          "Operating CF (₹ Cr)": h.operating_cash_flow === null ? null : h.operating_cash_flow / 1e7,
          "Capex (₹ Cr)": h.capex === null ? null : h.capex / 1e7, "Free CF (₹ Cr)": h.free_cash_flow === null ? null : h.free_cash_flow / 1e7,
        })),
      },
    ]);
  }

  return (
    <main className="page-shell">
      <AppNav />

      <section className="vl-intro">
        <div>
          <p className="eyebrow">DCF valuation</p>
          <h1 className="vl-title">{symbol ? "What is it worth — and what is the price assuming?" : "Value any NSE stock"}</h1>
          <p className="muted">
            A <b>DCF</b> projects the company&apos;s cash flows and discounts them to today to estimate a fair value per share.
            A <b>reverse DCF</b> works backwards from today&apos;s price to the growth the market is already paying for.
          </p>
        </div>
        <div className="vl-search">
          <StockSearch basePath="/valuation" placeholder="Search a stock to value — name or symbol" autoFocus={!symbol} />
        </div>
      </section>

      {!symbol && (
        <section className="panel">
          <p className="muted" style={{ margin: 0 }}>Search for a company above. Assumptions are pre-filled from its own history and you can change every one of them.</p>
        </section>
      )}

      {symbol && loading && !data && (
        <div className="vl-loading"><div className="an-spinner vl-spinner" /><p className="muted">Fetching {symbol.toUpperCase()}&apos;s financial statements…</p></div>
      )}
      {error && <section className="panel"><p className="error-text">{error}</p></section>}

      {data && (
        <>
          <section className="vl-head">
            <div>
              <span className="eyebrow">{[data.sector, data.industry].filter(Boolean).join(" · ")}</span>
              <h2 className="vl-company">{data.company_name}</h2>
              <div className="vl-meta">
                <span className="an-symbol">{data.symbol.replace(/\.NS$/, "")}</span>
                <b>{fmtINR(data.price)}</b>
                {data.market_cap_cr && <span className="muted">Mcap ₹{data.market_cap_cr.toLocaleString("en-IN")} Cr</span>}
                <Link className="mini-btn" href={`/analyze/${data.symbol.replace(/\.NS$/, "")}`}>Full analysis</Link>
                {v?.available && <button className="mini-btn" onClick={exportExcel}>Export to Excel</button>}
              </div>
            </div>
            {data.source === "demo" && <span className="pill-tag pill-tag--warn">Demo data — not real financials</span>}
          </section>

          {!v?.available ? (
            <section className="panel">
              <p className={v?.needs_shares ? "vl-need" : "error-text"} style={{ margin: 0 }}>{v?.reason}</p>
              {v?.needs_shares && (
                <div className="vl-shares-form">
                  <label className="field">Shares outstanding (in crore)
                    <input type="number" min="0" step="0.01" placeholder="e.g. 8.75" value={manualSharesCr}
                      onChange={(e) => setManualSharesCr(e.target.value)} onKeyDown={(e) => e.key === "Enter" && applyShares()} />
                  </label>
                  <button className="primary-button" onClick={applyShares} disabled={!(Number(manualSharesCr) > 0) || loading}>
                    {loading ? "Calculating…" : "Calculate valuation"}
                  </button>
                  <p className="muted vl-small">1 crore = 1,00,00,000 shares. Exchanges list this as &quot;Issued size&quot; or &quot;Total shares&quot;.</p>
                </div>
              )}
            </section>
          ) : (
            <>
              <div className="vl-top">
                <section className={`vl-verdict vl-verdict--${tone}`}>
                  <span className="vl-verdict-label">{v.verdict}</span>
                  <div className="vl-verdict-nums">
                    <div><span>Intrinsic value</span><strong>{fmtINR(v.intrinsic_value)}</strong></div>
                    <div><span>Current price</span><strong>{fmtINR(data.price)}</strong></div>
                    <div><span>{v.upside_pct >= 0 ? "Upside" : "Downside"}</span><strong>{v.upside_pct > 0 ? "+" : ""}{v.upside_pct.toFixed(1)}%</strong></div>
                    <div><span>Buy below ({v.assumptions.margin_of_safety_pct}% margin)</span><strong>{fmtINR(v.buy_below)}</strong></div>
                  </div>
                  <p className="vl-verdict-note">
                    Fairly valued = price within ±{v.fair_band_pct}% of intrinsic value. Scenario range:{" "}
                    <b>{fmtINR(v.scenarios.bear)}</b> (bear) – <b>{fmtINR(v.scenarios.base)}</b> (base) – <b>{fmtINR(v.scenarios.bull)}</b> (bull).
                  </p>
                  <RangeBar bear={v.scenarios.bear} base={v.scenarios.base} bull={v.scenarios.bull} price={data.price} />
                </section>

                <section className="panel vl-reverse">
                  <h3>Reverse DCF</h3>
                  <div className="vl-implied">
                    <span>Growth priced in (years 1–5)</span>
                    <strong>{v.reverse_dcf.implied_growth_pct === null ? "> 150%" : pct(v.reverse_dcf.implied_growth_pct)}</strong>
                  </div>
                  <div className="vl-compare">
                    <div><span>Company&apos;s history</span><b>{pct(v.reverse_dcf.historical_growth_pct)}</b></div>
                    <div><span>Your assumption</span><b>{pct(v.reverse_dcf.assumed_growth_pct)}</b></div>
                  </div>
                  <p className="vl-reverse-text">{v.reverse_dcf.interpretation}</p>
                </section>
              </div>

              {(v.warnings.length > 0 || sharesNote) && (
                <div className="vl-warn">
                  {v.warnings.map((w) => <p key={w}>⚠ {w}</p>)}
                  {sharesNote && <p>ℹ {sharesNote}{sharesOverride && <button className="mini-btn vl-inline" onClick={() => { setSharesOverride(null); void load({}, null); }}>Clear</button>}</p>}
                </div>
              )}

              <section className="panel vl-panel">
                <div className="vl-panel-head">
                  <h3>Assumptions {loading && <span className="muted vl-recalc">recalculating…</span>}</h3>
                  {custom && <button className="mini-btn" onClick={resetDefaults}>Reset to defaults</button>}
                </div>
                {draft && (
                  <div className="vl-form">
                    <label className="field">Cash flow basis
                      <select value={draft.method} onChange={(e) => edit({ method: e.target.value as Draft["method"] })}>
                        <option value="fcf">Free cash flow</option>
                        <option value="earnings">Net profit</option>
                      </select>
                    </label>
                    <label className="field">Base cash flow (₹ Cr)<input type="number" step="10" value={draft.base_cr} onChange={(e) => edit({ base_cr: e.target.value })} /></label>
                    <label className="field">Growth, years 1–5 (%)<input type="number" step="0.5" value={draft.growth} onChange={(e) => edit({ growth: e.target.value })} /></label>
                    <label className="field">Terminal growth (%)<input type="number" step="0.5" value={draft.terminal} onChange={(e) => edit({ terminal: e.target.value })} /></label>
                    <label className="field">Discount rate (%)<input type="number" step="0.5" value={draft.discount} onChange={(e) => edit({ discount: e.target.value })} /></label>
                    <label className="field">Margin of safety (%)<input type="number" step="5" value={draft.mos} onChange={(e) => edit({ mos: e.target.value })} /></label>
                  </div>
                )}
                <details className="vl-notes">
                  <summary>How the defaults were chosen</summary>
                  <ul>
                    {v.default_notes.map((n) => <li key={n}>{n}</li>)}
                    <li>Defaults: growth {pct(v.defaults.growth_pct)}, terminal {pct(v.defaults.terminal_growth_pct)}, discount {pct(v.defaults.discount_rate_pct)}, base {cr(v.defaults.base_cash_flow)}.</li>
                    <li>Years 6–10 fade growth linearly to the terminal rate. Terminal value uses the Gordon growth formula.</li>
                  </ul>
                </details>
              </section>

              <div className="vl-2col">
                <section className="panel vl-panel">
                  <h3>10-year projection</h3>
                  <div className="table-shell">
                    <table className="vl-tbl">
                      <thead><tr><th>Year</th><th>Growth</th><th>Cash flow</th><th>Discount factor</th><th>Present value</th></tr></thead>
                      <tbody>
                        {v.dcf.rows.map((r) => (
                          <tr key={r.year}><td>{r.year}</td><td>{pct(r.growth_pct)}</td><td>{cr(r.cash_flow)}</td><td>{r.discount_factor.toFixed(3)}</td><td>{cr(r.present_value)}</td></tr>
                        ))}
                        <tr className="vl-tv"><td>Terminal</td><td>{pct(v.assumptions.terminal_growth_pct)}</td><td>{cr(v.dcf.terminal_value)}</td><td>—</td><td>{cr(v.dcf.pv_terminal)}</td></tr>
                      </tbody>
                    </table>
                  </div>
                </section>
                <section className="panel vl-panel">
                  <h3>From cash flows to value per share</h3>
                  <table className="vl-bridge">
                    <tbody>
                      <tr><td>PV of 10 years of cash flows</td><td>{cr(v.dcf.pv_cash_flows)}</td></tr>
                      <tr><td>PV of terminal value{v.dcf.terminal_share_pct !== null && <small> ({v.dcf.terminal_share_pct.toFixed(0)}% of total)</small>}</td><td>{cr(v.dcf.pv_terminal)}</td></tr>
                      <tr className="vl-sum"><td>Enterprise value</td><td>{cr(v.dcf.enterprise_value)}</td></tr>
                      <tr><td>+ Cash</td><td>{cr(v.dcf.cash)}</td></tr>
                      <tr><td>− Debt</td><td>{cr(v.dcf.debt)}</td></tr>
                      <tr className="vl-sum"><td>Equity value</td><td>{cr(v.dcf.equity_value)}</td></tr>
                      <tr>
                        <td>÷ Shares outstanding{data.shares_source && <small> ({data.shares_source})</small>}</td>
                        <td>{(v.dcf.shares / 1e7).toLocaleString("en-IN", { maximumFractionDigits: 2 })} Cr</td>
                      </tr>
                      <tr className="vl-sum vl-final"><td>Intrinsic value per share</td><td>{fmtINR(v.intrinsic_value)}</td></tr>
                    </tbody>
                  </table>
                </section>
              </div>

              <div className="vl-2col">
                <section className="panel vl-panel">
                  <h3>Sensitivity — value per share</h3>
                  <p className="muted vl-small">Rows: discount rate. Columns: terminal growth. Green = above today&apos;s price.</p>
                  <div className="table-shell">
                    <table className="vl-sens">
                      <thead><tr><th></th>{v.sensitivity.terminal_growth_pct.map((t) => <th key={t}>{t}%</th>)}</tr></thead>
                      <tbody>
                        {v.sensitivity.discount_rates_pct.map((d, i) => (
                          <tr key={d}>
                            <th>{d}%</th>
                            {v.sensitivity.values[i].map((val, j) => (
                              <td key={j} className={`${val === null ? "" : val >= data.price ? "vl-up" : "vl-down"} ${i === 2 && j === 2 ? "vl-centre" : ""}`}>
                                {val === null ? "—" : fmtINR(val)}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
                <section className="panel vl-panel">
                  <h3>Financial history</h3>
                  <div className="table-shell">
                    <table className="vl-tbl">
                      <thead><tr><th>Year</th><th>Revenue</th><th>Net profit</th><th>Op. cash flow</th><th>Capex</th><th>Free cash flow</th></tr></thead>
                      <tbody>
                        {data.history.map((h) => (
                          <tr key={h.year}><td>FY{String(h.year).slice(-2)}</td><td>{cr(h.revenue)}</td><td>{cr(h.net_income)}</td><td>{cr(h.operating_cash_flow)}</td><td>{cr(h.capex)}</td>
                            <td className={h.free_cash_flow !== null && h.free_cash_flow < 0 ? "q-bad" : ""}>{cr(h.free_cash_flow)}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="muted vl-small">Revenue CAGR {pct(v.historical.revenue_cagr_pct)} · profit CAGR {pct(v.historical.profit_cagr_pct)}</p>
                </section>
              </div>

              <p className="muted vl-small">
                A DCF is only as good as its assumptions — small changes in growth or discount rate move the answer a lot (see the sensitivity table).
                Use it to judge whether expectations are reasonable, not as a precise price target. Not investment advice.
              </p>
            </>
          )}
        </>
      )}

      <style>{`
        .vl-intro { display:grid; grid-template-columns:1fr 380px; gap:24px; align-items:end; margin-bottom:20px; }
        .vl-title { font-family:var(--font-space-grotesk),sans-serif; font-size:clamp(1.5rem,3.2vw,2.3rem); line-height:1.1; margin:8px 0 10px; }
        .vl-search .ss { max-width:none; }
        .vl-loading { display:flex; flex-direction:column; align-items:center; gap:12px; padding:60px 0; }
        .vl-spinner { width:34px; height:34px; border:3px solid var(--line); border-top-color:var(--accent); border-radius:50%; animation:vl-spin .7s linear infinite; }
        @keyframes vl-spin { to { transform:rotate(360deg); } }
        .an-symbol { background:var(--accent-soft); color:var(--accent); font-weight:700; padding:2px 12px; border-radius:999px; }
        .vl-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:16px; }
        .vl-company { font-family:var(--font-space-grotesk),sans-serif; font-size:1.6rem; margin:6px 0 8px; }
        .vl-meta { display:flex; flex-wrap:wrap; gap:8px 14px; align-items:center; }
        .vl-top { display:grid; grid-template-columns:1.4fr 1fr; gap:20px; margin-bottom:20px; }
        .vl-verdict { border-radius:24px; padding:20px 22px; border:1px solid var(--line); box-shadow:var(--shadow); background:var(--panel); }
        .vl-verdict--under { background:linear-gradient(135deg,rgba(22,108,90,0.12),rgba(255,255,255,0.7)); border-color:rgba(22,108,90,0.3); }
        .vl-verdict--fair { background:linear-gradient(135deg,rgba(192,138,18,0.12),rgba(255,255,255,0.7)); border-color:rgba(192,138,18,0.3); }
        .vl-verdict--over { background:linear-gradient(135deg,rgba(185,75,81,0.12),rgba(255,255,255,0.7)); border-color:rgba(185,75,81,0.3); }
        .vl-verdict-label { font-family:var(--font-space-grotesk),sans-serif; font-size:2rem; font-weight:700; }
        .vl-verdict--under .vl-verdict-label { color:var(--green); } .vl-verdict--fair .vl-verdict-label { color:#8a5c00; } .vl-verdict--over .vl-verdict-label { color:var(--red); }
        .vl-verdict-nums { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:14px 0 10px; }
        .vl-verdict-nums span { display:block; font-size:0.74rem; color:var(--muted); margin-bottom:3px; }
        .vl-verdict-nums strong { font-family:var(--font-space-grotesk),sans-serif; font-size:1.2rem; }
        .vl-verdict-note { font-size:0.84rem; color:var(--muted); margin:0 0 12px; }
        .vl-range { position:relative; height:34px; }
        .vl-range-track { position:absolute; top:12px; left:0; right:0; height:8px; border-radius:4px; background:linear-gradient(90deg,rgba(185,75,81,0.35),rgba(192,138,18,0.35),rgba(22,108,90,0.35)); }
        .vl-range-mark { position:absolute; top:4px; width:2px; height:24px; background:var(--text); }
        .vl-range-mark span { position:absolute; top:24px; left:50%; transform:translateX(-50%); font-size:0.7rem; white-space:nowrap; color:var(--muted); }
        .vl-range-price { background:var(--accent); width:3px; }
        .vl-range-price span { color:var(--accent); font-weight:700; top:-14px; }
        .vl-reverse h3, .vl-panel h3 { margin:0 0 10px; font-size:1.08rem; }
        .vl-implied { display:flex; justify-content:space-between; align-items:baseline; padding:12px 14px; border-radius:14px; background:var(--accent-soft); margin-bottom:10px; }
        .vl-implied strong { font-family:var(--font-space-grotesk),sans-serif; font-size:1.7rem; color:var(--accent); }
        .vl-compare { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:10px; }
        .vl-compare div { background:rgba(255,255,255,0.6); border:1px solid var(--line); border-radius:12px; padding:8px 12px; }
        .vl-compare span { display:block; font-size:0.74rem; color:var(--muted); }
        .vl-reverse-text { font-size:0.9rem; line-height:1.55; margin:0; }
        .vl-warn { background:rgba(214,150,0,0.09); border:1px solid rgba(214,150,0,0.3); border-radius:14px; padding:6px 14px; margin-bottom:18px; font-size:0.86rem; }
        .vl-warn p { margin:6px 0; }
        .vl-panel { margin-bottom:20px; }
        .vl-2col .vl-panel { margin-bottom:0; }
        .vl-panel-head { display:flex; justify-content:space-between; align-items:center; }
        .vl-recalc { font-size:0.8rem; font-weight:400; margin-left:8px; }
        .vl-form { display:grid; grid-template-columns:repeat(6,1fr); gap:12px; }
        .vl-notes { margin-top:12px; font-size:0.85rem; }
        .vl-notes summary { cursor:pointer; font-weight:600; }
        .vl-notes ul { color:var(--muted); line-height:1.6; }
        .vl-2col { display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-bottom:20px; }
        .vl-tbl, .vl-bridge, .vl-sens { width:100%; border-collapse:collapse; font-size:0.86rem; }
        .vl-tbl th { font-size:0.72rem; padding:8px; white-space:nowrap; }
        .vl-tbl td { padding:7px 8px; white-space:nowrap; }
        .vl-tv td { font-weight:600; background:rgba(241,104,0,0.05); }
        .vl-bridge td { padding:7px 4px; border-bottom:1px solid var(--line); }
        .vl-bridge td:last-child { text-align:right; font-weight:600; white-space:nowrap; }
        .vl-bridge small { color:var(--muted); }
        .vl-sum td { font-weight:700; }
        .vl-final td { font-size:1.05rem; color:var(--accent); border-bottom:none; }
        .vl-sens th, .vl-sens td { padding:7px 6px; text-align:center; border:1px solid var(--line); white-space:nowrap; }
        .vl-sens thead th, .vl-sens tbody th { background:var(--bg-deep); font-size:0.74rem; }
        .vl-up { background:rgba(22,108,90,0.08); color:var(--green); }
        .vl-down { background:rgba(185,75,81,0.06); color:var(--red); }
        .vl-centre { outline:2px solid var(--accent); outline-offset:-2px; font-weight:700; }
        .vl-small { font-size:0.8rem; margin:8px 0 0; }
        .vl-need { color:var(--text); line-height:1.55; }
        .vl-shares-form { display:flex; flex-wrap:wrap; align-items:flex-end; gap:12px; margin-top:14px; }
        .vl-shares-form .field { min-width:240px; }
        .vl-shares-form .vl-small { flex-basis:100%; margin:0; }
        .vl-inline { margin-left:8px; }
        @media (max-width:960px) {
          .vl-intro, .vl-top, .vl-2col { grid-template-columns:1fr; }
          .vl-verdict-nums { grid-template-columns:1fr 1fr; }
          .vl-form { grid-template-columns:1fr 1fr; }
        }
      `}</style>
    </main>
  );
}

function RangeBar({ bear, base, bull, price }: { bear: number; base: number; bull: number; price: number }) {
  const lo = Math.min(bear, price) * 0.92;
  const hi = Math.max(bull, price) * 1.05;
  const pos = (x: number) => `${((x - lo) / (hi - lo)) * 100}%`;
  return (
    <div className="vl-range" aria-label="Bear, base and bull values versus current price">
      <div className="vl-range-track" />
      {[["Bear", bear], ["Base", base], ["Bull", bull]].map(([label, val]) => (
        <div key={label as string} className="vl-range-mark" style={{ left: pos(val as number) }}><span>{label}</span></div>
      ))}
      <div className="vl-range-mark vl-range-price" style={{ left: pos(price) }}><span>Price</span></div>
    </div>
  );
}
