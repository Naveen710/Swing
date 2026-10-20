"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { ApiError, fmtDate, fmtMetric, getToken, rawUrl, research } from "../../lib/research";
import { ResearchShell, StateBox, useLoad } from "./ui";

type Obj = Record<string, unknown>;
const s = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));

function OpenOriginal({ rawId, label = "Open original" }: { rawId: number; label?: string }) {
  const [err, setErr] = useState<string | null>(null);
  async function open() {
    setErr(null);
    try {
      const token = getToken();
      const res = await fetch(rawUrl(rawId), { headers: token ? { Authorization: `Bearer ${token}` } : {} });
      if (!res.ok) throw new ApiError(res.status, res.status === 404 ? "The original content is not stored (fetch error or over the size limit)." : `Failed (${res.status})`);
      const blob = await res.blob();
      window.open(URL.createObjectURL(blob), "_blank", "noopener");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed");
    }
  }
  return <><button className="mini-btn" onClick={() => void open()}>{label}</button>{err && <span className="error-text rs-small">{err}</span>}</>;
}

function Source({ src }: { src: Obj | null | undefined }) {
  if (!src) return <p className="muted">No archived source recorded for this item.</p>;
  return (
    <div className="rs-source">
      <dl className="rs-dl">
        <dt>Provider</dt><dd>{s(src.provider)}</dd>
        <dt>Source key</dt><dd className="rs-mono">{s(src.source_key)} (version {s(src.version)})</dd>
        <dt>URL</dt><dd>{src.url ? <a href={String(src.url)} target="_blank" rel="noopener noreferrer nofollow">{String(src.url)}</a> : "—"}</dd>
        <dt>Retrieved</dt><dd>{fmtDate(src.retrieved_at as string)} · last seen {fmtDate(src.last_seen_at as string)}</dd>
        <dt>Published</dt><dd>{src.published_at ? fmtDate(src.published_at as string) : "not stated by the source"}</dd>
        <dt>SHA-256</dt><dd className="rs-mono rs-break">{s(src.sha256)}</dd>
        <dt>Parser</dt><dd>{s(src.parser_version)} · {s(src.status)}{src.error ? ` — ${String(src.error)}` : ""}</dd>
      </dl>
      {src.status === "ok" && <OpenOriginal rawId={Number(src.id)} />}
    </div>
  );
}

function DocumentView({ docId, highlight }: { docId: number; highlight?: number }) {
  const { data, error, loading } = useLoad(() => research.document(docId), [docId]);
  return (
    <StateBox loading={loading} error={error}>
      {data && (
        <section className="panel">
          <div className="rs-panel-head"><h2>{String(data.document.title)}</h2>{data.document.raw_source_id && <OpenOriginal rawId={Number(data.document.raw_source_id)} label="Open PDF" />}</div>
          <p className="muted rs-small">{data.company ? <Link href={`/research/company/${data.company.symbol}`}>{data.company.symbol}</Link> : "No company"} · {s(data.document.doc_type)} · {s(data.document.pages)} page(s) · text {s(data.document.text_status)} · parser {s(data.document.parser_version)}</p>
          {data.document.text_status !== "ok" && <p className="q-bad">No extractable text — this is probably a scanned PDF. Open the original instead.</p>}
          <div className="rs-pages">
            {data.chunks.map((c) => (
              <div key={c.id} id={`chunk-${c.id}`} className={`rs-chunk ${c.id === highlight ? "rs-chunk--on" : ""}`}>
                <span className="rs-chunk-ref">Page {c.page} · chunk:{c.id}</span>
                <pre>{c.text}</pre>
              </div>
            ))}
          </div>
          {data.extractions.map((x, i) => (
            <div key={i} className="rs-extract">
              <h3>AI extraction · {x.model} · {fmtDate(x.created_at)}</h3>
              <ul className="rs-claims">{x.claims.map((c, j) => (
                <li key={j} className={`rs-claim rs-claim--${c.status === "verified" ? "sourced" : "unverified"}`}>
                  <span className={`rs-claim-type rs-claim-type--${c.status === "verified" ? "sourced" : "unverified"}`}>{c.status}</span>
                  <span className="rs-claim-text">{c.text}<span className="table-subtext">“{c.quote}”{c.reason ? ` — ${c.reason}` : ""}</span></span>
                  {c.chunk_id && <a className="rs-ev" href={`#chunk-${c.chunk_id}`}>chunk:{c.chunk_id}</a>}
                </li>
              ))}</ul>
              {x.language_changes && <p className="muted rs-small">Tone: {x.language_changes}</p>}
            </div>
          ))}
        </section>
      )}
    </StateBox>
  );
}

