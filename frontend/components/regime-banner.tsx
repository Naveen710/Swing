"use client";

import { useEffect, useState } from "react";

import { getMarketRegime } from "../lib/api";
import { MarketRegimeSnapshot } from "../types";

const LABEL: Record<string, string> = {
  bull: "Risk-on",
  neutral: "Selective",
  bear: "Risk-off",
  unknown: "Unconfirmed",
};

export function useMarketRegime() {
  const [regime, setRegime] = useState<MarketRegimeSnapshot | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);

  async function load(refresh = false) {
    setLoading(true);
    try {
      setRegime(await getMarketRegime(refresh));
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);
  return { regime, error, loading, reload: load };
}

export function RegimeBanner({
  regime, loading, error, onRefresh, compact = false,
}: {
  regime: MarketRegimeSnapshot | null;
  loading: boolean;
  error: boolean;
  onRefresh?: () => void;
  compact?: boolean;
}) {
  if (loading && !regime) {
    return <div className="rg rg--unknown"><span className="rg-title">Reading market regime…</span></div>;
  }
  if (error || !regime) {
    return (
      <div className="rg rg--unknown">
        <span className="rg-title">Market regime unavailable</span>
        <span className="rg-sub">Signals still work, but trade smaller until the regime can be confirmed.</span>
      </div>
    );
  }

  const r = regime;
  return (
    <div className={`rg rg--${r.regime}`}>
      <div className="rg-head">
        <div>
          <span className="rg-pill">{LABEL[r.regime]} · {r.regime}</span>
          <span className="rg-title">{r.notes[0]}</span>
        </div>
        {onRefresh && (
          <button type="button" className="secondary-button rg-refresh" onClick={onRefresh} disabled={loading}>
            {loading ? "Checking…" : "Recheck"}
          </button>
        )}
      </div>
      <div className="rg-metrics">
        <Metric label={`${r.benchmark_name} vs 200 EMA`}
          value={r.benchmark_above_ema200 === null ? "—" : r.benchmark_above_ema200 ? "Above" : "Below"}
          ok={r.benchmark_above_ema200} />
        <Metric label="20D return"
          value={r.benchmark_return_20d_pct === null ? "—" : `${r.benchmark_return_20d_pct > 0 ? "+" : ""}${r.benchmark_return_20d_pct.toFixed(1)}%`}
          ok={r.benchmark_return_20d_pct === null ? null : r.benchmark_return_20d_pct >= 0} />
        <Metric label="Breadth (> 50 EMA)"
          value={r.breadth_above_ema50_pct === null ? "—" : `${r.breadth_above_ema50_pct.toFixed(0)}%`}
          ok={r.breadth_above_ema50_pct === null ? null : r.breadth_above_ema50_pct >= 50} />
        <Metric label="India VIX" value={r.vix === null ? "—" : r.vix.toFixed(1)}
          ok={r.vix === null ? null : r.vix <= 20} />
        {!compact && (
          <Metric label="System thresholds"
            value={`${Math.round(r.recommended_min_probability * 100)}% · ${r.recommended_min_risk_reward}× · ${Math.round(r.position_size_multiplier * 100)}% size`} />
        )}
      </div>
      {!compact && r.notes.length > 1 && (
        <ul className="rg-notes">{r.notes.slice(1).map((n) => <li key={n}>{n}</li>)}</ul>
      )}
    </div>
  );
}

function Metric({ label, value, ok }: { label: string; value: string; ok?: boolean | null }) {
  return (
    <div className="rg-metric">
      <span className="rg-metric-l">{label}</span>
      <strong className={ok === true ? "q-ok" : ok === false ? "q-bad" : ""}>{value}</strong>
    </div>
  );
}
