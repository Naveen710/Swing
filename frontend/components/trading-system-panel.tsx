"use client";

import Link from "next/link";
import { useState } from "react";
import { runScan } from "../lib/api";
import { ScanUniverse, TradeSetup } from "../types";

type Tab = "picks" | "rules" | "risk";

const UNIVERSE_OPTIONS: { value: ScanUniverse; label: string }[] = [
  { value: "nifty500", label: "Nifty 500" },
  { value: "nifty_smallcap_250", label: "Smallcap 250" },
  { value: "mid_small_2000_plus", label: "Mid & Small 2000+" },
];

function fmtINR(n: number) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(n);
}

function fmtDate(s: string) {
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
  }).format(new Date(s));
}

function fmtPattern(p: string) {
  return p.split("_").map((w) => w[0]?.toUpperCase() + w.slice(1)).join(" ");
}

function riskColor(level: string) {
  if (level === "low") return "var(--green)";
  if (level === "high") return "var(--red)";
  return "#b07d00";
}

export function TradingSystemPanel() {
  const [activeTab, setActiveTab] = useState<Tab>("picks");

  // Scan params
  const [capital, setCapital] = useState(1800000);
  const [riskPct, setRiskPct] = useState(1.5);
  const [universe, setUniverse] = useState<ScanUniverse>("nifty500");
  const [maxResults, setMaxResults] = useState(10);

  // Scan state
  const [results, setResults] = useState<TradeSetup[]>([]);
  const [scanning, setScanning] = useState(false);
  const [scanMsg, setScanMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scanned, setScanned] = useState(false);

  async function handleScan() {
    setScanning(true);
    setError(null);
    setScanMsg(null);
    setScanned(false);
    setActiveTab("picks");
    try {
      const resp = await runScan({
        universe,
        max_results: maxResults,
        min_probability: 0.65,   // system algo threshold
        min_risk_reward: 2.5,    // system algo threshold
        investment_amount: capital,
      });
      setResults(resp.results);
      setScanned(true);
      if (resp.scan_in_progress) {
        setScanMsg(
          resp.results.length
            ? "Showing cached results while fresh scan runs in background. Re-run in a minute for latest picks."
            : "Scan started in background — click Run again in ~60 seconds to see results."
        );
      } else if (!resp.results.length) {
        setScanMsg("No stocks passed all 4 system filters right now. Market may be weak — try again tomorrow or lower the universe.");
      } else {
        setScanMsg(`${resp.results.length} stocks passed the system algo filters.`);
      }
    } catch {
      setError("Could not reach the backend. Make sure the API is running.");
    } finally {
      setScanning(false);
    }
  }

  // Per-trade position sizing
  function positionSize(sl: number, entry: number) {
    const riskRs = capital * riskPct / 100;
    const slPct = Math.abs((entry - sl) / entry);
    return slPct > 0 ? Math.min(riskRs / slPct, capital * 0.2) : 0; // cap at 20% of capital
  }

  function shares(sl: number, entry: number) {
    const pos = positionSize(sl, entry);
    return entry > 0 ? Math.floor(pos / entry) : 0;
  }

  const totalDeployed = results.reduce((s, r) => s + positionSize(r.stop_loss, r.entry_price), 0);
  const totalExpectedProfit = results.reduce((s, r) => {
    const qty = shares(r.stop_loss, r.entry_price);
    return s + qty * (r.target_price - r.entry_price);
  }, 0);

  return (
    <section className="panel ts-panel">
      {/* ── Header + controls ── */}
      <div className="ts-header">
        <div>
          <h2 style={{ margin: "0 0 4px" }}>System trading algo</h2>
          <p className="ts-sub">
            Runs your 4-signal filter (RSI 55–75 · EMA trend · volume surge · price structure · ADX &gt; 22 · R:R ≥ 2.5) against the selected universe and returns ranked trade picks with position sizing.
          </p>
        </div>
      </div>

      {/* ── Run controls ── */}
      <div className="ts-controls">
        <div className="ts-control-group">
          <label className="ts-label">Capital (₹)</label>
          <input
            type="number"
            className="ts-input"
            min={100000}
            step={100000}
            value={capital}
            onChange={(e) => setCapital(Number(e.target.value))}
          />
        </div>
        <div className="ts-control-group">
          <label className="ts-label">Risk per trade (%)</label>
          <input
            type="number"
            className="ts-input"
            min={0.5}
            max={3}
            step={0.1}
            value={riskPct}
            onChange={(e) => setRiskPct(Number(e.target.value))}
          />
        </div>
        <div className="ts-control-group">
          <label className="ts-label">Universe</label>
          <select
            className="ts-input"
            value={universe}
            onChange={(e) => setUniverse(e.target.value as ScanUniverse)}
          >
            {UNIVERSE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
        <div className="ts-control-group">
          <label className="ts-label">Max picks</label>
          <input
            type="number"
            className="ts-input"
            min={5}
            max={20}
            step={1}
            value={maxResults}
            onChange={(e) => setMaxResults(Number(e.target.value))}
          />
        </div>
        <button
          className="primary-button ts-run-btn"
          onClick={() => void handleScan()}
          disabled={scanning}
        >
          {scanning ? "Scanning..." : "▶ Run system scan"}
        </button>
      </div>

      {scanMsg && <p className="ts-notice">{scanMsg}</p>}
      {error && <p className="ts-error">{error}</p>}

      {/* ── Summary stats (shown after scan) ── */}
      {scanned && results.length > 0 && (
        <div className="ts-summary-row">
          <div className="ts-stat">
            <span className="ts-stat-label">Picks found</span>
            <strong>{results.length}</strong>
          </div>
          <div className="ts-stat">
            <span className="ts-stat-label">Capital to deploy</span>
            <strong>{fmtINR(totalDeployed)}</strong>
          </div>
          <div className="ts-stat">
            <span className="ts-stat-label">Expected profit (if all hit target)</span>
            <strong style={{ color: "var(--green)" }}>{fmtINR(totalExpectedProfit)}</strong>
          </div>
          <div className="ts-stat">
            <span className="ts-stat-label">Avg confidence</span>
            <strong>
              {Math.round(results.reduce((s, r) => s + r.probability_score, 0) / results.length * 100)}%
            </strong>
          </div>
        </div>
      )}

      {/* ── Tabs ── */}
      <div className="ts-tabs">
        <button className={`ts-tab ${activeTab === "picks" ? "ts-tab--active" : ""}`} onClick={() => setActiveTab("picks")}>
          Trade picks {scanned && results.length > 0 ? `(${results.length})` : ""}
        </button>
        <button className={`ts-tab ${activeTab === "rules" ? "ts-tab--active" : ""}`} onClick={() => setActiveTab("rules")}>
          Signal rules
        </button>
        <button className={`ts-tab ${activeTab === "risk" ? "ts-tab--active" : ""}`} onClick={() => setActiveTab("risk")}>
          Risk limits
        </button>
      </div>

      {/* ── PICKS tab ── */}
      {activeTab === "picks" && (
        <div className="ts-fade">
          {!scanned ? (
            <div className="empty-state">
              <p>Set your capital and risk, then click <strong>▶ Run system scan</strong> to generate trade picks.</p>
            </div>
          ) : results.length === 0 ? (
            <div className="empty-state">
              <p>No stocks passed all system filters. Try Nifty 500 or come back when market conditions are stronger.</p>
            </div>
          ) : (
            <div className="table-shell">
              <table className="ts-picks-table">
                <thead>
                  <tr>
                    <th>Stock</th>
                    <th>Pattern</th>
                    <th>Entry</th>
                    <th>Stop loss</th>
                    <th>Target</th>
                    <th>R:R</th>
                    <th>Confidence</th>
                    <th>Qty</th>
                    <th>Deploy ₹</th>
                    <th>Expected profit</th>
                    <th>Target by</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((r, i) => {
                    const qty = shares(r.stop_loss, r.entry_price);
                    const deploy = positionSize(r.stop_loss, r.entry_price);
                    const profit = qty * (r.target_price - r.entry_price);
                    const slPct = Math.abs((r.entry_price - r.stop_loss) / r.entry_price * 100);
                    return (
                      <tr key={r.symbol} className={i === 0 ? "ts-top-pick" : ""}>
                        <td>
                          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                            {i === 0 && <span className="ts-crown">★</span>}
                            <div>
                              <Link href={`/stocks/${r.symbol}`} className="stock-link">{r.symbol}</Link>
                              <div className="table-subtext">{r.company_name}</div>
                              <div className="table-subtext">{r.sector}</div>
                            </div>
                          </div>
                        </td>
                        <td>
                          {fmtPattern(r.pattern)}
                          <div className="table-subtext">
                            RS {Math.round(r.relative_strength.score * 100)}
                          </div>
                        </td>
                        <td><strong>{fmtINR(r.entry_price)}</strong></td>
                        <td>
                          <span style={{ color: "var(--red)" }}>{fmtINR(r.stop_loss)}</span>
                          <div className="table-subtext">−{slPct.toFixed(1)}%</div>
                        </td>
                        <td>
                          <span style={{ color: "var(--green)" }}>{fmtINR(r.target_price)}</span>
                          <div className="table-subtext">+{r.expected_return_pct.toFixed(1)}%</div>
                        </td>
                        <td><strong>{r.risk_reward_ratio.toFixed(1)}×</strong></td>
                        <td>
                          <div className="ts-prob-wrap">
                            <div className="ts-prob-bar">
                              <div className="ts-prob-fill" style={{ width: Math.round(r.probability_score * 100) + "%" }} />
                            </div>
                            <span>{Math.round(r.probability_score * 100)}%</span>
                          </div>
                          <div className="table-subtext" style={{ color: riskColor(r.event_risk.risk_level) }}>
                            Event: {r.event_risk.risk_level}
                          </div>
                        </td>
                        <td>{qty > 0 ? qty + " shares" : "—"}</td>
                        <td>{qty > 0 ? fmtINR(deploy) : "—"}</td>
                        <td style={{ color: "var(--green)", fontWeight: 600 }}>
                          {qty > 0 ? fmtINR(profit) : "—"}
                        </td>
                        <td>
                          {fmtDate(r.estimated_target_date)}
                          <div className="table-subtext">{r.estimated_target_sessions} sessions</div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── SIGNAL RULES tab ── */}
      {activeTab === "rules" && (
        <div className="ts-fade">
          <p className="ts-intro">All 4 layers are applied automatically by the algo. This is what it checks for every stock before including it as a pick.</p>

          {[
            {
              n: "1", title: "Trend filter", badge: "Mandatory", color: "green",
              rules: [
                { main: "Price above 50 EMA on daily chart", sub: "Bullish structure — no buys in downtrends" },
                { main: "Higher highs, higher lows (last 10 candles)", sub: "Confirms uptrend, not just a bounce" },
                { main: "Sector in top half by relative strength", sub: "Don't swim against the tide" },
              ]
            },
            {
              n: "2", title: "Momentum signal", badge: "Mandatory", color: "green",
              rules: [
                { main: "RSI (14) between 55–75", sub: "Bullish momentum, not overbought" },
                { main: "MACD histogram positive or recent crossover", sub: "Within last 3 bars" },
                { main: "ADX > 22", sub: "Trending move, not sideways chop" },
              ]
            },
            {
              n: "3", title: "Volume confirmation", badge: "2 of 3 needed", color: "amber",
              rules: [
                { main: "Volume ratio > 1.5× (20-day average)", sub: "Breakout bars must have conviction" },
                { main: "Delivery % > 40% where available", sub: "Serious buyers, not just intraday flippers" },
                { main: "Accumulation score > 0.55", sub: "Multi-day up-volume pattern" },
              ]
            },
            {
              n: "4", title: "Price structure & risk/reward", badge: "Mandatory", color: "green",
              rules: [
                { main: "Breakout of resistance or pullback to support", sub: "Entry trigger defined before execution" },
                { main: "Stop loss below swing low (max 5% from entry)", sub: "Hard rule — no loose stops" },
                { main: "Minimum 2.5× Risk:Reward ratio", sub: "Target must be at next resistance or 2.5× SL distance" },
              ]
            },
          ].map((block) => (
            <div key={block.n} className="ts-signal-block">
              <div className="ts-signal-head">
                <span className="ts-signal-title">{block.n}. {block.title}</span>
                <span className={`ts-badge ts-badge--${block.color}`}>{block.badge}</span>
              </div>
              {block.rules.map((r, i) => (
                <div key={i} className="ts-rule-row">
                  <span className="ts-arrow">→</span>
                  <div>
                    <div className="ts-rule-main">{r.main}</div>
                    <div className="ts-rule-sub">{r.sub}</div>
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {/* ── RISK LIMITS tab ── */}
      {activeTab === "risk" && (
        <div className="ts-fade">
          {[
            {
              title: "Daily limits",
              rules: [
                { n: "1", main: `Stop trading if down ${fmtINR(capital * 0.015)} in a day`, sub: "1.5% of your capital. Log reason, review next morning." },
                { n: "2", main: "Max 3 new entries per day", sub: "Overtrading dilutes your best setups." },
                { n: "3", main: "No trades 15 min before/after major events", sub: "RBI policy, results, SEBI news. Gap risk blows through stops." },
              ]
            },
            {
              title: "Weekly limits",
              rules: [
                { n: "1", main: `Weekly drawdown limit: ${fmtINR(capital * 0.03)}`, sub: "Hit it → take a 2-day break before you make it worse." },
                { n: "2", main: "Review all trades every Sunday", sub: "Win rate, avg R:R, which patterns worked. Your journal IS your edge." },
              ]
            },
            {
              title: "Portfolio limits",
              rules: [
                { n: "1", main: "Max 5 concurrent open positions", sub: "Each capped at 20% of active capital by the position sizer above." },
                { n: "2", main: "Max 2 trades in the same sector", sub: "Correlated positions = hidden concentration risk." },
                { n: "3", main: `Monthly drawdown limit: ${fmtINR(capital * 0.06)}`, sub: "Hit it → paper trade for 2 weeks. Protects the base." },
              ]
            },
            {
              title: "Scaling rule",
              rules: [
                { n: "→", main: "Increase position size only after 3 consecutive profitable months", sub: "Then scale by 20% only. Earn the right to risk more." },
                { n: "→", main: "At ₹21L milestone, lock 10% in liquid fund", sub: "Never fall below ₹18L base. The floor is sacred." },
              ]
            },
          ].map((block) => (
            <div key={block.title} className="ts-risk-block">
              <p className="ts-risk-title">{block.title}</p>
              <div className="ts-risk-rules">
                {block.rules.map((r) => (
                  <div key={r.n} className="ts-rule-row ts-risk-row">
                    <span className="ts-arrow">{r.n}</span>
                    <div>
                      <div className="ts-rule-main">{r.main}</div>
                      <div className="ts-rule-sub">{r.sub}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <style>{`
        .ts-panel { margin-top: 24px; }
        .ts-header { margin-bottom: 18px; }
        .ts-sub { color: var(--muted); font-size: 0.88rem; line-height: 1.6; margin: 0; max-width: 80ch; }

        .ts-controls {
          display: flex;
          flex-wrap: wrap;
          align-items: flex-end;
          gap: 14px;
          padding: 18px;
          background: rgba(255,255,255,0.52);
          border: 1px solid var(--line);
          border-radius: 18px;
          margin-bottom: 14px;
        }
        .ts-control-group { display: flex; flex-direction: column; gap: 6px; }
        .ts-label { font-size: 0.82rem; color: var(--muted); }
        .ts-input {
          border: 1px solid var(--line);
          border-radius: 12px;
          background: rgba(255,255,255,0.8);
          color: var(--text);
          padding: 9px 12px;
          font: inherit;
          width: 140px;
        }
        .ts-run-btn { align-self: flex-end; padding: 10px 24px; font-size: 0.95rem; }

        .ts-notice { color: var(--muted); font-size: 0.88rem; margin: 0 0 14px; }
        .ts-error  { color: var(--red);   font-size: 0.88rem; margin: 0 0 14px; }

        .ts-summary-row {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 12px;
          margin-bottom: 18px;
        }
        .ts-stat {
          background: rgba(255,255,255,0.58);
          border: 1px solid var(--line);
          border-radius: 16px;
          padding: 14px 16px;
        }
        .ts-stat-label { display: block; font-size: 0.78rem; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; margin-bottom: 5px; }
        .ts-stat strong { font-size: 1.35rem; font-family: var(--font-space-grotesk), sans-serif; }

        .ts-tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 18px; }
        .ts-tab {
          padding: 8px 18px; border-radius: 999px; border: 1px solid var(--line);
          background: rgba(255,255,255,0.55); color: var(--muted);
          font-size: 0.88rem; font-weight: 500; cursor: pointer;
          transition: all 120ms ease;
        }
        .ts-tab:hover { border-color: rgba(241,104,0,0.28); color: var(--text); }
        .ts-tab--active {
          background: linear-gradient(135deg,#f16800,#ff9a2f);
          color: #fff7ef; border-color: transparent;
        }
        .ts-fade { animation: tsfade 180ms ease; }
        @keyframes tsfade { from { opacity:0; transform:translateY(5px); } to { opacity:1; transform:translateY(0); } }

        .ts-picks-table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
        .ts-picks-table th {
          color: var(--muted); font-size: 0.76rem; text-transform: uppercase;
          letter-spacing: .08em; padding: 10px 10px; border-bottom: 1px solid var(--line);
          text-align: left; font-weight: 500; white-space: nowrap;
        }
        .ts-picks-table td { padding: 11px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
        .ts-picks-table tr:last-child td { border-bottom: none; }
        .ts-top-pick td { background: rgba(255,122,0,0.04); }
        .ts-crown { color: var(--accent); font-size: 1rem; }

        .ts-prob-wrap { display: flex; align-items: center; gap: 6px; font-size: 0.85rem; }
        .ts-prob-bar { flex:1; height: 5px; background: var(--bg-deep); border-radius: 3px; min-width: 50px; overflow: hidden; }
        .ts-prob-fill { height: 100%; background: linear-gradient(90deg,#f16800,#ff9a2f); border-radius: 3px; }

        .ts-intro { color: var(--muted); font-size: 0.88rem; line-height: 1.6; margin: 0 0 16px; }

        .ts-signal-block {
          background: rgba(255,255,255,0.52); border: 1px solid var(--line);
          border-radius: 16px; padding: 14px 16px; margin-bottom: 12px;
        }
        .ts-signal-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
        .ts-signal-title { font-weight: 600; font-size: 0.94rem; }
        .ts-badge { font-size: 0.74rem; font-weight: 600; padding: 3px 10px; border-radius: 999px; }
        .ts-badge--green { background: rgba(22,108,90,0.1); color: var(--green); }
        .ts-badge--amber { background: rgba(180,120,0,0.1); color: #8a5c00; }

        .ts-rule-row { display: flex; gap: 10px; padding: 7px 0; border-bottom: 1px solid var(--line); }
        .ts-rule-row:last-child { border-bottom: none; padding-bottom: 0; }
        .ts-risk-row { padding: 12px 14px; }
        .ts-arrow { color: var(--accent); font-weight: 700; font-size: 0.8rem; padding-top: 2px; min-width: 18px; }
        .ts-rule-main { font-size: 0.9rem; font-weight: 500; }
        .ts-rule-sub  { font-size: 0.82rem; color: var(--muted); margin-top: 2px; }

        .ts-risk-block { margin-bottom: 16px; }
        .ts-risk-title { font-size: 0.78rem; font-weight: 600; text-transform: uppercase; letter-spacing: .1em; color: var(--muted); margin: 0 0 8px; }
        .ts-risk-rules { background: rgba(255,255,255,0.52); border: 1px solid var(--line); border-radius: 16px; overflow: hidden; }

        @media (max-width: 960px) {
          .ts-summary-row { grid-template-columns: 1fr 1fr; }
          .ts-controls { flex-direction: column; }
          .ts-input { width: 100%; }
        }
      `}</style>
    </section>
  );
}
