"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  IngestState, SEGMENT_KEYS, SegmentItem, SegmentKey, SegmentSummary, Status, fmtDate, fmtMetric, fmtPrice, jobProgress, research,
} from "../../lib/research";
import { ResearchShell, Score, StateBox, StatusBadge, useLoad } from "./ui";

const SHORT: Record<SegmentKey, string> = { large: "Large", mid: "Mid", small: "Small" };

function downloadText(text: string, name: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/* ── the three buttons on the research dashboard ─────────────────────── */

export function SegmentButtons() {
  const router = useRouter();
  const { data, error } = useLoad(() => research.segments(), []);
  const [busy, setBusy] = useState<SegmentKey | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  async function open(k: SegmentKey) {
    setBusy(k);
    setMsg(null);
    try {
      await research.fetchSegment(k);          // starts in the background; the page shows progress
      router.push(`/research/segment/${k}`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Could not start the fetch");
      setBusy(null);
    }
  }

  const byKey = Object.fromEntries((data?.segments ?? []).map((s) => [s.key, s])) as Partial<Record<SegmentKey, SegmentSummary>>;
  const running = data?.job.running ? data.job : null;
  return (
    <section className="rs-seg-wrap" aria-label="Market-cap segments">
      <div className="rs-seg-row">
        {SEGMENT_KEYS.map((k) => {
          const s = byKey[k];
          const pct = s && s.count ? Math.round((s.researched / s.count) * 100) : 0;
          const live = running?.segment === k;
          return (
            <button key={k} type="button" className={`rs-seg rs-seg--${k}`} onClick={() => void open(k)} disabled={busy !== null}
              aria-label={`${s?.label ?? SHORT[k] + " cap"}: fetch and view ${s?.count ?? ""} companies`}>
              <span className="rs-seg-top">
                <span className="rs-seg-label">{SHORT[k]} cap</span>
                <span className="rs-seg-count">{s ? s.count : "…"}</span>
              </span>
              <span className="rs-seg-index">{s?.index ?? (k === "large" ? "NIFTY 100" : k === "mid" ? "NIFTY Midcap 150" : "NIFTY Smallcap 250")}</span>
              <span className="rs-seg-rank">{s?.rank ?? ""}</span>
              <span className="rs-seg-bar" aria-hidden><span style={{ width: `${live ? jobProgress(running) ?? 0 : pct}%` }} /></span>
              <span className="rs-seg-foot">
                {live ? `Fetching ${running?.done ?? 0}/${running?.total ?? "…"}` : s ? `${s.researched} of ${s.count} researched` : error ? "unavailable" : "loading…"}
                <span className="rs-seg-cta">{busy === k ? "Starting…" : "Fetch & view →"}</span>
              </span>
            </button>
          );
        })}
      </div>
      <p className="muted rs-small rs-seg-note">
        {data?.note ?? "Large = NIFTY 100, Mid = NIFTY Midcap 150, Small = NIFTY Smallcap 250 — together the NIFTY 500."} Pressing a
        button downloads the current list from NSE Indices and fetches each company from free sources in the background.
      </p>
      {msg && <p className="error-text">{msg}</p>}
    </section>
  );
}

/* ── segment page ────────────────────────────────────────────────────── */

type SortKey = "symbol" | "status" | "price" | "market_cap" | "pe" | "returns" | "revenue_growth" | "profit_growth" | "debt_equity" | "ret_1y" |
  "business_quality" | "growth_inflection" | "valuation" | "last_refresh";
const STATUS_RANK: Record<string, number> = { ELIGIBLE_FOR_RESEARCH: 0, REVIEW_REQUIRED: 1, BLOCKED: 2 };
const DIMS = ["business_quality", "growth_inflection", "valuation"] as const;

function sortValue(i: SegmentItem, k: SortKey): number | string | null {
  if (k === "symbol") return i.symbol;
  if (k === "status") return i.status ? STATUS_RANK[i.status] : null;
  if (k === "price") return i.price;
  if (k === "last_refresh") return i.last_refresh;
  if (k === "returns") return (i.is_financial ? i.metrics.roe : i.metrics.roce)?.value ?? i.metrics.roe?.value ?? null;
  if ((DIMS as readonly string[]).includes(k)) return i.dimensions[k] ?? null;
  return i.metrics[k]?.value ?? null;
}

/* Colour a number only when there is one: negative red, positive green if asked. */
const tone = (v: number | null | undefined, positive: boolean) => (v === null || v === undefined ? "" : v < 0 ? "q-bad" : positive ? "q-ok" : "");

function useJobWatch(seg: SegmentKey, reload: () => void, initial: IngestState | undefined) {
  const [job, setJob] = useState<IngestState | null>(null);
  const [watching, setWatching] = useState(false);
  const tick = useRef(0);
  useEffect(() => {
    if (initial) {
      setJob(initial);
      if (initial.running) setWatching(true);
    }
  }, [initial]);
  useEffect(() => {
    if (!watching) return;
    const t = setInterval(async () => {
      try {
        const s = await research.ingestStatus();
        setJob(s);
        tick.current += 1;
        if (!s.running) { setWatching(false); reload(); }
        else if (s.segment === seg && tick.current % 4 === 0) reload();   // fill the table in as companies finish
      } catch { /* keep polling — the free host may be waking up */ }
    }, 2500);
    return () => clearInterval(t);
  }, [watching, seg, reload]);
  return { job, watch: () => setWatching(true), watching };
}

export function SegmentPage({ seg }: { seg: SegmentKey }) {
  const { data, error, loading, reload } = useLoad(() => research.segment(seg), [seg]);
  const { job, watch } = useJobWatch(seg, reload, data?.job);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<"" | Status | "none">("");
  const [industry, setIndustry] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "status", dir: 1 });
  const [msg, setMsg] = useState<string | null>(null);

  const running = job?.running ? job : null;
  const mine = job?.segment === seg;

  async function start(force = false) {
    setMsg(null);
    try {
      const r = await research.fetchSegment(seg, { force });
      if (!r.started) setMsg(r.reason ?? "Another fetch is running.");
      watch();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Could not start");
    }
  }
  async function stop() {
    try { await research.stopIngest(); watch(); } catch (e) { setMsg(e instanceof Error ? e.message : "Could not stop"); }
  }
  async function template() {
    try { downloadText(await research.segmentTemplate(seg), `real_cases_${seg}_cap.csv`); } catch (e) { setMsg(e instanceof Error ? e.message : "Download failed"); }
  }

  const industries = useMemo(() => [...new Set((data?.items ?? []).map((i) => i.industry).filter(Boolean) as string[])].sort(), [data]);
  const items = useMemo(() => {
    const t = q.trim().toLowerCase();
    const list = (data?.items ?? []).filter((i) =>
      (!t || i.symbol.toLowerCase().includes(t) || i.name.toLowerCase().includes(t)) &&
      (!industry || i.industry === industry) &&
      (!status || (status === "none" ? !i.status : i.status === status)));
    return [...list].sort((a, b) => {
      const x = sortValue(a, sort.key), y = sortValue(b, sort.key);
      if (x === null && y === null) return a.symbol.localeCompare(b.symbol);
      if (x === null) return 1;                      // missing values always last
      if (y === null) return -1;
      const c = typeof x === "string" ? x.localeCompare(String(y)) : (x as number) - (y as number);
      return c * sort.dir || a.symbol.localeCompare(b.symbol);
    });
  }, [data, q, status, industry, sort]);

  const th = (key: SortKey, label: string, title?: string) => (
    <th aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : undefined}>
      <button type="button" className="rs-sort" title={title} onClick={() => setSort((s) => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : (key === "symbol" || key === "status" ? 1 : -1) }))}>
        {label}{sort.key === key ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
      </button>
    </th>
  );

  const pct = jobProgress(running);
  return (
    <ResearchShell
      title={data ? `${data.label} · ${data.index}` : `${SHORT[seg]} cap`}
      intro={data ? <>{data.count} companies — {data.rank}. Each one is fetched from free sources and run through the same research engine; nothing missing is filled in.</> : null}
      actions={<>
        {running && mine
          ? <button className="secondary-button" onClick={() => void stop()} disabled={running.stage === "stopping"}>{running.stage === "stopping" ? "Stopping…" : "Stop fetching"}</button>
          : <button className="primary-button" onClick={() => void start(false)} disabled={Boolean(running)}>Fetch latest data</button>}
        <button className="secondary-button" onClick={() => void template()}>Verification template</button>
      </>}
    >
      <div className="rs-tabs" role="tablist" aria-label="Segment">
        {SEGMENT_KEYS.map((k) => <Link key={k} role="tab" aria-selected={k === seg} href={`/research/segment/${k}`} className={`rs-tab ${k === seg ? "rs-tab--on" : ""}`}>{SHORT[k]} cap</Link>)}
      </div>
      {msg && <p className="error-text">{msg}</p>}

      {running && (
        <section className={`panel rs-progress ${mine ? "" : "rs-progress--other"}`} aria-live="polite">
          {mine ? <>
            <div className="rs-progress-head">
              <b>{running.stage === "preparing" ? "Downloading the constituent list…" : running.stage === "news" ? "Matching news…" : running.stage === "stopping" ? "Stopping after the current company…" : `Fetching ${running.current ?? ""}`}</b>
              <span className="rs-num">{running.done ?? 0} / {running.total || "…"}</span>
            </div>
            <div className="rs-progress-bar"><span style={{ width: `${pct ?? 2}%` }} /></div>
            <p className="muted rs-small">{running.counts ? `${running.counts.ok} complete · ${running.counts.partial} partial · ${running.counts.error} failed · ${running.counts.fresh} already fresh (skipped)` : ""}
              {" "}Runs on the server — you can leave this page and come back. Free sources are slow; a full list can take a while.</p>
          </> : <p className="rs-small">Another fetch is running ({running.label ?? "deep-research set"}, {running.done ?? 0}/{running.total ?? "…"}). This segment can start when it finishes.
            {" "}<button className="mini-btn" onClick={() => void stop()}>Stop it</button></p>}
        </section>
      )}
      {!running && mine && job && (job.stage === "done" || job.stage === "stopped") && job.counts && (
        <p className="rs-small rs-mt">Last fetch {job.stage === "stopped" ? "stopped" : "finished"} {fmtDate(job.finished_at)}: {job.counts.ok} complete, {job.counts.partial} partial,
          {" "}{job.counts.error} failed, {job.counts.fresh} skipped as fresh. {job.counts.partial + job.counts.error > 0 && <Link href="/research/data">See what was missing →</Link>}</p>
      )}

      <StateBox loading={loading && !data} error={error} onRetry={reload}>
        {data && (
          <>
            <section className="rs-stats">
              <div className="rs-stat"><span>Researched</span><b>{data.researched}<small className="rs-of"> / {data.count}</small></b>
                <small>{data.count - data.researched} not fetched yet</small></div>
              <div className="rs-stat"><span>Eligible for research</span><b className="q-ok">{data.totals.ELIGIBLE_FOR_RESEARCH}</b><small>not buy signals</small></div>
              <div className="rs-stat"><span>Review required</span><b>{data.totals.REVIEW_REQUIRED}</b><small>a check needs a human look</small></div>
              <div className="rs-stat"><span>Blocked</span><b className={data.totals.BLOCKED ? "q-bad" : ""}>{data.totals.BLOCKED}</b><small>serious flag or too little data</small></div>
              <div className="rs-stat"><span>Constituent list</span><b className="rs-stat-text">{data.source === "niftyindices" ? "NSE Indices" : "Bundled"}</b>
                <small>{data.source === "niftyindices" ? `downloaded ${fmtDate(data.list_as_of)}` : "live download not done or failed — may be out of date"}</small></div>
            </section>

            <section className="panel rs-filters">
              <label className="field rs-grow">Search<input className="rs-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Symbol or name" /></label>
              <label className="field">Status<select value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
                <option value="">All</option><option value="ELIGIBLE_FOR_RESEARCH">Eligible</option><option value="REVIEW_REQUIRED">Review required</option>
                <option value="BLOCKED">Blocked</option><option value="none">Not fetched yet</option></select></label>
              <label className="field">Industry<select value={industry} onChange={(e) => setIndustry(e.target.value)}>
                <option value="">All industries</option>{industries.map((i) => <option key={i}>{i}</option>)}</select></label>
            </section>

            <section className="panel">
              <div className="rs-panel-head"><h2>{items.length === data.count ? `All ${data.count} companies` : `${items.length} of ${data.count} companies`}</h2>
                <span className="muted rs-small">Click a column to sort · missing values sort last</span></div>
              {items.length === 0 ? <p className="muted">No companies match these filters.</p> : (
                <div className="table-shell">
                  <table className="rs-tbl rs-tbl--num rs-seg-tbl">
                    <thead><tr>
                      {th("symbol", "Company")}{th("status", "Status")}{th("price", "Price")}{th("market_cap", "Mkt cap")}{th("pe", "P/E")}
                      {th("returns", "ROCE / ROE", "ROE for banks and other financials, ROCE for everyone else")}{th("revenue_growth", "Rev. growth")}
                      {th("profit_growth", "Profit growth")}{th("debt_equity", "Debt/equity")}{th("ret_1y", "1y return")}
                      {th("business_quality", "Quality")}{th("growth_inflection", "Growth")}{th("valuation", "Valuation")}{th("last_refresh", "Fetched")}
                    </tr></thead>
                    <tbody>{items.map((i) => {
                      const ret = i.is_financial ? i.metrics.roe : (i.metrics.roce?.value !== null && i.metrics.roce?.value !== undefined ? i.metrics.roce : i.metrics.roe);
                      return (
                        <tr key={i.symbol}>
                          <td><Link className="stock-link" href={`/research/company/${i.symbol}`}>{i.symbol}</Link><div className="table-subtext rs-seg-name">{i.name}{i.industry ? ` · ${i.industry}` : ""}</div></td>
                          <td><StatusBadge status={i.status} size="sm" />{i.flag_counts.blocker > 0 && <div className="table-subtext q-bad">{i.flag_counts.blocker} blocker{i.flag_counts.blocker > 1 ? "s" : ""}</div>}</td>
                          <td>{fmtPrice(i.price)}</td>
                          <td>{fmtMetric(i.metrics.market_cap)}</td>
                          <td>{fmtMetric(i.metrics.pe)}</td>
                          <td>{fmtMetric(ret)}</td>
                          <td className={tone(i.metrics.revenue_growth?.value, false)}>{fmtMetric(i.metrics.revenue_growth)}</td>
                          <td className={tone(i.metrics.profit_growth?.value, false)}>{fmtMetric(i.metrics.profit_growth)}</td>
                          <td>{i.is_financial ? <span className="muted" title="Not meaningful for lenders">n/a</span> : fmtMetric(i.metrics.debt_equity)}</td>
                          <td className={tone(i.metrics.ret_1y?.value, true)}>{fmtMetric(i.metrics.ret_1y)}</td>
                          {DIMS.map((d) => <td key={d}><Score value={i.dimensions[d]} /></td>)}
                          <td className="rs-nowrap rs-small">{i.last_refresh ? fmtDate(i.last_refresh) : <span className="muted">never</span>}</td>
                        </tr>
                      );
                    })}</tbody>
                  </table>
                </div>
              )}
              <p className="muted rs-small rs-mt">{data.note} Scores rank companies for further reading — they are not predictions or recommendations.</p>
            </section>
          </>
        )}
      </StateBox>
    </ResearchShell>
  );
}
