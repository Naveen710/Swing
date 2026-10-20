"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { fmtDate, research } from "../../lib/research";
import { ResearchShell, StateBox, useLoad } from "./ui";

const EVENTS = ["", "results", "order", "corporate", "capital", "governance", "meeting", "other"];

export function NewsEvents() {
  const params = useSearchParams();
  const [tab, setTab] = useState(params.get("tab") === "filings" ? "filings" : "news");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [matched, setMatched] = useState(false);
  const [dups, setDups] = useState(false);
  const [evt, setEvt] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { const t = setTimeout(() => setQuery(q), 300); return () => clearTimeout(t); }, [q]);
  const news = useLoad(() => research.news({ q: query, matched_only: matched, include_duplicates: dups, limit: 100 }), [query, matched, dups]);
  const anns = useLoad(() => research.announcements({ event_type: evt, limit: 100 }), [evt]);
  async function refresh() {
    setMsg(null);
    try {
      const r = await research.refreshNews();
      setMsg(r.status === "ok" ? `${r.new ?? 0} new headlines (${r.duplicates ?? 0} duplicates of earlier stories).` : `News refresh failed: ${r.error}`);
      news.reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Failed");
    }
  }
  return (
    <ResearchShell title="News & filings" intro="Exchange filings are official disclosures. News headlines are leads to check, not facts — each shows its publisher and timestamps, and repeats of the same story are grouped."
      actions={<button className="secondary-button" onClick={() => void refresh()}>Fetch latest news</button>}>
      <div className="rs-tabs" role="tablist">
        <button role="tab" aria-selected={tab === "news"} className={`rs-tab ${tab === "news" ? "rs-tab--on" : ""}`} onClick={() => setTab("news")}>News</button>
        <button role="tab" aria-selected={tab === "filings"} className={`rs-tab ${tab === "filings" ? "rs-tab--on" : ""}`} onClick={() => setTab("filings")}>Exchange filings</button>
      </div>
      {msg && <p className="muted rs-small">{msg}</p>}
      {tab === "news" ? (
        <>
          <section className="panel rs-filters">
            <input className="rs-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search headlines" aria-label="Search headlines" />
            <label className="rs-check-inline"><input type="checkbox" checked={matched} onChange={(e) => setMatched(e.target.checked)} /> Only matched to a company</label>
            <label className="rs-check-inline"><input type="checkbox" checked={dups} onChange={(e) => setDups(e.target.checked)} /> Show duplicate stories</label>
          </section>
          <StateBox loading={news.loading && !news.data} error={news.error} onRetry={news.reload} empty={news.data?.items.length === 0} emptyText="No headlines yet. Press Fetch latest news.">
            <section className="panel">
              <div className="table-shell">
                <table className="rs-tbl">
                  <thead><tr><th>Published</th><th>Headline</th><th>Publisher</th><th>Company</th><th>Retrieved</th></tr></thead>
                  <tbody>{news.data?.items.map((n) => (
                    <tr key={n.id}>
                      <td className="rs-nowrap">{fmtDate(n.published_at)}</td>
                      <td><a href={n.url} target="_blank" rel="noopener noreferrer nofollow">{n.headline}</a>
                        {n.duplicates?.length ? <div className="table-subtext">Also reported by {n.duplicates.map((d) => d.publisher).join(", ")}</div> : null}</td>
                      <td>{n.publisher ?? "—"}</td>
                      <td>{n.symbol ? <><Link href={`/research/company/${n.symbol}`}>{n.symbol}</Link><div className="table-subtext">matched by {n.match_method}</div></> : <span className="muted">unmatched</span>}</td>
                      <td className="rs-nowrap muted">{fmtDate(n.retrieved_at)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              <p className="muted rs-small">{news.data?.note}</p>
            </section>
          </StateBox>
        </>
      ) : (
        <>
          <section className="panel rs-filters">
            <label className="field">Event type
              <select value={evt} onChange={(e) => setEvt(e.target.value)}>{EVENTS.map((e) => <option key={e} value={e}>{e || "All"}</option>)}</select>
            </label>
          </section>
          <StateBox loading={anns.loading && !anns.data} error={anns.error} onRetry={anns.reload} empty={anns.data?.items.length === 0} emptyText="No filings retrieved yet. Refresh a company to pull its NSE announcements.">
            <section className="panel">
              <div className="table-shell">
                <table className="rs-tbl">
                  <thead><tr><th>Filed</th><th>Company</th><th>Type</th><th>Announcement</th><th>Evidence</th></tr></thead>
                  <tbody>{anns.data?.items.map((a) => (
                    <tr key={a.id}>
                      <td className="rs-nowrap">{fmtDate(a.published_at)}</td>
                      <td><Link href={`/research/company/${a.symbol}`}>{a.symbol}</Link>{a.is_sample && <span className="pill-tag pill-tag--warn rs-ml">sample</span>}</td>
                      <td><span className={`rs-evt rs-evt--${a.event_type}`}>{a.event_type}</span><div className="table-subtext">{a.category}</div></td>
                      <td>{a.text}</td>
                      <td><Link className="rs-link-sm" href={`/research/evidence/ann:${a.id}`}>{a.document_id ? "Filing + document" : "Filing"}</Link></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              <p className="muted rs-small">Event types are assigned by keyword rules from NSE&apos;s own category and text — check the filing itself.</p>
            </section>
          </StateBox>
        </>
      )}
    </ResearchShell>
  );
}
