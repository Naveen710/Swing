"use client";

import Link from "next/link";
import { useState } from "react";

import { fmtDate, research } from "../../lib/research";
import { ResearchShell, StateBox, useLoad } from "./ui";

const KINDS = ["", "missing", "conflict", "stale", "fetch_error", "parse_error", "mismatch", "unsupported"];

export function DataQualityConsole() {
  const [status, setStatus] = useState("open");
  const [kind, setKind] = useState("");
  const { data, error, loading, reload } = useLoad(() => research.dataQuality(status, kind || undefined), [status, kind]);
  const [msg, setMsg] = useState<string | null>(null);
  async function upload(e: React.ChangeEvent<HTMLInputElement>, fn: (f: File) => Promise<unknown>, label: string) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    setMsg(`${label}…`);
    try {
      const r = await fn(f);
      setMsg(`${label}: ${JSON.stringify((r as { summary?: unknown }).summary ?? r)}`);
      reload();
    } catch (x) {
      setMsg(x instanceof Error ? x.message : "Upload failed");
    }
  }
  const ver = data?.verification ?? [];
  const vsum = ver.reduce<Record<string, number>>((a, v) => ({ ...a, [v.status]: (a[v.status] ?? 0) + 1 }), {});
  return (
    <ResearchShell title="Data quality" intro="Everything the system could not get, could not parse, found contradictory or stale — plus every ingestion run and the manual verification checks. Nothing missing is silently filled in.">
      <section className="panel">
        <h2>Imports and verification</h2>
        <div className="rs-uploads">
          <label className="rs-upload">Import facts (CSV / Excel)<input type="file" accept=".csv,.xlsx" onChange={(e) => void upload(e, research.importFacts, "Facts import")} />
            <span className="muted rs-small">Columns: symbol, metric, period_end, period_type (FY/Q/TTM), value, unit (INR, INR_cr, INR_lakh, INR_mn, shares, ratio), scope, source_note</span></label>
          <label className="rs-upload">Import a PDF (results, annual report, presentation)<input type="file" accept="application/pdf" onChange={(e) => {
            const sym = prompt("Company symbol for this PDF (leave empty for none)") ?? "";
            void upload(e, (f) => research.importDocument(f, sym.trim() || undefined), "PDF import");
          }} /><span className="muted rs-small">Text is extracted page by page so claims can cite the page.</span></label>
          <label className="rs-upload">Run verification checks (CSV)<input type="file" accept=".csv" onChange={(e) => void upload(e, research.verify, "Verification")} />
            <span className="muted rs-small">Expected values you checked against the source. Blank templates per segment: <Link href="/research/segment/large">Large</Link> · <Link href="/research/segment/mid">Mid</Link> · <Link href="/research/segment/small">Small</Link> cap → “Verification template”.</span></label>
        </div>
        {msg && <p className="rs-small">{msg}</p>}
      </section>

      <section className="panel rs-filters">
        <label className="field">Status<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="open">Open</option><option value="resolved">Resolved</option><option value="ignored">Ignored</option><option value="all">All</option></select></label>
        <label className="field">Kind<select value={kind} onChange={(e) => setKind(e.target.value)}>{KINDS.map((k) => <option key={k} value={k}>{k || "All kinds"}</option>)}</select></label>
      </section>
      <StateBox loading={loading && !data} error={error} onRetry={reload}>
        {data && (
          <>
            <section className="rs-stats">
              {data.counts.length === 0 ? <div className="rs-stat"><span>Open issues</span><b className="q-ok">None</b></div> :
                Object.entries(data.counts.reduce<Record<string, number>>((a, c) => ({ ...a, [c.kind]: (a[c.kind] ?? 0) + c.count }), {})).map(([k, n]) => (
                  <div className="rs-stat" key={k}><span>{k.replace("_", " ")}</span><b>{n}</b></div>
                ))}
              <div className="rs-stat"><span>Verification</span><b className={vsum.mismatch ? "q-bad" : "q-ok"}>{vsum.match ?? 0} match</b><small>{vsum.mismatch ?? 0} unexplained mismatch · {vsum.explained ?? 0} explained · {vsum.missing ?? 0} missing · {vsum.not_checked ?? 0} to fill in</small></div>
            </section>

            <section className="panel">
              <h2>Issues ({data.issues.length})</h2>
              {data.issues.length === 0 ? <p className="muted">No {status === "all" ? "" : status} issues{kind ? ` of kind ${kind}` : ""}.</p> : (
                <div className="table-shell">
                  <table className="rs-tbl">
                    <thead><tr><th>Severity</th><th>Company</th><th>Kind</th><th>Field</th><th>Detail</th><th>Seen</th><th></th></tr></thead>
                    <tbody>{data.issues.map((i) => (
                      <tr key={i.id}>
                        <td><span className={`pill-tag pill-tag--${i.severity === "error" ? "bad" : i.severity === "warn" ? "warn" : "muted"}`}>{i.severity}</span></td>
                        <td>{i.symbol ? <Link href={`/research/company/${i.symbol}`}>{i.symbol}</Link> : <span className="muted">global</span>}</td>
                        <td>{i.kind}</td><td className="rs-mono rs-small">{i.field}</td><td className="rs-small">{i.detail}</td>
                        <td className="rs-nowrap rs-small">{fmtDate(i.first_seen)}{i.last_seen !== i.first_seen ? ` → ${fmtDate(i.last_seen)}` : ""}</td>
                        <td>{i.status === "open" ? <span className="button-group">
                          <button className="mini-btn" onClick={() => void research.setIssue(i.id, "resolved").then(reload)}>Resolve</button>
                          <button className="mini-btn" onClick={() => void research.setIssue(i.id, "ignored").then(reload)}>Ignore</button></span>
                          : <button className="mini-btn" onClick={() => void research.setIssue(i.id, "open").then(reload)}>Reopen</button>}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              )}
            </section>

            <section className="panel">
              <h2>Verification checks ({ver.length})</h2>
              {ver.length === 0 ? <p className="muted">No checks run yet.</p> : (
                <div className="table-shell">
                  <table className="rs-tbl rs-tbl--num">
                    <thead><tr><th>Case</th><th>Company</th><th>What</th><th>Expected</th><th>Stored</th><th>Result</th><th>Source / explanation</th></tr></thead>
                    <tbody>{ver.map((v) => (
                      <tr key={v.case_id} className={v.status === "mismatch" ? "rs-row-bad" : ""}>
                        <td>{v.case_id}</td><td>{v.symbol}</td><td>{v.kind} {v.field} {v.period}</td>
                        <td>{v.expected === null ? "—" : v.expected.toLocaleString("en-IN")}</td><td>{v.actual === null ? "—" : v.actual.toLocaleString("en-IN")}</td>
                        <td><span className={`pill-tag pill-tag--${v.status === "match" ? "ok" : v.status === "mismatch" || v.status === "missing" ? "bad" : v.status === "explained" ? "warn" : "muted"}`}>{v.status.replace("_", " ")}</span></td>
                        <td className="rs-small">{v.source_note}{v.explanation ? <div className="muted">{v.explanation}</div> : null}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              )}
            </section>

            <div className="rs-grid-2">
              <section className="panel">
                <h2>Ingestion runs</h2>
                <div className="table-shell">
                  <table className="rs-tbl">
                    <thead><tr><th>Run</th><th>Kind</th><th>Company</th><th>Finished</th><th>Status</th></tr></thead>
                    <tbody>{data.runs.map((r) => (
                      <tr key={r.id}><td>#{r.id}</td><td>{r.kind}</td><td>{r.symbol ?? "—"}</td><td className="rs-nowrap">{r.finished_at ? fmtDate(r.finished_at) : "running"}</td>
                        <td><span className={`pill-tag pill-tag--${r.status === "ok" ? "ok" : r.status === "partial" ? "warn" : r.status === "running" ? "muted" : "bad"}`}>{r.status}</span></td></tr>
                    ))}</tbody>
                  </table>
                </div>
              </section>
              <section className="panel">
                <h2>Sources</h2>
                <ul className="rs-plain">{data.sources.map((s) => <li key={s.provider}><b>{s.provider}</b> — {s.payloads} archived payloads, last {fmtDate(s.last_retrieved)}</li>)}</ul>
                <h3>Recent fetch failures</h3>
                {data.source_errors.length === 0 ? <p className="muted">None.</p> : (
                  <ul className="rs-issues">{data.source_errors.map((e) => <li key={e.id} className="rs-issue rs-issue--error"><span className="rs-mono rs-small">{e.source_key}</span> · {fmtDate(e.retrieved_at)} — {e.error}</li>)}</ul>
                )}
              </section>
            </div>
          </>
        )}
      </StateBox>
    </ResearchShell>
  );
}
