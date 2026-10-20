"use client";

import Link from "next/link";
import { useState } from "react";

import { DIM_SHORT, research } from "../../lib/research";
import { ResearchShell, Score, StateBox, StatusBadge, useLoad } from "./ui";

const DIMS = ["growth_inflection", "business_quality", "financial_strength", "valuation", "governance_risk", "data_confidence", "recognition_gap"];
const HELP: Record<string, string> = {
  growth_inflection: "Revenue/profit growth and whether it is accelerating; margin trend",
  business_quality: "ROCE (ROE for lenders), margins",
  financial_strength: "Leverage, interest cover, cash conversion, receivables, other income, dilution",
  valuation: "What the price implies vs what the company has delivered; base scenario; earnings yield",
  governance_risk: "Governance filings, dilution, restatements, volatility, drawdown — higher is lower risk",
  data_confidence: "How much of the needed data exists, open data errors, history length, verification",
  recognition_gap: "Inference: fundamentals improving faster than the price has moved, scaled by valuation",
};

export function DiscoveryQueue() {
  const [sort, setSort] = useState("growth_inflection");
  const [status, setStatus] = useState("");
  const [sector, setSector] = useState("");
  const [sample, setSample] = useState(true);
  const [minConf, setMinConf] = useState(0);
  const [pct, setPct] = useState(false);
  const { data, error, loading, reload } = useLoad(() => research.discovery({ sort, status, sector, include_sample: sample, min_confidence: minConf || undefined }), [sort, status, sector, sample, minConf]);
  const sectors = Array.from(new Set((data?.items ?? []).map((i) => i.sector).filter(Boolean))) as string[];
  return (
    <ResearchShell title="Discovery queue" intro={<>Companies ranked for <b>further research</b>, one dimension at a time. Credible fundamental improvement with possibly limited market recognition, subject to valuation, risk and data confidence. Rules are simple and visible; nothing here claims to predict returns.</>}>
      <section className="panel rs-filters">
        <label className="field">Sort by
          <select value={sort} onChange={(e) => setSort(e.target.value)}>{DIMS.map((d) => <option key={d} value={d}>{DIM_SHORT[d]}</option>)}</select>
        </label>
        <label className="field">Status
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option><option value="ELIGIBLE_FOR_RESEARCH">Eligible</option><option value="REVIEW_REQUIRED">Review required</option><option value="BLOCKED">Blocked</option>
          </select>
        </label>
        <label className="field">Sector
          <select value={sector} onChange={(e) => setSector(e.target.value)}><option value="">All</option>{sectors.sort().map((s) => <option key={s} value={s}>{s}</option>)}</select>
        </label>
        <label className="field">Min data confidence
          <select value={minConf} onChange={(e) => setMinConf(Number(e.target.value))}><option value={0}>Any</option><option value={50}>50+</option><option value={75}>75+</option></select>
        </label>
        <label className="rs-check-inline"><input type="checkbox" checked={sample} onChange={(e) => setSample(e.target.checked)} /> Include sample companies</label>
        <label className="rs-check-inline"><input type="checkbox" checked={pct} onChange={(e) => setPct(e.target.checked)} /> Show sector percentiles</label>
      </section>
      <StateBox loading={loading && !data} error={error} onRetry={reload} empty={data?.items.length === 0}
        emptyText={<>Nothing in the queue yet. Load sample companies or refresh real ones from the <Link href="/research">dashboard</Link>.</>}>
        {data && (
          <section className="panel">
            <p className="muted rs-small">{data.note} Eligible {data.totals.ELIGIBLE_FOR_RESEARCH ?? 0} · Review {data.totals.REVIEW_REQUIRED ?? 0} · Blocked {data.totals.BLOCKED ?? 0}. Blocked and review companies stay listed so failures are visible.</p>
            <div className="table-shell">
              <table className="rs-tbl rs-queue">
                <thead><tr><th>Company</th><th>Status</th>{DIMS.map((d) => <th key={d} title={HELP[d]} className={d === sort ? "rs-th-on" : ""}><button className="rs-th-btn" onClick={() => setSort(d)}>{DIM_SHORT[d]}</button></th>)}<th>Flags</th></tr></thead>
                <tbody>{data.items.map((i) => (
                  <tr key={i.symbol}>
                    <td><Link className="stock-link" href={`/research/company/${i.symbol}`}>{i.symbol}</Link>
                      <div className="table-subtext">{i.name}{i.is_sample && <span className="pill-tag pill-tag--warn rs-ml">sample</span>}</div>
                      <div className="table-subtext">{i.sector ?? "—"}{i.is_financial ? " · lender/insurer" : ""}</div></td>
                    <td><StatusBadge status={i.status} size="sm" /></td>
                    {DIMS.map((d) => (
                      <td key={d}>
                        {pct ? <span title={`vs ${i.percentile_basis[d] === "sector" ? "sector peers" : "all researched companies"}`}>{i.percentiles[d] === null ? "—" : `${i.percentiles[d]}th`}{i.percentile_basis[d] === "all" && i.percentiles[d] !== null ? "*" : ""}</span>
                          : <Score value={i.dimensions[d]} label={DIM_SHORT[d]} />}
                      </td>
                    ))}
                    <td className="rs-flagcell">{i.flags.length === 0 ? <span className="muted">—</span> : i.flags.slice(0, 2).map((f, n) => <div key={n} className={`rs-flag-sm rs-flag-sm--${f.level}`}>{f.text}</div>)}
                      {i.flags.length > 2 && <div className="muted rs-small">+{i.flags.length - 2} more</div>}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            {pct && <p className="muted rs-small">Percentiles are within the sector when it has 3+ researched companies; * means compared with all researched companies instead.</p>}
          </section>
        )}
      </StateBox>
    </ResearchShell>
  );
}
