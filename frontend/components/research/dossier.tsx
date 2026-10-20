"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { CompanyDetail, DossierOutput, fmtCr, fmtDate, fmtMetric, fmtPrice, research } from "../../lib/research";
import { ClaimList, DimensionGrid, EvidenceChips, FreshnessLine, ResearchShell, StateBox, StatusBadge, useIngestPoll, useLoad } from "./ui";

const TABS = [["overview", "Overview"], ["financials", "Financials"], ["valuation", "Valuation"], ["price", "Price"], ["filings", "Filings & news"], ["data", "Data & provenance"]] as const;
type Tab = (typeof TABS)[number][0];

const INK = "#18223b", NEEL = "#3346b8", MARIGOLD = "#e9a23b", GREEN = "#0f8a5f", MUTED = "#8f99ad";

function AddToWatchlist({ symbol }: { symbol: string }) {
  const lists = useLoad(() => research.watchlists(), []);
  const [open, setOpen] = useState(false);
  const [wid, setWid] = useState<number | "new">("new");
  const [name, setName] = useState("Research ideas");
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  async function save() {
    setMsg(null);
    try {
      let id = wid;
      if (id === "new") id = (await research.createWatchlist(name)).id;
      await research.addToWatchlist(id as number, symbol, reason);
      setMsg("Added.");
      setOpen(false);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Could not add");
    }
  }
  if (!open) return <><button className="secondary-button" onClick={() => setOpen(true)}>Add to watchlist</button>{msg && <span className="rs-small muted">{msg}</span>}</>;
  return (
    <div className="rs-pop panel">
      <label className="field">List
        <select value={wid} onChange={(e) => setWid(e.target.value === "new" ? "new" : Number(e.target.value))}>
          <option value="new">New list…</option>
          {(lists.data ?? []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
        </select>
      </label>
      {wid === "new" && <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} /></label>}
      <label className="field">Why are you adding it?<textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. order book accelerating; check Q2 margins" /></label>
      <div className="button-group"><button className="primary-button" onClick={() => void save()} disabled={!reason.trim()}>Add</button><button className="mini-btn" onClick={() => setOpen(false)}>Cancel</button></div>
      {msg && <p className="error-text">{msg}</p>}
    </div>
  );
}

function Overview({ out, snapshotId }: { out: DossierOutput; snapshotId: number }) {
  const [narr, setNarr] = useState<Awaited<ReturnType<typeof research.narrate>> | null>(null);
  const [narrErr, setNarrErr] = useState<string | null>(null);
  const by = (s: string) => out.claims.filter((c) => c.section === s);
  async function narrate() {
    setNarrErr(null);
    try { setNarr(await research.narrate(snapshotId)); } catch (e) { setNarrErr(e instanceof Error ? e.message : "Failed"); }
  }
  return (
    <>
      {out.flags.length > 0 && (
        <section className="panel rs-flags">
          <h2>Flags</h2>
          <ul>{out.flags.map((f, i) => <li key={i} className={`rs-flag rs-flag--${f.level}`}><b>{f.level === "blocker" ? "Blocker" : f.level === "review" ? "Review" : "Watch"}</b> {f.text}</li>)}</ul>
        </section>
      )}
      <section className="panel">
        <div className="rs-panel-head"><h2>Seven dimensions</h2><span className="muted rs-small">Kept separate on purpose — a serious risk is never averaged away. Click one to see its inputs.</span></div>
        <DimensionGrid dims={out.dimensions} snapshotId={snapshotId} />
      </section>
      <div className="rs-grid-2">
        <section className="panel"><h2>Why it may be interesting</h2><ClaimList claims={by("why")} snapshotId={snapshotId} empty="Nothing notable from the data available." /></section>
        <section className="panel"><h2>What could go wrong</h2><ClaimList claims={by("risk")} snapshotId={snapshotId} empty="No risk checks failed on the data available — that is not the same as no risk." /></section>
      </div>
      <div className="rs-grid-2">
        <section className="panel"><h2>What the valuation implies</h2><ClaimList claims={by("valuation")} snapshotId={snapshotId} empty="Valuation not available for this company." /></section>
        <section className="panel"><h2>Catalysts and filings</h2><ClaimList claims={by("catalyst")} snapshotId={snapshotId} empty="No catalyst-type filings retrieved." /></section>
      </div>
      <section className="panel">
        <div className="rs-panel-head"><h2>Written summary</h2><button className="mini-btn" onClick={() => void narrate()}>Draft with AI</button></div>
        <p className="muted rs-small">The AI may only rephrase the claims above and must cite them; any number not in a claim is flagged. Optional — needs RESEARCH_LLM_API_KEY.</p>
        {narrErr && <p className="muted">{narrErr}</p>}
        {narr && narr.paragraphs.map((p, i) => <p key={i} className={p.status === "verified" ? "" : "rs-unverified"}>{p.text}{p.status !== "verified" && <span className="pill-tag pill-tag--warn rs-ml">unverified: {p.reason}</span>}</p>)}
        {narr && <p className="muted rs-small">Model {narr.model}{narr.cached ? " · cached" : ""}</p>}
      </section>
    </>
  );
}

function Financials({ out, snapshotId }: { out: DossierOutput; snapshotId: number }) {
  const rows = out.financial_table.map((r) => ({ ...r, fy: `FY${r.period_end.slice(2, 4)}` }));
  const groups: [string, string[]][] = [
    ["Growth", ["revenue_growth", "revenue_cagr", "revenue_acceleration", "profit_growth", "profit_cagr", "profit_acceleration", "q_revenue_yoy", "q_profit_yoy", "q_acceleration"]],
    ["Profitability & returns", ["ebit_margin", "ebit_margin_change", "net_margin", "roce", "roce_change", "roe"]],
    ["Balance sheet & cash", ["debt_equity", "interest_cover", "cfo_pat", "fcf_pat", "receivable_days", "receivable_days_change", "receivables_growth", "inventory_days", "inventory_days_change", "other_income_share", "share_change"]],
    ["Valuation & price", ["market_cap", "pe", "pb", "ev_ebitda", "earnings_yield", "ret_1m", "ret_3m", "ret_1y", "rel_1y", "below_high", "volatility", "max_drawdown"]],
  ];
  return (
    <>
      <section className="panel">
        <h2>Revenue, profit and margin (₹ crore)</h2>
        {rows.length === 0 ? <p className="muted">No annual statements available.</p> : (
          <div className="rs-chart">
            <ResponsiveContainer width="100%" height={280}>
              <ComposedChart data={rows} margin={{ top: 10, right: 20, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="#e3e8f0" vertical={false} />
                <XAxis dataKey="fy" stroke={MUTED} fontSize={12} />
                <YAxis yAxisId="cr" stroke={MUTED} fontSize={12} width={70} tickFormatter={(v) => Number(v).toLocaleString("en-IN")} />
                <YAxis yAxisId="pct" orientation="right" stroke={MUTED} fontSize={12} width={44} unit="%" />
                <Tooltip formatter={(v, n) => (String(n).includes("margin") ? `${Number(v).toFixed(1)}%` : `₹${fmtCr(Number(v))} cr`)} />
                <Legend />
                <Bar yAxisId="cr" dataKey="revenue_cr" name="Revenue" fill={NEEL} radius={[4, 4, 0, 0]} isAnimationActive={false} />
                <Bar yAxisId="cr" dataKey="net_income_cr" name="Net profit" fill={MARIGOLD} radius={[4, 4, 0, 0]} isAnimationActive={false} />
                <Line yAxisId="pct" dataKey="ebit_margin_pct" name="EBIT margin" stroke={GREEN} strokeWidth={2} dot isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
        <div className="table-shell">
          <table className="rs-tbl rs-tbl--num">
            <thead><tr><th>Year</th><th>Revenue</th><th>EBIT</th><th>Net profit</th><th>Op. cash flow</th><th>Free cash flow</th><th>Debt</th><th>Equity</th><th>EBIT margin</th><th>Source</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.period_end}><td>{r.fy}</td><td>{fmtCr(r.revenue_cr)}</td><td>{fmtCr(r.ebit_cr)}</td><td>{fmtCr(r.net_income_cr)}</td><td>{fmtCr(r.cfo_cr)}</td><td>{fmtCr(r.fcf_cr)}</td>
                <td>{fmtCr(r.debt_cr)}</td><td>{fmtCr(r.equity_cr)}</td><td>{r.ebit_margin_pct === null ? "—" : `${r.ebit_margin_pct.toFixed(1)}%`}</td>
                <td><EvidenceChips refs={r.fact_ids.map((i) => `fact:${i}`)} max={1} /></td></tr>
            ))}</tbody>
          </table>
        </div>
      </section>
      {groups.map(([title, keys]) => (
        <section className="panel" key={title}>
          <h2>{title}</h2>
          <div className="table-shell">
            <table className="rs-tbl">
              <thead><tr><th>Metric</th><th>Value</th><th>Period</th><th>How it is calculated</th><th>Inputs</th></tr></thead>
              <tbody>{keys.filter((k) => out.metrics[k]).map((k) => {
                const m = out.metrics[k];
                return (
                  <tr key={k} className={m.status !== "ok" ? "rs-row-na" : ""}>
                    <td>{m.label}</td>
                    <td className="rs-num">{m.status === "ok" ? fmtMetric(m) : <span className="muted">{m.status === "not_applicable" ? "not applicable" : "unavailable"}</span>}</td>
                    <td>{m.period ?? "—"}</td>
                    <td className="rs-small">{m.status === "ok" ? m.formula : m.reason}{m.status === "ok" && m.reason && <div className="q-bad">{m.reason}</div>}</td>
                    <td><EvidenceChips refs={[`metric:${k}`]} snapshotId={snapshotId} max={1} /></td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        </section>
      ))}
      <section className="panel">
        <h2>Accounting checks</h2>
        <ul className="rs-checks">{out.checks.map((c) => (
          <li key={c.key} className={`rs-check rs-check--${c.status}`}><span className="rs-check-status">{c.status.replace("_", " ")}</span><b>{c.label}</b><span>{c.detail}</span></li>
        ))}</ul>
      </section>
    </>
  );
}

function ValuationTab({ out, symbol }: { out: DossierOutput; symbol: string }) {
  const v = out.valuation;
  if (v.unavailable_reason) return <section className="panel"><h2>Valuation scenarios</h2><p className="muted">Not available: {v.unavailable_reason}.</p></section>;
  const names = ["bear", "base", "bull"] as const;
  const keys = Array.from(new Set(names.flatMap((n) => Object.keys(v.scenarios[n]?.assumptions ?? {}))));
  const label = (k: string) => k.replace(/_/g, " ").replace(" pct", " (%)").replace(" cr", " (₹ cr)");
  return (
    <>
      <section className="panel">
        <div className="rs-panel-head"><h2>Bear / base / bull</h2><Link className="rs-link-sm" href={`/valuation/${symbol}`}>Change assumptions on the Valuation page</Link></div>
        <p className="rs-estimate">{v.label}</p>
        <p className="muted rs-small">Method: {v.method}</p>
        <div className="rs-scen">
          {names.map((n) => {
            const s = v.scenarios[n];
            return (
              <div key={n} className={`rs-scen-card rs-scen-card--${n}`}>
                <span className="rs-scen-name">{n === "bear" ? "Bear" : n === "base" ? "Base" : "Bull"} estimate</span>
                <b>{s?.value_per_share ? fmtPrice(s.value_per_share) : "—"}</b>
                <span className={s?.upside_pct !== undefined && s.upside_pct < 0 ? "q-bad" : "q-ok"}>{s?.upside_pct !== undefined ? `${s.upside_pct >= 0 ? "+" : ""}${s.upside_pct.toFixed(0)}% vs ${fmtPrice(out.price.last)}` : s?.reason}</span>
              </div>
            );
          })}
        </div>
        <div className="table-shell">
          <table className="rs-tbl rs-tbl--num">
            <thead><tr><th>Assumption</th>{names.map((n) => <th key={n}>{n}</th>)}</tr></thead>
            <tbody>{keys.map((k) => <tr key={k}><td>{label(k)}</td>{names.map((n) => <td key={n}>{v.scenarios[n]?.assumptions[k] ?? "—"}</td>)}</tr>)}</tbody>
          </table>
        </div>
      </section>
      {v.reverse_dcf && (
        <section className="panel">
          <h2>What the price assumes (reverse DCF)</h2>
          <div className="rs-rdcf">
            <div><span>Growth priced in (years 1–5)</span><b>{v.reverse_dcf.implied_growth_pct === null ? "> 150%/yr" : `${v.reverse_dcf.implied_growth_pct.toFixed(1)}%`}</b></div>
            <div><span>Growth delivered (lower of revenue and profit CAGR)</span><b>{v.reverse_dcf.delivered_growth_pct === null ? "—" : `${v.reverse_dcf.delivered_growth_pct.toFixed(1)}%`}</b></div>
          </div>
          <p className="muted rs-small">{v.reverse_dcf.note}. Inputs: <EvidenceChips refs={v.inputs.map((i) => `fact:${i}`)} max={6} /></p>
        </section>
      )}
    </>
  );
}

function PriceTab({ symbol }: { symbol: string }) {
  const { data, error, loading, reload } = useLoad(() => research.prices(symbol, 760), [symbol]);
  const rows = (data?.bars ?? []).map(([d, , , , c]) => ({ d, c }));
  return (
    <StateBox loading={loading} error={error} onRetry={reload} empty={!rows.length} emptyText="No price history yet — refresh this company's data.">
      {data && (
        <section className="panel">
          <h2>Price (₹)</h2>
          <p className="muted rs-small">{data.adjustment}. Source: {data.provider}.</p>
          <div className="rs-chart">
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={rows} margin={{ top: 10, right: 20, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="#e3e8f0" vertical={false} />
                <XAxis dataKey="d" stroke={MUTED} fontSize={11} minTickGap={50} tickFormatter={(d) => fmtDate(d)} />
                <YAxis stroke={MUTED} fontSize={12} width={64} domain={["auto", "auto"]} />
                <Tooltip labelFormatter={(d) => fmtDate(String(d))} formatter={(v) => fmtPrice(Number(v))} />
                <Line dataKey="c" name="Close" stroke={NEEL} strokeWidth={2} dot={false} isAnimationActive={false} />
                {data.actions.filter((a) => a.action === "split").map((a) => <ReferenceLine key={a.date} x={a.date} stroke={MARIGOLD} label={{ value: "split", fill: INK, fontSize: 11 }} />)}
              </LineChart>
            </ResponsiveContainer>
          </div>
          {data.actions.length > 0 && <p className="muted rs-small">Corporate actions: {data.actions.map((a) => `${fmtDate(a.date)} ${a.action} ${a.value}`).join(" · ")}</p>}
        </section>
      )}
    </StateBox>
  );
}

function FilingsTab({ symbol }: { symbol: string }) {
  const anns = useLoad(() => research.announcements({ symbol, limit: 40 }), [symbol]);
  const news = useLoad(() => research.news({ symbol, limit: 30 }), [symbol]);
  const [busy, setBusy] = useState<number | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  async function extract(docId: number) {
    setBusy(docId);
    setMsg(null);
    try {
      const r = await research.extract(docId);
      setMsg(`${r.claims.length} claims extracted (${r.claims.filter((c) => c.status === "verified").length} verified). Refresh data to include them in the dossier.`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="rs-grid-2">
      <section className="panel">
        <h2>Exchange filings</h2>
        {msg && <p className="muted rs-small">{msg}</p>}
        <StateBox loading={anns.loading} error={anns.error} empty={!anns.data?.items.length} emptyText="No filings retrieved. NSE may block requests from some cloud hosts — check Data quality.">
          <ul className="rs-feed">{anns.data?.items.map((a) => (
            <li key={a.id}>
              <span className="rs-feed-meta">{fmtDate(a.published_at)} · <span className={`rs-evt rs-evt--${a.event_type}`}>{a.event_type}</span> · {a.category}</span>
              <Link href={`/research/evidence/ann:${a.id}`} className="rs-feed-text">{a.text}</Link>
              {a.document_id && <span className="rs-feed-actions"><Link className="rs-link-sm" href={`/research/evidence/ann:${a.id}`}>Open document</Link>
                <button className="mini-btn" onClick={() => void extract(a.document_id!)} disabled={busy === a.document_id}>{busy === a.document_id ? "Extracting…" : "Extract with AI"}</button></span>}
            </li>
          ))}</ul>
        </StateBox>
      </section>
      <section className="panel">
        <h2>News</h2>
        <p className="muted rs-small">Headlines are leads, not verified facts.</p>
        <StateBox loading={news.loading} error={news.error} empty={!news.data?.items.length} emptyText="No headlines matched to this company.">
          <ul className="rs-feed">{news.data?.items.map((n) => (
            <li key={n.id}>
              <span className="rs-feed-meta">{fmtDate(n.published_at)} · {n.publisher}{n.duplicates?.length ? ` · +${n.duplicates.length} similar` : ""}</span>
              <a href={n.url} target="_blank" rel="noopener noreferrer nofollow" className="rs-feed-text">{n.headline}</a>
            </li>
          ))}</ul>
        </StateBox>
      </section>
    </div>
  );
}

function DataTab({ d }: { d: CompanyDetail }) {
  const [replay, setReplay] = useState<string | null>(null);
  const out = d.snapshot?.output;
  async function doReplay(id: number) {
    const r = await research.replay(id);
    setReplay(r.reproducible === null ? r.reason ?? "" : r.reproducible ? `Snapshot ${id} reproduced exactly (hash ${r.replayed_hash?.slice(0, 12)}…).` : `Snapshot ${id} did NOT reproduce — stored ${r.stored_hash?.slice(0, 12)}…, replayed ${r.replayed_hash?.slice(0, 12)}…`);
  }
  return (
    <>
      <section className="panel">
        <h2>Freshness and sources</h2>
        <FreshnessLine f={d.freshness} />
        {out && <ul className="rs-plain">
          <li>Providers: {out.provenance.providers.join(", ") || "none"} · {out.provenance.facts} facts · {out.provenance.announcements} filings · {out.provenance.documents} documents · {out.provenance.ai_extractions} AI extractions</li>
          <li>Benchmark: {out.provenance.index ? `${out.provenance.index.name}, as of ${fmtDate(out.provenance.index.as_of)}` : "unavailable"}</li>
          {out.provenance.fact_issues.map((x) => <li key={x} className="q-bad">{x}</li>)}
        </ul>}
      </section>
      <section className="panel">
        <h2>Open data issues ({d.issues.length})</h2>
        {d.issues.length === 0 ? <p className="muted">None.</p> : (
          <ul className="rs-issues">{d.issues.map((i) => <li key={i.id} className={`rs-issue rs-issue--${i.severity}`}><b>{i.kind}</b> <span className="rs-mono">{i.field}</span> — {i.detail}</li>)}</ul>
        )}
      </section>
      <section className="panel">
        <h2>Snapshot history</h2>
        <p className="muted rs-small">Each snapshot stores the exact inputs it used, so it can be replayed to check it reproduces.</p>
        {replay && <p className="rs-small">{replay}</p>}
        <div className="table-shell">
          <table className="rs-tbl">
            <thead><tr><th>#</th><th>As of</th><th>Status</th><th>Growth</th><th>Valuation</th><th>Code</th><th></th></tr></thead>
            <tbody>{d.history.map((h) => (
              <tr key={h.id}><td>{h.id}</td><td>{fmtDate(h.as_of)}</td><td><StatusBadge status={h.status} size="sm" /></td>
                <td>{h.dimensions.growth_inflection?.toFixed(0) ?? "—"}</td><td>{h.dimensions.valuation?.toFixed(0) ?? "—"}</td><td className="rs-mono">{h.code_version}</td>
                <td><button className="mini-btn" onClick={() => void doReplay(h.id)}>Replay</button></td></tr>
            ))}</tbody>
          </table>
        </div>
      </section>
      {d.identifier_history.length > 0 && (
        <section className="panel"><h2>Identifier history</h2>
          <ul className="rs-plain">{d.identifier_history.map((x, i) => <li key={i}>{x.field}: {x.old_value} → {x.new_value} ({fmtDate(x.recorded_at)})</li>)}</ul>
        </section>
      )}
    </>
  );
}

export function CompanyDossier({ symbol }: { symbol: string }) {
  const { data, error, loading, reload } = useLoad(() => research.company(symbol), [symbol]);
  const [tab, setTab] = useState<Tab>("overview");
  const done = useCallback(() => reload(), [reload]);
  const poll = useIngestPoll(done);
  const [msg, setMsg] = useState<string | null>(null);
  const out = data?.snapshot?.output;
  async function refresh() {
    setMsg(null);
    try {
      const r = await research.refresh(symbol);
      if (!r.started) setMsg(r.reason ?? "A refresh is already running");
      poll.start();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Refresh failed");
    }
  }
  async function downloadMd() {
    const md = await research.dossierMd(symbol);
    const url = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${symbol}-dossier.md`;
    a.click();
    URL.revokeObjectURL(url);
  }
  const c = data?.company;
  return (
    <ResearchShell
      title={c ? c.name : symbol}
      sample={c?.is_sample}
      intro={c && <span className="rs-idline"><b>{c.symbol}</b> · NSE · ISIN <span className="rs-mono">{c.isin ?? "—"}</span> · {c.sector ?? "sector unknown"}{c.industry && c.industry !== c.sector ? ` · ${c.industry}` : ""}{out?.price.last ? ` · ${fmtPrice(out.price.last)}` : ""}</span>}
      actions={c && <>
        <button className="primary-button" onClick={() => void refresh()} disabled={poll.active}>{poll.active ? "Refreshing…" : "Refresh data"}</button>
        <AddToWatchlist symbol={c.symbol} />
        <Link className="secondary-button" href={`/research/journal?new=${c.symbol}`}>Write a thesis</Link>
        {out && <button className="mini-btn" onClick={() => void downloadMd()}>Download dossier</button>}
      </>}
    >
      {msg && <p className="error-text">{msg}</p>}
      <StateBox loading={loading && !data} error={error} onRetry={reload}>
        {data && (
          <>
            <section className="rs-statusbar">
              <StatusBadge status={out?.status} />
              <span className="muted">{out ? out.status_note : "No research yet — refresh this company to fetch prices, statements and filings."}</span>
              {out && <span className="muted rs-small">As of {fmtDate(out.as_of)} · snapshot #{data.snapshot!.id}</span>}
            </section>
            <FreshnessLine f={data.freshness} />
            {out && (
              <>
                <div className="rs-tabs" role="tablist">
                  {TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={`rs-tab ${tab === k ? "rs-tab--on" : ""}`} onClick={() => setTab(k)}>{l}</button>)}
                </div>
                {tab === "overview" && <Overview out={out} snapshotId={data.snapshot!.id} />}
                {tab === "financials" && <Financials out={out} snapshotId={data.snapshot!.id} />}
                {tab === "valuation" && <ValuationTab out={out} symbol={c!.symbol} />}
                {tab === "price" && <PriceTab symbol={c!.symbol} />}
                {tab === "filings" && <FilingsTab symbol={c!.symbol} />}
                {tab === "data" && <DataTab d={data} />}
              </>
            )}
            {!out && data.issues.length > 0 && <DataTab d={data} />}
          </>
        )}
      </StateBox>
    </ResearchShell>
  );
}
