"use client";

import Link from "next/link";
import { useState } from "react";

import { DIM_SHORT, fmtDate, fmtPrice, research } from "../../lib/research";
import { ResearchShell, StateBox, StatusBadge, useLoad } from "./ui";

export function ResearchWatchlists() {
  const { data, error, loading, reload } = useLoad(() => research.watchlists(), []);
  const [name, setName] = useState("");
  const [adding, setAdding] = useState<Record<number, { symbol: string; reason: string }>>({});
  const [msg, setMsg] = useState<string | null>(null);
  async function act(fn: () => Promise<unknown>) {
    setMsg(null);
    try { await fn(); reload(); } catch (e) { setMsg(e instanceof Error ? e.message : "Failed"); }
  }
  return (
    <ResearchShell title="Watchlists" intro="Lists of companies you are researching, with why you added each one and what has changed since — price, research status and dimension scores.">
      <section className="panel rs-inline-form">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New list name, e.g. Capex beneficiaries" aria-label="New list name" />
        <button className="primary-button" disabled={!name.trim()} onClick={() => void act(async () => { await research.createWatchlist(name.trim()); setName(""); })}>Create list</button>
      </section>
      {msg && <p className="error-text">{msg}</p>}
      <StateBox loading={loading && !data} error={error} onRetry={reload} empty={data?.length === 0} emptyText="No lists yet. Create one, or use “Add to watchlist” on any company page.">
        {data?.map((w) => {
          const form = adding[w.id] ?? { symbol: "", reason: "" };
          return (
            <section key={w.id} className="panel">
              <div className="rs-panel-head">
                <h2>{w.name}</h2>
                <span className="button-group">
                  <button className="mini-btn" onClick={() => { const n = prompt("Rename list", w.name); if (n) void act(() => research.renameWatchlist(w.id, n, w.description)); }}>Rename</button>
                  <button className="mini-btn mini-btn--danger" onClick={() => { if (confirm(`Delete "${w.name}" and its notes?`)) void act(() => research.deleteWatchlist(w.id)); }}>Delete</button>
                </span>
              </div>
              {w.items.length === 0 ? <p className="muted">Empty list.</p> : (
                <div className="table-shell">
                  <table className="rs-tbl">
                    <thead><tr><th>Company</th><th>Why it was added</th><th>Since added</th><th>Status</th><th>Score changes</th><th></th></tr></thead>
                    <tbody>{w.items.map((i) => (
                      <tr key={i.id}>
                        <td><Link className="stock-link" href={`/research/company/${i.symbol}`}>{i.symbol}</Link><div className="table-subtext">{i.name}{i.is_sample && <span className="pill-tag pill-tag--warn rs-ml">sample</span>}</div></td>
                        <td className="rs-reason">{i.reason ?? <span className="muted">—</span>}<div className="table-subtext">added {fmtDate(i.added_at)}</div></td>
                        <td><b className={i.price_change_pct !== null && i.price_change_pct < 0 ? "q-bad" : "q-ok"}>{i.price_change_pct === null ? "—" : `${i.price_change_pct >= 0 ? "+" : ""}${i.price_change_pct.toFixed(1)}%`}</b>
                          <div className="table-subtext">{fmtPrice(i.added_price)} → {fmtPrice(i.price)}</div></td>
                        <td>{i.status_then && i.status_then !== i.status_now ? <><StatusBadge status={i.status_then} size="sm" /> → </> : null}<StatusBadge status={i.status_now} size="sm" /></td>
                        <td>{Object.keys(i.dimension_changes).length === 0 ? <span className="muted">no change</span> :
                          Object.entries(i.dimension_changes).map(([k, v]) => <div key={k} className={v < 0 ? "q-bad" : "q-ok"}>{DIM_SHORT[k]} {v > 0 ? "+" : ""}{v}</div>)}</td>
                        <td><button className="mini-btn" onClick={() => void act(() => research.removeFromWatchlist(w.id, i.id))}>Remove</button></td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              )}
              <div className="rs-inline-form rs-add">
                <input value={form.symbol} onChange={(e) => setAdding({ ...adding, [w.id]: { ...form, symbol: e.target.value } })} placeholder="Symbol" aria-label="Symbol to add" />
                <input className="rs-grow" value={form.reason} onChange={(e) => setAdding({ ...adding, [w.id]: { ...form, reason: e.target.value } })} placeholder="Why are you adding it?" aria-label="Reason" />
                <button className="secondary-button" disabled={!form.symbol.trim() || !form.reason.trim()}
                  onClick={() => void act(async () => { await research.addToWatchlist(w.id, form.symbol.trim(), form.reason.trim()); setAdding({ ...adding, [w.id]: { symbol: "", reason: "" } }); })}>Add</button>
              </div>
            </section>
          );
        })}
      </StateBox>
    </ResearchShell>
  );
}
