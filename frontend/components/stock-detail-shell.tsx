"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getStockDetail } from "../lib/api";
import { StockDetailResponse } from "../types";
import { PriceChart } from "./price-chart";

export function StockDetailShell({ symbol }: { symbol: string }) {
  const [detail, setDetail] = useState<StockDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadDetail(options?: { silent?: boolean }) {
    if (!options?.silent) {
      setRefreshing(true);
    }

    try {
      const response = await getStockDetail(symbol);
      setDetail(response);
      setError(null);
    } catch {
      setError("Unable to load this stock. Verify the backend is running.");
    } finally {
      if (!options?.silent) {
        setRefreshing(false);
      }
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadDetail({ silent: true });
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
          <button
            className="secondary-button"
            onClick={() => void loadDetail()}
            disabled={refreshing}
            type="button"
          >
            {refreshing ? "Refreshing..." : "Refresh detail"}
          </button>
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
              <Metric
                label="Target ETA"
                value={`${signal.estimated_target_sessions} sessions`}
              />
              <Metric
                label="Projected target date"
                value={formatDate(signal.estimated_target_date)}
              />
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
        <section className="detail-grid">
          <article className="panel">
            <h2>Liquidity filter</h2>
            <div className="metric-grid">
              <Metric
                label="20D traded value"
                value={`${signal.liquidity.average_traded_value_20d_cr.toFixed(1)} Cr`}
              />
              <Metric
                label="50D traded value"
                value={`${signal.liquidity.average_traded_value_50d_cr.toFixed(1)} Cr`}
              />
              <Metric
                label="Liquidity score"
                value={`${Math.round(signal.liquidity.score * 100)}`}
              />
              <Metric
                label="Filter status"
                value={signal.liquidity.passes_filter ? "Pass" : "Watch"}
              />
            </div>
          </article>

          <article className="panel">
            <h2>Accumulation quality</h2>
            <div className="metric-grid">
              <Metric
                label="Accumulation score"
                value={`${Math.round(signal.accumulation.score * 100)}`}
              />
              <Metric
                label="Up/down volume"
                value={signal.accumulation.up_volume_ratio_10d.toFixed(2)}
              />
              <Metric
                label="ATR contraction"
                value={signal.accumulation.atr_contraction_ratio.toFixed(2)}
              />
              <Metric
                label="High closes (10D)"
                value={`${signal.accumulation.closes_near_high_10d}`}
              />
              <Metric
                label="Data source"
                value={
                  signal.accumulation.source === "nse_delivery"
                    ? "NSE delivery"
                    : "Volume proxy"
                }
              />
              <Metric
                label="Avg delivery (10D)"
                value={formatOptionalPct(signal.accumulation.average_delivery_pct_10d)}
              />
              <Metric
                label="Latest delivery"
                value={formatOptionalPct(signal.accumulation.latest_delivery_pct)}
              />
              <Metric
                label="Rising delivery days"
                value={`${signal.accumulation.rising_delivery_days_10d}`}
              />
            </div>
          </article>
        </section>
      ) : null}

      {signal ? (
        <section className="detail-grid">
          <article className="panel">
            <h2>Sector leadership</h2>
            <div className="metric-grid">
              <Metric
                label="Sector rank"
                value={`${signal.sector_strength.rank}/${signal.sector_strength.sector_count}`}
              />
              <Metric
                label="Sector score"
                value={`${Math.round(signal.sector_strength.score * 100)}`}
              />
              <Metric
                label="Avg sector 50D excess"
                value={formatSignedPct(signal.sector_strength.average_excess_return_50d_pct)}
              />
              <Metric
                label="Avg sector 120D excess"
                value={formatSignedPct(signal.sector_strength.average_excess_return_120d_pct)}
              />
            </div>
          </article>

          <article className="panel">
            <h2>Event risk</h2>
            <div className="metric-grid">
              <Metric
                label="Risk level"
                value={capitalize(signal.event_risk.risk_level)}
              />
              <Metric
                label="Days to earnings"
                value={
                  signal.event_risk.days_to_earnings !== null
                    ? `${signal.event_risk.days_to_earnings}`
                    : "Unknown"
                }
              />
              <Metric
                label="Earnings date"
                value={
                  signal.event_risk.earnings_date
                    ? formatDate(signal.event_risk.earnings_date)
                    : "Unknown"
                }
              />
              <Metric
                label="Ranking penalty"
                value={signal.event_risk.ranking_penalty.toFixed(2)}
              />
            </div>
          </article>
        </section>
      ) : null}

      {signal ? (
        <section className="panel">
          <h2>Target timing</h2>
          <div className="metric-grid">
            <Metric
              label="Projected sessions"
              value={`${signal.estimated_target_sessions}`}
            />
            <Metric
              label="Projected date"
              value={formatDate(signal.estimated_target_date)}
            />
            <Metric
              label="Historical target hit rate"
              value={`${Math.round(signal.backtest.target_hit_rate * 100)}%`}
            />
            <Metric
              label="Avg sessions to target"
              value={
                signal.backtest.average_target_sessions !== null
                  ? `${signal.backtest.average_target_sessions.toFixed(1)}`
                  : "Insufficient hits"
              }
            />
          </div>
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

function formatOptionalPct(value: number | null) {
  if (value === null) {
    return "Unavailable";
  }
  return `${value.toFixed(1)}%`;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric"
  }).format(new Date(value));
}

function capitalize(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
