"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getStockDetail } from "../lib/api";
import { StockDetailResponse } from "../types";
import { PriceChart } from "./price-chart";

export function StockDetailShell({ symbol }: { symbol: string }) {
  const [detail, setDetail] = useState<StockDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const response = await getStockDetail(symbol);
        setDetail(response);
      } catch {
        setError("Unable to load this stock. Verify the backend is running.");
      } finally {
        setLoading(false);
      }
    }

    void load();
  }, [symbol]);

  if (loading) {
    return (
      <main className="page-shell">
        <p className="muted">Loading stock detail...</p>
      </main>
    );
  }

  if (error || !detail) {
    return (
      <main className="page-shell">
        <Link href="/" className="text-link">
          Back to dashboard
        </Link>
        <p className="error-text">{error ?? "Stock detail unavailable."}</p>
      </main>
    );
  }

  const signal = detail.latest_signal;

  return (
    <main className="page-shell">
      <section className="detail-header">
        <div>
          <Link href="/" className="text-link">
            Back to dashboard
          </Link>
          <p className="eyebrow">{detail.stock.sector}</p>
          <h1>{detail.stock.company_name}</h1>
          <p className="hero-copy">{detail.stock.symbol}</p>
        </div>

        {signal ? (
          <div className="hero-spotlight">
            <div className="spotlight-label">Latest signal</div>
            <div className="spotlight-symbol">
              {Math.round(signal.probability_score * 100)}%
            </div>
            <div className="spotlight-pattern">{formatPattern(signal.pattern)}</div>
            <div className="spotlight-metric">
              RR {signal.risk_reward_ratio.toFixed(2)} / RS{" "}
              {Math.round(signal.relative_strength.score * 100)} / Expected{" "}
              {formatCurrency(signal.expected_profit_amount)}
            </div>
          </div>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Price map</h2>
            <p>Simple line view for the last 90 sessions with setup levels.</p>
          </div>
        </div>
        <PriceChart candles={detail.candles} signal={signal} />
      </section>

      {signal ? (
        <section className="detail-grid">
          <article className="panel">
            <h2>Trade setup</h2>
            <div className="metric-grid">
              <Metric label="Entry" value={formatCurrency(signal.entry_price)} />
              <Metric label="Stop" value={formatCurrency(signal.stop_loss)} />
              <Metric label="Target" value={formatCurrency(signal.target_price)} />
              <Metric label="Expected return" value={`${signal.expected_return_pct}%`} />
            </div>
          </article>

          <article className="panel">
            <h2>Indicator snapshot</h2>
            <div className="metric-grid">
              <Metric label="EMA20" value={signal.indicators.ema20.toFixed(2)} />
              <Metric label="EMA50" value={signal.indicators.ema50.toFixed(2)} />
              <Metric label="EMA200" value={signal.indicators.ema200.toFixed(2)} />
              <Metric label="RSI14" value={signal.indicators.rsi14.toFixed(2)} />
            </div>
          </article>
        </section>
      ) : null}

      {signal ? (
        <section className="panel">
          <h2>Relative strength vs {signal.relative_strength.benchmark_name}</h2>
          <div className="metric-grid">
            <Metric
              label="RS score"
              value={`${Math.round(signal.relative_strength.score * 100)}`}
            />
            <Metric
              label="20D excess"
              value={formatSignedPct(signal.relative_strength.excess_return_20d_pct)}
            />
            <Metric
              label="50D excess"
              value={formatSignedPct(signal.relative_strength.excess_return_50d_pct)}
            />
            <Metric
              label="120D excess"
              value={formatSignedPct(signal.relative_strength.excess_return_120d_pct)}
            />
          </div>
        </section>
      ) : null}

      {signal ? (
        <section className="panel">
          <h2>Scanner note</h2>
          <p className="hero-copy">{signal.confidence_reason}</p>
        </section>
      ) : (
        <section className="panel">
          <h2>No active signal yet</h2>
          <p className="hero-copy">
            This stock is in the sample universe, but the latest scan did not rank it
            into the active opportunities list.
          </p>
        </section>
      )}
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-card">
      <span className="stat-label">{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function formatPattern(pattern: string) {
  return pattern
    .split("_")
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}

function formatCurrency(value: number) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0
  }).format(value);
}

function formatSignedPct(value: number) {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}
