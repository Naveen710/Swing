"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { research } from "../../lib/research";
import { ResearchShell, StateBox, StatusBadge, useLoad } from "./ui";

const PAGE = 50;

export function CompanyExplorer() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [sector, setSector] = useState("");
  const [scope, setScope] = useState("all");
  const [coverage, setCoverage] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);
  useEffect(() => { const t = setTimeout(() => { setQuery(q); setPage(0); }, 300); return () => clearTimeout(t); }, [q]);
  const sectors = useLoad(() => research.sectors(), []);
  const { data, error, loading, reload } = useLoad(() => research.companies({ q: query, sector, scope, coverage, status, limit: PAGE, offset: page * PAGE }),
    [query, sector, scope, coverage, status, page]);

  return (
    <ResearchShell title="Company explorer" intro="Every NSE-listed company in the master list, with which ones have data and research. Search by name, symbol or ISIN.">
      <section className="panel rs-filters">
        <input className="rs-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, symbol or ISIN" aria-label="Search companies" />
        <select value={scope} onChange={(e) => { setScope(e.target.value); setPage(0); }} aria-label="Scope">
          <option value="all">All companies</option>
          <option value="deep">Deep-research set</option>
          <option value="researched">Researched</option>
          <option value="sample">Sample (fictional)</option>
        </select>
        <select value={sector} onChange={(e) => { setSector(e.target.value); setPage(0); }} aria-label="Sector">
          <option value="">All sectors</option>
          {(sectors.data ?? []).map((s) => <option key={s.sector} value={s.sector}>{s.sector} ({s.companies})</option>)}
        </select>
        <select value={coverage} onChange={(e) => { setCoverage(e.target.value); setPage(0); }} aria-label="Data coverage">
          <option value="">Any data coverage</option>
          <option value="with_data">Has financial data</option>
          <option value="no_data">No data yet</option>
        </select>
        <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(0); }} aria-label="Research status">
          <option value="">Any research status</option>
          <option value="ELIGIBLE_FOR_RESEARCH">Eligible for research</option>
          <option value="REVIEW_REQUIRED">Review required</option>
          <option value="BLOCKED">Blocked</option>
        </select>
      </section>
      <p className="muted rs-small">Sector comes from the NSE index files bundled with the app (Nifty 500 and Smallcap 250); others show "—" until Yahoo metadata is fetched. Size and liquidity filters need price data, so they live on the Discovery queue.</p>
      <StateBox loading={loading && !data} error={error} onRetry={reload} empty={data?.total === 0} emptyText="No companies match. Try fewer filters.">
        {data && (
          <section className="panel">
            <div className="rs-panel-head"><h2>{data.total.toLocaleString("en-IN")} companies</h2>
              <span className="rs-pager">
                <button className="mini-btn" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</button>
                <span className="muted rs-small">{page * PAGE + 1}–{Math.min((page + 1) * PAGE, data.total)}</span>
                <button className="mini-btn" disabled={(page + 1) * PAGE >= data.total} onClick={() => setPage((p) => p + 1)}>Next</button>
              </span>
            </div>
            <div className="table-shell">
              <table className="rs-tbl">
                <thead><tr><th>Company</th><th>Sector</th><th>ISIN</th><th>Data</th><th>Research</th></tr></thead>
                <tbody>
                  {data.items.map((c) => (
                    <tr key={c.id}>
                      <td><Link className="stock-link" href={`/research/company/${c.symbol}`}>{c.symbol}</Link>
                        <div className="table-subtext">{c.name}{c.is_sample && <span className="pill-tag pill-tag--warn rs-ml">sample</span>}{c.deep_research && !c.is_sample && <span className="pill-tag pill-tag--muted rs-ml">deep set</span>}</div></td>
                      <td>{c.sector ?? <span className="muted">—</span>}{c.is_financial && <div className="table-subtext">financial — sector rules apply</div>}</td>
                      <td className="rs-mono">{c.isin ?? "—"}</td>
                      <td>{c.facts ? `${c.facts} facts` : <span className="muted">none yet</span>}</td>
                      <td><StatusBadge status={c.research_status} size="sm" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </StateBox>
    </ResearchShell>
  );
}
