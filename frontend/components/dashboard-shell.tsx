"use client";

import Link from "next/link";
import { startTransition, useEffect, useMemo, useState } from "react";

import { getLatestSignals, getScanStatus, getStocks, runScan } from "../lib/api";
import { StockSummary, TradeSetup } from "../types";
import { OpportunitiesTable } from "./opportunities-table";

const DEFAULT_INVESTMENT = 100000;

export function DashboardShell() {
  const [signals, setSignals] = useState<TradeSetup[]>([]);
  const [stocks, setStocks] = useState<StockSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [scanInProgress, setScanInProgress] = useState(false);
  const [scanNotice, setScanNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [minProbability, setMinProbability] = useState(0.6);
  const [minRiskReward, setMinRiskReward] = useState(2.0);
  const [maxResults, setMaxResults] = useState(12);
  const [investmentAmount, setInvestmentAmount] = useState(DEFAULT_INVESTMENT);
  const [selectedSector, setSelectedSector] = useState("All sectors");

  useEffect(() => {
    async function bootstrap() {
      try {
        const [stockUniverse, latestSignals, status] = await Promise.all([
          getStocks(),
          getLatestSignals(),
          getScanStatus()
        ]);
        setStocks(stockUniverse);
        setSignals(latestSignals);
        setScanInProgress(status.scan_in_progress);
        if (status.scan_in_progress) {
          setScanNotice(
            latestSignals.length
              ? "Showing the latest saved opportunities while a fresh NSE-wide scan runs in the background."
              : "Full NSE scan is already running in the background. Results will refresh automatically when it finishes."
          );
        }
      } catch (requestError) {
        setError("Unable to reach the backend API. Start the FastAPI server and retry.");
      } finally {
        setLoading(false);
      }
    }

    void bootstrap();
  }, []);

  useEffect(() => {
    if (!scanInProgress) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void getScanStatus()
        .then((status) => {
          if (!status.scan_in_progress) {
            setScanInProgress(false);
            setScanNotice("Fresh scan complete. Results have been updated.");
            void getLatestSignals().then((latestSignals) => {
              setSignals(latestSignals);
            });
          }
        })
        .catch(() => {
          // Keep the current board visible and try again on the next poll.
        });
    }, 15000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [scanInProgress]);

  const sectors = useMemo(() => {
    const unique = new Set(stocks.map((stock) => stock.sector));
    return ["All sectors", ...Array.from(unique).sort()];
  }, [stocks]);

  const topSetup = signals[0] ?? null;
  const averageProbability = signals.length
    ? Math.round(
        (signals.reduce((sum, signal) => sum + signal.probability_score, 0) /
          signals.length) *
          100
      )
    : 0;

  async function handleScan() {
    setScanning(true);
    setError(null);

    startTransition(() => {
      void runScan({
        max_results: maxResults,
        min_probability: minProbability,
        min_risk_reward: minRiskReward,
        investment_amount: investmentAmount,
        sectors: selectedSector === "All sectors" ? undefined : [selectedSector]
      })
        .then((response) => {
          setSignals(response.results);
          setScanInProgress(response.scan_in_progress);
          if (response.scan_in_progress) {
            setScanNotice(
              response.refresh_started && response.results.length
                ? "Showing the latest saved opportunities while a fresh NSE-wide scan runs in the background."
                : response.refresh_started
                  ? "Full NSE scan started in the background. This page will refresh automatically when it finishes."
                  : "A full NSE scan is already running. This page will refresh automatically when it finishes."
            );
          } else {
            setScanNotice(null);
          }
        })
        .catch(() => {
          setError("Scan failed. Check backend logs or verify the API URL.");
        })
        .finally(() => {
          setScanning(false);
        });
    });
  }

  return (
    <main className="page-shell">
      <section className="hero-panel">
        <div>
          <p className="eyebrow">NSE systematic swing scanner</p>
          <h1>Run a ranked swing-trade scan from one dashboard.</h1>
          <p className="hero-copy">
            This MVP uses a rule-based engine with backtest snapshots so you can
            move from broad ideas to a product-ready scanner architecture.
          </p>
        </div>

        <div className="hero-spotlight">
          <div className="spotlight-label">Today&apos;s top setup</div>
          {topSetup ? (
            <>
              <div className="spotlight-symbol">{topSetup.symbol}</div>
              <div className="spotlight-pattern">
                {formatPattern(topSetup.pattern)}
              </div>
              <div className="spotlight-metric">
                {Math.round(topSetup.probability_score * 100)}% probability / RS{" "}
                {Math.round(topSetup.relative_strength.score * 100)}
              </div>
              <Link className="link-button" href={`/stocks/${topSetup.symbol}`}>
                Open detail view
              </Link>
            </>
          ) : (
            <p className="muted">
              Run the scanner to generate ranked opportunities.
            </p>
          )}
        </div>
      </section>

      <section className="stats-grid">
        <article className="stat-card">
          <span className="stat-label">Active opportunities</span>
          <strong>{signals.length}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">Average confidence</span>
          <strong>{averageProbability}%</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">Universe size</span>
          <strong>{stocks.length || 20} stocks</strong>
        </article>
      </section>

      <section className="control-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <h2>Scanner controls</h2>
              <p>Adjust the filters, then trigger a fresh scan.</p>
            </div>
            <button
              className="primary-button"
              onClick={() => void handleScan()}
              disabled={scanning || scanInProgress}
            >
              {scanning
                ? "Starting..."
                : scanInProgress
                  ? "Refreshing in background..."
                  : "Run scanner"}
            </button>
          </div>

          <div className="control-row">
            <label>
              Min probability
              <input
                type="number"
                min={0.4}
                max={0.95}
                step={0.01}
                value={minProbability}
                onChange={(event) => setMinProbability(Number(event.target.value))}
              />
            </label>
            <label>
              Min risk/reward
              <input
                type="number"
                min={1}
                max={5}
                step={0.1}
                value={minRiskReward}
                onChange={(event) => setMinRiskReward(Number(event.target.value))}
              />
            </label>
            <label>
              Max results
              <input
                type="number"
                min={5}
                max={30}
                step={1}
                value={maxResults}
                onChange={(event) => setMaxResults(Number(event.target.value))}
              />
            </label>
            <label>
              Investment amount
              <input
                type="number"
                min={10000}
                step={10000}
                value={investmentAmount}
                onChange={(event) => setInvestmentAmount(Number(event.target.value))}
              />
            </label>
            <label>
              Sector
              <select
                value={selectedSector}
                onChange={(event) => setSelectedSector(event.target.value)}
              >
                {sectors.map((sector) => (
                  <option key={sector} value={sector}>
                    {sector}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {scanNotice ? <p className="muted">{scanNotice}</p> : null}
          {error ? <p className="error-text">{error}</p> : null}
        </article>

        <aside className="panel insight-panel">
          <h2>What this MVP already proves</h2>
          <ul className="insight-list">
            <li>Pattern ranking with consolidation breakout, EMA pullback, relative-strength breakout, support bounce, and VCP.</li>
            <li>Relative-strength overlay versus NIFTY is part of the ranking now.</li>
            <li>Per-signal backtest statistics you can later replace with real historical data.</li>
            <li>Frontend/backend contract ready for live NSE and Yahoo Finance providers.</li>
          </ul>
        </aside>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Top swing opportunities</h2>
            <p>Sorted by setup quality, reward potential, and outperformance versus NIFTY.</p>
          </div>
        </div>

        {loading ? (
          <p className="muted">Loading current signals...</p>
        ) : (
          <OpportunitiesTable signals={signals} />
        )}
      </section>
    </main>
  );
}

function formatPattern(pattern: string) {
  return pattern
    .split("_")
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}
