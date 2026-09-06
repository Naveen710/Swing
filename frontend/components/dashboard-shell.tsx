"use client";

import Link from "next/link";
import { startTransition, useEffect, useMemo, useState } from "react";

import { getLatestSignals, getScanStatus, getStocks, runScan } from "../lib/api";
import { ScanUniverse, StockSummary, TradeSetup } from "../types";
import { OpportunitiesTable } from "./opportunities-table";
import { TradingSystemPanel } from "./trading-system-panel";

const DEFAULT_INVESTMENT = 100000;
const DEFAULT_UNIVERSE: ScanUniverse = "nifty500";
const UNIVERSE_LABELS: Record<ScanUniverse, string> = {
  nifty500: "Nifty 500",
  nifty_smallcap_250: "Nifty Smallcap 250",
  mid_small_2000_plus: "Mid & Small 2000+"
};

export function DashboardShell() {
  const [signals, setSignals] = useState<TradeSetup[]>([]);
  const [stocks, setStocks] = useState<StockSummary[]>([]);
  const [selectedUniverse, setSelectedUniverse] =
    useState<ScanUniverse>(DEFAULT_UNIVERSE);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [scanInProgress, setScanInProgress] = useState(false);
  const [scanNotice, setScanNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [minProbability, setMinProbability] = useState(0.55);
  const [minRiskReward, setMinRiskReward] = useState(1.8);
  const [maxResults, setMaxResults] = useState(12);
  const [investmentAmount, setInvestmentAmount] = useState(DEFAULT_INVESTMENT);
  const [selectedSector, setSelectedSector] = useState("All sectors");
  const [showTradingSystem, setShowTradingSystem] = useState(false);

  async function refreshDashboard(
    preferredUniverse?: ScanUniverse,
    options?: { silent?: boolean }
  ) {
    if (!options?.silent) {
      setRefreshing(true);
    }

    try {
      const status = await getScanStatus();
      const activeUniverse = preferredUniverse ?? status.universe ?? DEFAULT_UNIVERSE;
      const [stockUniverse, latestSignals] = await Promise.all([
        getStocks(activeUniverse),
        getLatestSignals(activeUniverse)
      ]);

      setSelectedUniverse(activeUniverse);
      setStocks(stockUniverse);
      setSignals(latestSignals);
      setScanInProgress(status.scan_in_progress);

      if (status.scan_in_progress) {
        setScanNotice(
          latestSignals.length
            ? `Showing the latest saved opportunities while a fresh ${formatUniverseLabel(activeUniverse)} scan runs in the background.`
            : `Scanning ${status.scanned_symbols} of ${status.universe_size} ${formatUniverseLabel(activeUniverse)} stocks${activeUniverse === "mid_small_2000_plus" ? " with 10 parallel workers" : ""}. Click Refresh results to pull the newest saved board.`
        );
      } else if (!options?.silent) {
        setScanNotice(
          latestSignals.length
            ? `Latest ${formatUniverseLabel(activeUniverse)} results loaded.`
            : `No opportunities matched the current filters for ${formatUniverseLabel(activeUniverse)}. Try lowering min probability or min risk/reward and run again.`
        );
      }

      setError(null);
    } catch {
      setError("Unable to refresh the latest results from the backend.");
    } finally {
      if (!options?.silent) {
        setRefreshing(false);
      }
    }
  }

  useEffect(() => {
    async function bootstrap() {
      try {
        await refreshDashboard(undefined, { silent: true });
      } catch {
        setError("Unable to reach the backend API. Start the FastAPI server and retry.");
      } finally {
        setLoading(false);
      }
    }

    void bootstrap();
  }, []);

  useEffect(() => {
    if (selectedSector === "All sectors") {
      return;
    }

    const availableSectors = new Set(stocks.map((stock) => stock.sector));
    if (!availableSectors.has(selectedSector)) {
      setSelectedSector("All sectors");
    }
  }, [selectedSector, stocks]);

  useEffect(() => {
    if (!scanInProgress) {
      return;
    }

    let cancelled = false;
    let timeoutId: number | undefined;

    const pollStatus = async () => {
      try {
        const status = await getScanStatus();
        if (cancelled) {
          return;
        }

        const statusUniverse = status.universe ?? selectedUniverse;
        setSelectedUniverse(statusUniverse);

        if (!status.scan_in_progress) {
          setScanInProgress(false);
          if (!cancelled) {
            await refreshDashboard(statusUniverse);
          }
          return;
        }

        setScanNotice(
          `Scanning ${status.scanned_symbols} of ${status.universe_size} ${formatUniverseLabel(statusUniverse)} stocks${statusUniverse === "mid_small_2000_plus" ? " with 10 parallel workers" : ""}. The board will refresh automatically when the run finishes.`
        );
      } catch {
        if (cancelled) {
          return;
        }
      }

      timeoutId = window.setTimeout(() => {
        void pollStatus();
      }, 3000);
    };

    timeoutId = window.setTimeout(() => {
      void pollStatus();
    }, 3000);

    return () => {
      cancelled = true;
      if (timeoutId) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [scanInProgress, selectedUniverse]);

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

  async function handleScan(universe: ScanUniverse) {
    setSelectedUniverse(universe);
    setScanning(true);
    setError(null);

    startTransition(() => {
      void Promise.all([
        runScan({
          universe,
          max_results: maxResults,
          min_probability: minProbability,
          min_risk_reward: minRiskReward,
          investment_amount: investmentAmount,
          sectors: selectedSector === "All sectors" ? undefined : [selectedSector]
        }),
        getStocks(universe)
      ])
        .then(([response, stockUniverse]) => {
          setStocks(stockUniverse);
          setSignals(response.results);
          setScanInProgress(response.scan_in_progress);
          if (response.scan_in_progress) {
            setScanNotice(
              response.refresh_started && response.results.length
                ? `Showing the latest saved opportunities while a fresh ${formatUniverseLabel(universe)} scan runs in the background.`
                : response.refresh_started
                  ? `${formatUniverseLabel(universe)} scan started in the background${universe === "mid_small_2000_plus" ? " with 10 parallel workers" : ""}. Use Refresh results any time to pull the latest saved board.`
                  : "Another scan is already running. Use Refresh results to pull the latest saved board."
            );
          } else if (!response.results.length) {
            setScanNotice(
              `No opportunities matched the current filters for ${formatUniverseLabel(universe)}. Try lowering min probability or min risk/reward and run again.`
            );
          } else {
            setScanNotice("Scan complete. Results are ready below.");
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
            Toggle between Nifty 500, Nifty Smallcap 250, or a broader 2000+
            mid-and-small-cap discovery basket, then run the same parameter set
            against that universe.
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
              Pick a universe button to generate ranked opportunities.
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
          <span className="stat-label">{formatUniverseLabel(selectedUniverse)}</span>
          <strong>{stocks.length || 20} stocks</strong>
        </article>
      </section>

      <section className="control-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <h2>Scanner controls</h2>
              <p>Adjust the filters, then choose which index basket to scan.</p>
            </div>
            <div className="button-group">
              <button
                className={
                  selectedUniverse === "nifty500" ? "primary-button" : "secondary-button"
                }
                onClick={() => void handleScan("nifty500")}
                disabled={scanning || refreshing}
              >
                {scanning && selectedUniverse === "nifty500"
                  ? "Starting..."
                  : "Scan Nifty 500"}
              </button>
              <button
                className={
                  selectedUniverse === "nifty_smallcap_250"
                    ? "primary-button"
                    : "secondary-button"
                }
                onClick={() => void handleScan("nifty_smallcap_250")}
                disabled={scanning || refreshing}
              >
                {scanning && selectedUniverse === "nifty_smallcap_250"
                  ? "Starting..."
                  : "Scan Smallcap 250"}
              </button>
              <button
                className={
                  selectedUniverse === "mid_small_2000_plus"
                    ? "primary-button"
                    : "secondary-button"
                }
                onClick={() => void handleScan("mid_small_2000_plus")}
                disabled={scanning || refreshing}
              >
                {scanning && selectedUniverse === "mid_small_2000_plus"
                  ? "Starting..."
                  : "Scan Mid & Small 2000+"}
              </button>
              <button
                className="secondary-button"
                onClick={() => void refreshDashboard(selectedUniverse)}
                disabled={refreshing || loading}
              >
                {refreshing ? "Refreshing..." : "Refresh results"}
              </button>
              <button
                className={showTradingSystem ? "primary-button" : "secondary-button"}
                onClick={() => setShowTradingSystem((v) => !v)}
              >
                {showTradingSystem ? "Hide trading system" : "Trading system"}
              </button>
            </div>
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
          <h2>What will help you find better stocks</h2>
          <ul className="insight-list">
            <li>Add a liquidity filter using average traded value so illiquid names do not rank above cleaner swing setups.</li>
            <li>Rank sectors by relative strength and show only stocks from the strongest sectors when the broad market is weak.</li>
            <li>Add earnings and corporate-action awareness so fresh setups are not taken right into high-volatility event risk.</li>
            <li>Track breakout quality with multi-day volume expansion, not just one-day spikes, to reduce false starts.</li>
            <li>The new 2000+ mid/small-cap button widens discovery while keeping the current Nifty buttons unchanged.</li>
          </ul>
        </aside>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Top swing opportunities</h2>
            <p>
              Sorted by setup quality, reward potential, and outperformance
              versus NIFTY for {formatUniverseLabel(selectedUniverse)}.
            </p>
          </div>
        </div>

        {loading ? (
          <p className="muted">Loading current signals...</p>
        ) : (
          <OpportunitiesTable signals={signals} />
        )}
      </section>

      {showTradingSystem && <TradingSystemPanel />}
    </main>
  );
}

function formatPattern(pattern: string) {
  return pattern
    .split("_")
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}

function formatUniverseLabel(universe: ScanUniverse) {
  return UNIVERSE_LABELS[universe];
}
