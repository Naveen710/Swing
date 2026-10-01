"use client";

import { useEffect, useState } from "react";

import { getSectorRotation } from "../lib/api";
import { ScanUniverse, SectorRotationResponse } from "../types";

/* Top-down view: which sectors are leading the whole scanned universe right now. */
export function SectorRotationPanel({ universe, refreshKey }: { universe: ScanUniverse; refreshKey: unknown }) {
  const [data, setData] = useState<SectorRotationResponse | null>(null);
  const [expanded, setExpanded] = useState(false);

  const [scanTick, setScanTick] = useState(0);
  useEffect(() => {
    // Any scan (dashboard or trading-system panel) refreshes the ranking.
    const onScan = () => setScanTick((n) => n + 1);
    window.addEventListener("swing-scan-complete", onScan);
    return () => window.removeEventListener("swing-scan-complete", onScan);
  }, []);

  useEffect(() => {
    let alive = true;
    getSectorRotation(universe).then((d) => alive && setData(d)).catch(() => alive && setData(null));
    return () => { alive = false; };
  }, [universe, refreshKey, scanTick]);

  if (!data || data.sectors.length === 0) {
    return (
      <section className="panel sr-panel">
        <h2 className="sr-title">Sector rotation</h2>
        <p className="muted sr-empty">Run a scan to rank every sector in this universe by 1- and 3-month strength versus Nifty.</p>
      </section>
    );
  }

  const sectors = data.sectors;
  const leaders = sectors.filter((s) => s.leading);
  const laggards = sectors.filter((s) => s.lagging);
  const pct = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;

  return (
    <section className="panel sr-panel">
      <div className="sr-head">
        <div>
          <h2 className="sr-title">Sector rotation</h2>
          <p className="muted sr-sub">
            Median excess return vs Nifty across {sectors.reduce((a, s) => a + s.stocks, 0)} stocks. Favour setups in the
            leading sectors; picks in lagging sectors are marked down.
          </p>
        </div>
        <button className="mini-btn" onClick={() => setExpanded((v) => !v)}>{expanded ? "Hide table" : `All ${sectors.length} sectors`}</button>
      </div>
      <div className="sr-groups">
        <div>
          <span className="sr-label q-ok">Leading</span>
          <div className="sr-chips">
            {leaders.map((s) => (
              <span key={s.sector} className="sr-chip sr-chip--lead">
                <b>#{s.rank} {s.sector}</b> {pct(s.excess_return_3m_pct)} 3M · {pct(s.excess_return_1m_pct)} 1M
              </span>
            ))}
          </div>
        </div>
        {laggards.length > 0 && (
          <div>
            <span className="sr-label q-bad">Lagging</span>
            <div className="sr-chips">
              {laggards.map((s) => (
                <span key={s.sector} className="sr-chip sr-chip--lag">
                  <b>#{s.rank} {s.sector}</b> {pct(s.excess_return_3m_pct)} 3M
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
      {expanded && (
        <div className="table-shell" style={{ marginTop: 14 }}>
          <table>
            <thead><tr><th>Rank</th><th>Sector</th><th>Stocks</th><th>1M vs Nifty</th><th>3M vs Nifty</th><th>Status</th></tr></thead>
            <tbody>
              {sectors.map((s) => (
                <tr key={s.sector}>
                  <td>{s.rank}</td>
                  <td>{s.sector}</td>
                  <td>{s.stocks}</td>
                  <td className={s.excess_return_1m_pct >= 0 ? "q-ok" : "q-bad"}>{pct(s.excess_return_1m_pct)}</td>
                  <td className={s.excess_return_3m_pct >= 0 ? "q-ok" : "q-bad"}>{pct(s.excess_return_3m_pct)}</td>
                  <td>{s.leading ? <span className="pill-tag pill-tag--ok">Leading</span> : s.lagging ? <span className="pill-tag pill-tag--bad">Lagging</span> : <span className="pill-tag pill-tag--muted">Neutral</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
