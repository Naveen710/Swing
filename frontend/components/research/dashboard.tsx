"use client";

import Link from "next/link";
import { useState } from "react";

import { fmtDate, fmtPrice, research } from "../../lib/research";
import { DimShort, ResearchShell, Score, StateBox, StatusBadge, useIngestPoll, useLoad } from "./ui";

export function ResearchDashboard() {
  const { data, error, loading, reload } = useLoad(() => research.dashboard(), []);
  const [msg, setMsg] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);
  const poll = useIngestPoll(reload);
  const errors = data?.data_health.counts.filter((c) => c.severity === "error").reduce((a, c) => a + c.count, 0) ?? 0;
  const warnings = data?.data_health.counts.filter((c) => c.severity === "warn").reduce((a, c) => a + c.count, 0) ?? 0;
  const empty = data ? data.totals.researched === 0 : false;

  async function seed() {
    setSeeding(true);
    setMsg(null);
    try {
      await research.seedSamples();
      reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Seeding failed");
    } finally {
      setSeeding(false);
    }
  }
  async function ingestDeep() {
    setMsg(null);
    try {
      const r = await research.ingest({ deep: true });
      if (!r.started) setMsg(r.reason ?? "A job is already running");
      poll.start();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Could not start");
    }
  }

  return (
    <ResearchShell
      title="Research desk"
      intro="Find companies whose fundamentals may be turning, understand what the price already assumes, and keep the evidence. A research queue — not a list of stocks to buy."
      actions={<>
        <button className="secondary-button" onClick={() => void ingestDeep()} disabled={poll.active}>{poll.active ? `Refreshing ${poll.state?.done ?? 0}/${poll.state?.total ?? "…"}` : "Refresh deep-research set"}</button>
        <Link className="secondary-button" href="/research/companies">Find a company</Link>
      </>}
    >
      {msg && <p className="error-text">{msg}</p>}
      <StateBox loading={loading && !data} error={error} onRetry={reload}>
        {data && empty && (
          <section className="panel rs-onboard">
            <h2>Nothing researched yet</h2>
            <p className="muted">
              Load the <b>20 fictional sample companies</b> to see every page working (no internet needed), or refresh the
              deep-research set of {data.totals.deep} real companies from Yahoo Finance and NSE filings (takes a few minutes).
            </p>
            <div className="button-group">
              <button className="primary-button" onClick={() => void seed()} disabled={seeding}>{seeding ? "Loading samples…" : "Load sample companies"}</button>
              <button className="secondary-button" onClick={() => void ingestDeep()} disabled={poll.active}>Refresh real companies</button>
            </div>
          </section>
        )}
        {data && (
          <>
            <section className="rs-stats">
              <div className="rs-stat"><span>Market (Nifty 500, 1 year)</span><b className={data.market && data.market.ret_1y < 0 ? "q-bad" : "q-ok"}>{data.market ? `${data.market.ret_1y >= 0 ? "+" : ""}${data.market.ret_1y.toFixed(1)}%` : "—"}</b>
                <small>{data.market ? `3 months ${data.market.ret_3m >= 0 ? "+" : ""}${data.market.ret_3m.toFixed(1)}% · as of ${fmtDate(data.market.as_of)}` : "index data unavailable"}{data.demo_mode ? " · demo prices" : ""}</small></div>
              <div className="rs-stat"><span>Researched companies</span><b>{data.totals.researched}</b><small>{data.totals.companies.toLocaleString("en-IN")} in the company master</small></div>
              <div className="rs-stat"><span>Research queue</span><b>{data.queue_totals.ELIGIBLE_FOR_RESEARCH ?? 0}</b><small>eligible · {data.queue_totals.REVIEW_REQUIRED ?? 0} need review · {data.queue_totals.BLOCKED ?? 0} blocked</small></div>
              <div className="rs-stat"><span>Data health</span><b className={errors ? "q-bad" : "q-ok"}>{errors ? `${errors} errors` : "OK"}</b><small>{warnings} warnings · last run {data.data_health.last_run ? fmtDate(data.data_health.last_run.finished_at) : "never"}</small></div>
            </section>

            <div className="rs-grid-2">
              <section className="panel">
                <div className="rs-panel-head"><h2>Candidates for review</h2><Link className="rs-link-sm" href="/research/discovery">Full queue</Link></div>
                <p className="muted rs-small">{data.note}</p>
                {data.candidates.length === 0 ? <p className="muted">No eligible companies yet.</p> : (
                  <ul className="rs-list">
                    {data.candidates.map((c) => (
                      <li key={c.symbol}>
                        <Link href={`/research/company/${c.symbol}`} className="rs-co"><b>{c.symbol}</b><span>{c.name}</span></Link>
                        <span className="rs-mini-dims">
                          <span><DimShort k="growth_inflection" /> <Score value={c.dimensions.growth_inflection} /></span>
                          <span><DimShort k="recognition_gap" /> <Score value={c.dimensions.recognition_gap} /></span>
                        </span>
                        {c.flag_counts.watch > 0 && <span className="pill-tag pill-tag--warn">{c.flag_counts.watch} to watch</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="panel">
                <div className="rs-panel-head"><h2>Watchlist movements</h2><Link className="rs-link-sm" href="/research/watchlists">Watchlists</Link></div>
                {data.watchlist_moves.length === 0 ? <p className="muted">Add companies to a watchlist to see how price and research status move after you added them.</p> : (
                  <ul className="rs-list">
                    {data.watchlist_moves.map((w) => (
                      <li key={w.id}>
                        <Link href={`/research/company/${w.symbol}`} className="rs-co"><b>{w.symbol}</b><span>{w.name}</span></Link>
                        <span className={w.price_change_pct !== null && w.price_change_pct < 0 ? "q-bad" : "q-ok"}>{w.price_change_pct === null ? "—" : `${w.price_change_pct >= 0 ? "+" : ""}${w.price_change_pct.toFixed(1)}%`}</span>
                        <span className="muted rs-small">since {fmtDate(w.added_at)} at {fmtPrice(w.added_price)}</span>
                        {w.status_then && w.status_now && w.status_then !== w.status_now && <StatusBadge status={w.status_now} size="sm" />}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>

            <div className="rs-grid-2">
              <section className="panel">
                <div className="rs-panel-head"><h2>New filings</h2><Link className="rs-link-sm" href="/research/news?tab=filings">All filings</Link></div>
                {data.filings.length === 0 ? <p className="muted">No exchange filings retrieved yet.</p> : (
                  <ul className="rs-feed">
                    {data.filings.map((a) => (
                      <li key={a.id}>
                        <span className="rs-feed-meta">{fmtDate(a.published_at)} · <Link href={`/research/company/${a.symbol}`}>{a.symbol}</Link> · <span className={`rs-evt rs-evt--${a.event_type}`}>{a.event_type}</span></span>
                        <Link href={`/research/evidence/ann:${a.id}`} className="rs-feed-text">{a.text ?? a.category}</Link>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              <section className="panel">
                <div className="rs-panel-head"><h2>Company news</h2><Link className="rs-link-sm" href="/research/news">All news</Link></div>
                <p className="muted rs-small">Headlines are leads, not verified facts.</p>
                {data.news.length === 0 ? <p className="muted">No headlines matched to researched companies yet.</p> : (
                  <ul className="rs-feed">
                    {data.news.map((n) => (
                      <li key={n.id}>
                        <span className="rs-feed-meta">{fmtDate(n.published_at)} · {n.publisher ?? "unknown publisher"} · <Link href={`/research/company/${n.symbol}`}>{n.symbol}</Link></span>
                        <a href={n.url} target="_blank" rel="noopener noreferrer nofollow" className="rs-feed-text">{n.headline}</a>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>
          </>
        )}
      </StateBox>
    </ResearchShell>
  );
}
