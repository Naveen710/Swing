"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Fragment, useState } from "react";

import { Decision, fmtDate, fmtPrice, research } from "../../lib/research";
import { ResearchShell, StateBox, StatusBadge, useLoad } from "./ui";

const FIELDS: [keyof Decision, string, string][] = [
  ["thesis", "Thesis", "What do you believe that the market may not?"],
  ["drivers", "Expected drivers", "What has to happen — orders, margins, capacity, re-rating?"],
  ["valuation_assumptions", "Valuation assumptions", "What growth / multiple are you assuming? (The dossier shows what the price implies.)"],
  ["downside_risks", "Downside risks", "What could go wrong?"],
  ["invalidation", "Invalidation conditions", "What evidence would prove you wrong? Be specific and checkable."],
];

function DecisionForm({ initial, onSave, onCancel, withSymbol }: { initial: Partial<Decision> & { symbol?: string }; onSave: (d: Partial<Decision> & { symbol?: string }) => Promise<void>; onCancel?: () => void; withSymbol?: boolean }) {
  const [d, setD] = useState(initial);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try { await onSave(d); } catch (x) { setErr(x instanceof Error ? x.message : "Save failed"); } finally { setBusy(false); }
  }
  return (
    <form className="rs-form" onSubmit={submit}>
      <div className="rs-form-row">
        {withSymbol && <label className="field">Symbol<input value={d.symbol ?? ""} onChange={(e) => setD({ ...d, symbol: e.target.value.toUpperCase() })} required /></label>}
        <label className="field rs-grow">Title<input value={d.title ?? ""} onChange={(e) => setD({ ...d, title: e.target.value })} placeholder="One line" /></label>
      </div>
      {FIELDS.map(([k, label, ph]) => (
        <label key={k} className="field">{label}<textarea rows={k === "thesis" ? 4 : 2} value={(d[k] as string) ?? ""} onChange={(e) => setD({ ...d, [k]: e.target.value })} placeholder={ph} /></label>
      ))}
      {err && <p className="error-text">{err}</p>}
      <div className="button-group"><button className="primary-button" disabled={busy}>{busy ? "Saving…" : "Save"}</button>{onCancel && <button type="button" className="mini-btn" onClick={onCancel}>Cancel</button>}</div>
    </form>
  );
}