export function EvidenceViewer({ refId }: { refId: string }) {
  const params = useSearchParams();
  const snap = params.get("snapshot") ? Number(params.get("snapshot")) : undefined;
  const { data, error, loading, reload } = useLoad(() => research.evidence(refId, snap), [refId, snap]);
  const d = data as Obj | null;
  const kind = d?.kind as string | undefined;
  return (
    <ResearchShell title="Evidence" intro={<>Where this came from: <span className="rs-mono">{refId}</span>. Sourced items link to the archived original with its hash; computed items list their formula and the facts they used.</>}>
      <StateBox loading={loading} error={error} onRetry={reload}>
        {d && kind === "fact" && (() => {
          const f = d.fact as Obj;
          const versions = (d.versions as Obj[]) ?? [];
          return (
            <>
              <section className="panel">
                <h2>{s(f.metric)} · {s(f.period_type)} {fmtDate(f.period_end as string)}</h2>
                <dl className="rs-dl">
                  <dt>Value</dt><dd><b>{Number(f.value).toLocaleString("en-IN")}</b> {s(f.unit)}</dd>
                  <dt>Scope</dt><dd>{s(f.scope)}</dd>
                  <dt>Provider</dt><dd>{s(f.provider)}</dd>
                  <dt>Verification</dt><dd className={f.verification === "verified" ? "q-ok" : ""}>{s(f.verification)}</dd>
                  <dt>Version</dt><dd>{s(f.version)} {f.is_current ? "(current)" : "(superseded)"}</dd>
                  <dt>Note</dt><dd>{s(f.note)}</dd>
                </dl>
              </section>
              {versions.length > 1 && (
                <section className="panel"><h2>All versions</h2>
                  <table className="rs-tbl rs-tbl--num"><thead><tr><th>Version</th><th>Provider</th><th>Value</th><th>Unit</th><th>Recorded</th><th>Current</th></tr></thead>
                    <tbody>{versions.map((v) => <tr key={String(v.id)}><td>{s(v.version)}</td><td>{s(v.provider)}</td><td>{Number(v.value).toLocaleString("en-IN")}</td><td>{s(v.unit)}</td><td>{fmtDate(v.recorded_at as string)}</td><td>{v.is_current ? "yes" : "no"}</td></tr>)}</tbody>
                  </table>
                </section>
              )}
              <section className="panel"><h2>Source</h2><Source src={d.source as Obj} /></section>
            </>
          );
        })()}
        {d && kind === "announcement" && (() => {
          const a = d.announcement as Obj;
          return (
            <>
              <section className="panel">
                <h2>{s(a.category)}</h2>
                <p>{s(a.text)}</p>
                <dl className="rs-dl">
                  <dt>Exchange</dt><dd>{s(a.exchange)} · id {s(a.external_id)}</dd>
                  <dt>Filed</dt><dd>{fmtDate(a.published_at as string)}</dd>
                  <dt>Our event type</dt><dd>{s(a.event_type)} (keyword rule)</dd>
                  <dt>Attachment</dt><dd>{a.attachment_url && String(a.attachment_url).startsWith("http") ? <a href={String(a.attachment_url)} target="_blank" rel="noopener noreferrer nofollow">{String(a.attachment_url)}</a> : s(a.attachment_url)}</dd>
                </dl>
              </section>
              <section className="panel"><h2>Source</h2><Source src={d.source as Obj} /></section>
              {d.document ? <DocumentView docId={Number((d.document as Obj).id)} /> : null}
            </>
          );
        })()}
        {d && kind === "chunk" && <><DocumentView docId={Number((d.document as Obj).id)} highlight={Number((d.chunk as Obj).id)} /><section className="panel"><h2>Source</h2><Source src={d.source as Obj} /></section></>}
        {d && kind === "news" && (() => {
          const n = d.news as Obj;
          return (
            <section className="panel">
              <h2>{s(n.headline)}</h2>
              <p className="muted">Headline only — a lead to check, not a verified fact.</p>
              <dl className="rs-dl"><dt>Publisher</dt><dd>{s(n.publisher)}</dd><dt>Published</dt><dd>{fmtDate(n.published_at as string)}</dd><dt>Retrieved</dt><dd>{fmtDate(n.retrieved_at as string)}</dd>
                <dt>Link</dt><dd><a href={String(n.url)} target="_blank" rel="noopener noreferrer nofollow">{String(n.url)}</a></dd><dt>Matched by</dt><dd>{s(n.match_method)}</dd></dl>
              <Source src={d.source as Obj} />
            </section>
          );
        })()}
        {d && kind === "raw" && <section className="panel"><h2>Archived source</h2><Source src={d.source as Obj} /></section>}
        {d && kind === "metric" && (() => {
          const m = d.metric as Obj | null;
          if (!m) return <section className="panel"><p className="muted">Metric not found in this snapshot.</p></section>;
          const inputs = (d.inputs as Obj[]) ?? [];
          return (
            <section className="panel">
              <h2>{s(m.label)} — computed</h2>
              <dl className="rs-dl">
                <dt>Value</dt><dd><b>{fmtMetric({ value: m.value as number | null, unit: String(m.unit) })}</b> {m.period ? `(${String(m.period)})` : ""}</dd>
                <dt>Status</dt><dd>{s(m.status)}{m.reason ? ` — ${String(m.reason)}` : ""}</dd>
                <dt>Formula</dt><dd className="rs-mono">{s(m.formula)} · version {s(m.version)}</dd>
              </dl>
              <h3>Input facts</h3>
              {inputs.length === 0 ? <p className="muted">No stored facts (price-based or not computed).</p> : (
                <table className="rs-tbl rs-tbl--num"><thead><tr><th>Fact</th><th>Metric</th><th>Period</th><th>Value</th><th>Unit</th><th>Provider</th><th>Version</th></tr></thead>
                  <tbody>{inputs.map((x) => { const f = x.fact as Obj; return (
                    <tr key={String(f.id)}><td><Link href={`/research/evidence/fact:${f.id}`}>fact:{String(f.id)}</Link></td><td>{s(f.metric)}</td><td>{s(f.period_type)} {s(f.period_end)}</td>
                      <td>{Number(f.value).toLocaleString("en-IN")}</td><td>{s(f.unit)}</td><td>{s(f.provider)}</td><td>{s(f.version)}</td></tr>); })}</tbody>
                </table>
              )}
            </section>
          );
        })()}
        {d && (kind === "check" || kind === "dimension" || kind === "valuation") && (
          <section className="panel">
            <h2>{kind === "check" ? "Accounting check" : kind === "dimension" ? "Dimension" : "Valuation"} — computed</h2>
            {kind === "dimension" && d.dimension ? (() => { const dim = d.dimension as Obj; return (
              <>
                <p><b>{s(dim.label)}</b>: {s(dim.score)} / 100 · {s(dim.coverage_pct)}% of inputs had data</p>
                <table className="rs-tbl"><thead><tr><th>Input</th><th>Weight</th><th>Score</th><th>Why</th></tr></thead>
                  <tbody>{((dim.parts as Obj[]) ?? []).map((p) => <tr key={String(p.metric)}><td>{s(p.metric)}</td><td>{s(p.weight)}</td><td>{p.score === null ? "no data" : String(p.score)}</td><td>{s(p.reason)}</td></tr>)}</tbody></table>
                <p className="muted rs-small">Each input is scored on a straight line between two thresholds (0–100) and the dimension is the weighted average of inputs that have data. Weights are fixed rules, not fitted to returns.</p>
              </>); })() : <pre className="rs-json">{JSON.stringify(d.check ?? d.valuation ?? d.dimension, null, 2)}</pre>}
          </section>
        )}
      </StateBox>
      <p className="muted rs-small"><button className="mini-btn" onClick={() => history.back()}>Back</button></p>
    </ResearchShell>
  );
}