function DecisionCard({ d, reload }: { d: Decision; reload: () => void }) {
  const [edit, setEdit] = useState(false);
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  async function act(fn: () => Promise<unknown>) {
    setMsg(null);
    try { await fn(); reload(); } catch (e) { setMsg(e instanceof Error ? e.message : "Failed"); }
  }
  return (
    <section className={`panel rs-decision rs-decision--${d.status}`}>
      <div className="rs-panel-head">
        <div>
          <h2>{d.title || `${d.symbol} thesis`}</h2>
          <p className="muted rs-small"><Link href={`/research/company/${d.symbol}`}>{d.symbol}</Link> · {d.name}{d.is_sample ? " (sample)" : ""} · started {fmtDate(d.created_at)} at {fmtPrice(d.price_at_entry)} ·
            now {fmtPrice(d.price_now)} <b className={d.change_since_entry_pct !== null && d.change_since_entry_pct < 0 ? "q-bad" : "q-ok"}>{d.change_since_entry_pct === null ? "" : `(${d.change_since_entry_pct >= 0 ? "+" : ""}${d.change_since_entry_pct.toFixed(1)}%)`}</b></p>
        </div>
        <span className={`pill-tag pill-tag--${d.status === "open" ? "ok" : d.status === "invalidated" ? "bad" : "muted"}`}>{d.status}</span>
      </div>
      {edit ? <DecisionForm initial={d} onCancel={() => setEdit(false)} onSave={async (x) => { await research.updateDecision(d.id, x); setEdit(false); reload(); }} /> : (
        <dl className="rs-dl rs-dl--wide">
          {FIELDS.map(([k, label]) => <Fragment key={k}><dt>{label}</dt><dd className="rs-pre">{(d[k] as string) || <span className="muted">—</span>}</dd></Fragment>)}
          {d.outcome && <><dt>Outcome</dt><dd className="rs-pre">{d.outcome}</dd></>}
        </dl>
      )}
      <h3>Follow-ups</h3>
      {d.follow_ups.length === 0 ? <p className="muted rs-small">None yet. Record what you checked and whether the thesis is on track; the price and research status are captured automatically.</p> : (
        <ul className="rs-followups">{d.follow_ups.map((f, i) => (
          <li key={i}><span className="rs-feed-meta">{fmtDate(f.date)} · {fmtPrice(f.price)}{f.status ? <> · <StatusBadge status={f.status} size="sm" /></> : null}{f.outcome ? ` · ${f.outcome}` : ""}</span><span className="rs-pre">{f.note}</span></li>
        ))}</ul>
      )}
      <div className="rs-inline-form">
        <input className="rs-grow" value={note} onChange={(e) => setNote(e.target.value)} placeholder="What did you check?" aria-label="Follow-up note" />
        <select value={outcome} onChange={(e) => setOutcome(e.target.value)} aria-label="Outcome"><option value="">Outcome…</option><option>on track</option><option>mixed</option><option>off track</option></select>
        <button className="secondary-button" disabled={!note.trim()} onClick={() => void act(async () => { await research.followUp(d.id, note.trim(), outcome || null); setNote(""); setOutcome(""); })}>Add follow-up</button>
      </div>
      <div className="button-group rs-mt">
        <button className="mini-btn" onClick={() => setEdit(true)}>Edit</button>
        {d.status === "open" && <>
          <button className="mini-btn" onClick={() => { const o = prompt("Outcome (what happened?)"); if (o !== null) void act(() => research.updateDecision(d.id, { status: "closed", outcome: o })); }}>Close</button>
          <button className="mini-btn mini-btn--danger" onClick={() => { const o = prompt("Which invalidation condition was met?"); if (o !== null) void act(() => research.updateDecision(d.id, { status: "invalidated", outcome: o })); }}>Mark invalidated</button>
        </>}
        {d.status !== "open" && <button className="mini-btn" onClick={() => void act(() => research.updateDecision(d.id, { status: "open" }))}>Reopen</button>}
        <button className="mini-btn mini-btn--danger" onClick={() => { if (confirm("Delete this journal entry?")) void act(() => research.deleteDecision(d.id)); }}>Delete</button>
      </div>
      {msg && <p className="error-text">{msg}</p>}
    </section>
  );
}

export function DecisionJournal() {
  const params = useSearchParams();
  const preset = params.get("new");
  const [creating, setCreating] = useState(Boolean(preset));
  const [filter, setFilter] = useState("open");
  const { data, error, loading, reload } = useLoad(() => research.decisions(), []);
  const items = (data ?? []).filter((d) => filter === "all" || d.status === filter);
  return (
    <ResearchShell title="Decision journal" intro="Write down the thesis, what has to go right, what you are assuming about valuation, and what would prove you wrong — then record follow-ups. Outcomes, including failures, stay on record."
      actions={!creating && <button className="primary-button" onClick={() => setCreating(true)}>New entry</button>}>
      {creating && (
        <section className="panel">
          <h2>New journal entry</h2>
          <DecisionForm withSymbol initial={{ symbol: preset ?? "" }} onCancel={() => setCreating(false)}
            onSave={async (d) => { await research.createDecision({ ...d, symbol: d.symbol ?? "" }); setCreating(false); reload(); }} />
        </section>
      )}
      <div className="rs-tabs" role="tablist">
        {["open", "closed", "invalidated", "all"].map((f) => <button key={f} role="tab" aria-selected={filter === f} className={`rs-tab ${filter === f ? "rs-tab--on" : ""}`} onClick={() => setFilter(f)}>{f[0].toUpperCase() + f.slice(1)} ({(data ?? []).filter((d) => f === "all" || d.status === f).length})</button>)}
      </div>
      <StateBox loading={loading && !data} error={error} onRetry={reload} empty={items.length === 0} emptyText="No entries here. Open a company dossier and press “Write a thesis”.">
        {items.map((d) => <DecisionCard key={d.id} d={d} reload={reload} />)}
      </StateBox>
    </ResearchShell>
  );
}
