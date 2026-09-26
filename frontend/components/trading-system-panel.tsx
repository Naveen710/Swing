"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { getLatestSignals, getScanStatus, runScan } from "../lib/api";
import { ScanUniverse, TradeSetup } from "../types";

type Tab = "picks" | "rules" | "risk";
type ScanPhase = "idle" | "scanning" | "polling" | "done" | "error";

const UNIVERSE_OPTIONS: { value: ScanUniverse; label: string; stocks: string }[] = [
  { value: "nifty500",            label: "Nifty 500",         stocks: "~500 stocks" },
  { value: "nifty_smallcap_250",  label: "Smallcap 250",      stocks: "~250 stocks" },
  { value: "mid_small_2000_plus", label: "Mid & Small 2000+", stocks: "2000+ stocks" },
];

const MIN_PROB = 0.65;
const MIN_RR   = 2.5;

function fmtINR(n: number) {
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(n);
}
function fmtDate(s: string) {
  return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short" }).format(new Date(s));
}
function fmtPattern(p: string) {
  return p.split("_").map((w) => w[0]?.toUpperCase() + w.slice(1)).join(" ");
}
function riskColor(level: string) {
  if (level === "low")  return "var(--green)";
  if (level === "high") return "var(--red)";
  return "#b07d00";
}

export function TradingSystemPanel() {
  const [activeTab,  setActiveTab]  = useState<Tab>("picks");
  const [capital,    setCapital]    = useState(1800000);
  const [riskPct,    setRiskPct]    = useState(1.5);
  const [universe,   setUniverse]   = useState<ScanUniverse>("nifty500");
  const [maxResults, setMaxResults] = useState(10);

  const [phase,     setPhase]     = useState<ScanPhase>("idle");
  const [results,   setResults]   = useState<TradeSetup[]>([]);
  const [progress,  setProgress]  = useState({ scanned: 0, total: 0 });
  const [statusMsg, setStatusMsg] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  function stopPolling() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }

  function applyFilters(raw: TradeSetup[]): TradeSetup[] {
    return raw
      .filter(t => t.probability_score >= MIN_PROB && t.risk_reward_ratio >= MIN_RR)
      .slice(0, maxResults);
  }

  async function pollStatus() {
    try {
      const s = await getScanStatus();
      setProgress({ scanned: s.scanned_symbols ?? 0, total: s.universe_size ?? 0 });
      if (!s.scan_in_progress) {
        stopPolling();
        const raw = await getLatestSignals(universe);
        const filtered = applyFilters(raw);
        setResults(filtered);
        setPhase("done");
        setStatusMsg(
          filtered.length
            ? `${filtered.length} stock${filtered.length > 1 ? "s" : ""} passed all system filters (confidence ≥ ${MIN_PROB * 100}%, R:R ≥ ${MIN_RR}×).`
            : "Scan complete — no stocks passed all 4 filters right now. Market conditions may be weak; try again later or switch universe."
        );
      }
    } catch { /* keep polling */ }
  }

  async function handleScan() {
    stopPolling();
    setPhase("scanning");
    setResults([]);
    setProgress({ scanned: 0, total: 0 });
    setStatusMsg("");
    setActiveTab("picks");

    try {
      const data = await runScan({
        universe,
        max_results: maxResults,
        min_probability: MIN_PROB,
        min_risk_reward: MIN_RR,
        investment_amount: capital,
      });

      // Got synchronous results
      if (!data.scan_in_progress && data.results?.length) {
        const filtered = applyFilters(data.results);
        setResults(filtered);
        setPhase("done");
        setStatusMsg(`${filtered.length} stock${filtered.length > 1 ? "s" : ""} passed all system filters.`);
        return;
      }

      // Background scan started — poll for completion
      setPhase("polling");
      setStatusMsg("Scan running in background — checking every 5 seconds…");
      pollRef.current = setInterval(() => { void pollStatus(); }, 5000);
      setTimeout(() => { void pollStatus(); }, 3000);

    } catch (e: unknown) {
      setPhase("error");
      setStatusMsg(e instanceof Error ? e.message : "Could not reach the backend. Make sure the API is running.");
    }
  }

  // Position sizing
  function positionSize(sl: number, entry: number) {
    const riskRs  = capital * riskPct / 100;
    const slFrac  = Math.abs((entry - sl) / entry);
    return slFrac > 0 ? Math.min(riskRs / slFrac, capital * 0.2) : 0;
  }
  function qty(sl: number, entry: number) {
    return entry > 0 ? Math.floor(positionSize(sl, entry) / entry) : 0;
  }

  const totalDeployed = results.reduce((s, r) => s + positionSize(r.stop_loss, r.entry_price), 0);
  const totalProfit   = results.reduce((s, r) => {
    const q = qty(r.stop_loss, r.entry_price);
    return s + q * (r.target_price - r.entry_price);
  }, 0);
  const avgConf = results.length
    ? Math.round(results.reduce((s, r) => s + r.probability_score, 0) / results.length * 100)
    : 0;

  const progressPct = progress.total > 0 ? Math.min(100, Math.round(progress.scanned / progress.total * 100)) : 0;
  const isRunning   = phase === "scanning" || phase === "polling";

  return (
    <section className="panel ts-panel">
      {/* Header */}
      <div className="ts-header">
        <h2 style={{ margin: "0 0 4px" }}>System trading algo</h2>
        <p className="ts-sub">
          Scans NSE stocks through 4 signal filters (RSI 55–75 · 50 EMA trend · volume surge ·
          ADX&nbsp;&gt;&nbsp;22 · R:R ≥ {MIN_RR}× · confidence ≥ {MIN_PROB * 100}%) and returns ranked
          trade picks with exact entry, stop loss, target and position size for your capital.
        </p>
      </div>

      {/* Controls */}
      <div className="ts-controls">
        <div className="ts-cg">
          <label className="ts-label">Capital (₹)</label>
          <input className="ts-input" type="number" min={100000} step={100000} value={capital}
            onChange={e => setCapital(Number(e.target.value))} />
        </div>
        <div className="ts-cg">
          <label className="ts-label">Risk per trade (%)</label>
          <input className="ts-input" type="number" min={0.5} max={3} step={0.1} value={riskPct}
            onChange={e => setRiskPct(Number(e.target.value))} />
        </div>
        <div className="ts-cg">
          <label className="ts-label">Universe</label>
          <select className="ts-input" value={universe}
            onChange={e => setUniverse(e.target.value as ScanUniverse)}>
            {UNIVERSE_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{o.label} ({o.stocks})</option>
            ))}
          </select>
        </div>
        <div className="ts-cg">
          <label className="ts-label">Max picks</label>
          <input className="ts-input" type="number" min={5} max={20} step={1} value={maxResults}
            onChange={e => setMaxResults(Number(e.target.value))} />
        </div>
        <button className="primary-button ts-run-btn" onClick={() => void handleScan()} disabled={isRunning}>
          {phase === "scanning" ? "Starting scan…" : phase === "polling" ? "Scanning…" : "▶ Run system scan"}
        </button>
      </div>

      {/* Progress bar */}
      {phase === "polling" && (
        <div className="ts-prog-outer">
          <div className="ts-prog-track">
            <div className="ts-prog-fill"
              style={{ width: progressPct > 0 ? `${progressPct}%` : "40%",
                animation: progressPct === 0 ? "ts-slide 1.4s ease infinite" : "none" }} />
          </div>
          <span className="ts-prog-text">
            {progress.total > 0
              ? `Scanned ${progress.scanned} / ${progress.total} stocks (${progressPct}%) — results appear automatically when complete`
              : "Scan running in background…"}
          </span>
        </div>
      )}

      {/* Status / error */}
      {statusMsg && <p className={phase === "error" ? "ts-error" : "ts-notice"}>{statusMsg}</p>}

      {/* Summary bar */}
      {phase === "done" && results.length > 0 && (
        <div className="ts-summary">
          <div className="ts-stat">
            <span className="ts-stat-l">Picks found</span>
            <strong>{results.length}</strong>
          </div>
          <div className="ts-stat">
            <span className="ts-stat-l">Capital to deploy</span>
            <strong>{fmtINR(totalDeployed)}</strong>
          </div>
          <div className="ts-stat ts-stat--g">
            <span className="ts-stat-l">Expected profit (all targets hit)</span>
            <strong>{fmtINR(totalProfit)}</strong>
          </div>
          <div className="ts-stat">
            <span className="ts-stat-l">Avg confidence</span>
            <strong>{avgConf}%</strong>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="ts-tabs">
        <button className={`ts-tab ${activeTab === "picks" ? "ts-tab--on" : ""}`} onClick={() => setActiveTab("picks")}>
          Trade picks{phase === "done" && results.length > 0 ? ` (${results.length})` : ""}
        </button>
        <button className={`ts-tab ${activeTab === "rules" ? "ts-tab--on" : ""}`} onClick={() => setActiveTab("rules")}>Signal rules</button>
        <button className={`ts-tab ${activeTab === "risk"  ? "ts-tab--on" : ""}`} onClick={() => setActiveTab("risk")}>Risk limits</button>
      </div>

      {/* ── PICKS ── */}
      {activeTab === "picks" && (
        <div className="ts-fade">
          {phase === "idle" && (
            <div className="empty-state">
              <p>Set your capital and risk above, then click <strong>▶ Run system scan</strong> to generate trade picks.</p>
              <p style={{ color:"var(--muted)", fontSize:"0.88rem", marginTop:6 }}>
                The algo filters every stock in the selected universe through all 4 signal layers and returns only the strongest, most liquid setups ranked by confidence score.
              </p>
            </div>
          )}
          {isRunning && (
            <div className="empty-state">
              <p>Scanning {UNIVERSE_OPTIONS.find(o => o.value === universe)?.stocks ?? "stocks"} — typically takes 30–90 seconds for large universes.</p>
              <p style={{ color:"var(--muted)", fontSize:"0.88rem", marginTop:6 }}>Results will appear automatically once the scan completes.</p>
            </div>
          )}
          {phase === "error" && <div className="empty-state"><p>Could not complete scan. Check that the backend is running and try again.</p></div>}
          {phase === "done" && results.length === 0 && (
            <div className="empty-state">
              <p>No stocks passed all system filters right now.</p>
              <p style={{ color:"var(--muted)", fontSize:"0.88rem", marginTop:6 }}>
                The algo applies strict criteria — zero results means the market lacks strong setups at this moment. Try Smallcap 250 or run again after the next trading session.
              </p>
            </div>
          )}
          {phase === "done" && results.length > 0 && (
            <div className="table-shell">
              <table className="ts-tbl">
                <thead>
                  <tr>
                    <th>Stock</th><th>Pattern</th><th>Entry / CMP</th>
                    <th>Stop loss</th><th>Target</th><th>R:R</th>
                    <th>Confidence</th><th>Qty to buy</th>
                    <th>Deploy ₹</th><th>Expected profit</th><th>Target by</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((r, i) => {
                    const q      = qty(r.stop_loss, r.entry_price);
                    const deploy = positionSize(r.stop_loss, r.entry_price);
                    const profit = q * (r.target_price - r.entry_price);
                    const slPct  = Math.abs((r.entry_price - r.stop_loss) / r.entry_price * 100);
                    return (
                      <tr key={r.symbol} className={i === 0 ? "ts-top" : ""}>
                        <td>
                          <div style={{ display:"flex", alignItems:"flex-start", gap:6 }}>
                            {i === 0 && <span className="ts-star">★</span>}
                            <div>
                              <Link href={`/stocks/${r.symbol}`} className="stock-link">{r.symbol}</Link>
                              <div className="table-subtext">{r.company_name}</div>
                              <div className="table-subtext">{r.sector}</div>
                            </div>
                          </div>
                        </td>
                        <td>
                          {fmtPattern(r.pattern)}
                          <div className="table-subtext">RS {Math.round(r.relative_strength.score * 100)}</div>
                        </td>
                        <td>
                          <strong>{fmtINR(r.entry_price)}</strong>
                          <div className="table-subtext">CMP {fmtINR(r.current_price)}</div>
                        </td>
                        <td>
                          <span style={{ color:"var(--red)" }}>{fmtINR(r.stop_loss)}</span>
                          <div className="table-subtext">−{slPct.toFixed(1)}%</div>
                        </td>
                        <td>
                          <span style={{ color:"var(--green)" }}>{fmtINR(r.target_price)}</span>
                          <div className="table-subtext">+{r.expected_return_pct.toFixed(1)}%</div>
                        </td>
                        <td><strong>{r.risk_reward_ratio.toFixed(1)}×</strong></td>
                        <td>
                          <div className="ts-pb-row">
                            <div className="ts-pb"><div className="ts-pb-fill" style={{ width: Math.round(r.probability_score * 100) + "%" }} /></div>
                            <span>{Math.round(r.probability_score * 100)}%</span>
                          </div>
                          <div className="table-subtext" style={{ color: riskColor(r.event_risk.risk_level) }}>
                            Event: {r.event_risk.risk_level}
                          </div>
                        </td>
                        <td style={{ fontWeight:600 }}>{q > 0 ? `${q} shares` : "—"}</td>
                        <td>{q > 0 ? fmtINR(deploy) : "—"}</td>
                        <td style={{ color:"var(--green)", fontWeight:600 }}>{q > 0 ? `+${fmtINR(profit)}` : "—"}</td>
                        <td>
                          {fmtDate(String(r.estimated_target_date))}
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

      {/* ── SIGNAL RULES ── */}
      {activeTab === "rules" && (
        <div className="ts-fade">
          <p className="ts-intro">All 4 layers run automatically on every scan. A stock must pass all mandatory layers to appear as a pick.</p>
          {([
            { n:"1", title:"Trend filter", badge:"Mandatory", col:"green", rules:[
              { m:"Price above 50 EMA on daily chart",        s:"Bullish structure — no buys in downtrends" },
              { m:"Higher highs, higher lows (last 10 bars)", s:"Confirms uptrend, not just a dead-cat bounce" },
              { m:"Sector in top half by relative strength",  s:"Ride the strongest sectors, avoid laggards" },
            ]},
            { n:"2", title:"Momentum signal", badge:"Mandatory", col:"green", rules:[
              { m:"RSI (14) between 55–75",                          s:"Bullish momentum, not overbought" },
              { m:"MACD histogram positive or recent crossover",      s:"Within last 3 bars" },
              { m:"ADX > 22",                                        s:"Trending move, not sideways chop" },
            ]},
            { n:"3", title:"Volume confirmation", badge:"2 of 3 needed", col:"amber", rules:[
              { m:"Volume ratio > 1.5× 20-day average", s:"Breakout bars need conviction behind them" },
              { m:"Delivery % > 40% (NSE data)",         s:"Serious buyers, not intraday flippers" },
              { m:"Accumulation score > 0.55",           s:"Multi-day up-volume pattern confirmed" },
            ]},
            { n:"4", title:"Price structure & R:R", badge:"Mandatory", col:"green", rules:[
              { m:"Breakout of resistance or pullback to support", s:"Entry trigger is defined precisely before execution" },
              { m:"Stop loss below swing low (max 5% from entry)", s:"Hard rule — no loose stops" },
              { m:`Minimum ${MIN_RR}× Risk:Reward ratio`,          s:"Target at next resistance or 2.5× SL distance" },
            ]},
          ] as const).map(b => (
            <div key={b.n} className="ts-sb">
              <div className="ts-sh">
                <span className="ts-st">{b.n}. {b.title}</span>
                <span className={`ts-badge ts-badge--${b.col}`}>{b.badge}</span>
              </div>
              {b.rules.map((r, i) => (
                <div key={i} className="ts-rr">
                  <span className="ts-arr">→</span>
                  <div><div className="ts-rm">{r.m}</div><div className="ts-rs">{r.s}</div></div>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {/* ── RISK LIMITS ── */}
      {activeTab === "risk" && (
        <div className="ts-fade">
          {([
            { title:"Daily limits", rules:[
              { n:"1", m:`Stop trading if down ${fmtINR(capital * 0.015)} in a day`, s:"1.5% of capital. Log reason, review next morning. Prevents revenge-trading." },
              { n:"2", m:"Max 3 new entries per day",                                  s:"Overtrading dilutes your best setups." },
              { n:"3", m:"No trades 15 min before/after major events",                 s:"RBI policy, results, SEBI news. Gap risk blows stops." },
            ]},
            { title:"Weekly limits", rules:[
              { n:"1", m:`Weekly drawdown limit: ${fmtINR(capital * 0.03)}`, s:"Hit it → 2-day break before damage compounds." },
              { n:"2", m:"Review all trades every Sunday",                   s:"Win rate, avg R:R, which patterns worked. Your journal is your edge." },
            ]},
            { title:"Portfolio limits", rules:[
              { n:"1", m:"Max 5 concurrent open positions",      s:"Each capped at 20% of active capital by the position sizer." },
              { n:"2", m:"Max 2 trades in the same sector",      s:"Correlated positions = hidden concentration risk." },
              { n:"3", m:`Monthly drawdown cap: ${fmtINR(capital * 0.06)}`, s:"Hit it → paper trade for 2 weeks. Protects the base." },
            ]},
            { title:"Scaling rule", rules:[
              { n:"→", m:"Increase sizing only after 3 consecutive profitable months", s:"Then scale by 20% only. Earn the right to risk more." },
              { n:"→", m:"At ₹21L, lock 10% in a liquid fund",                        s:"Never fall below ₹18L base capital. The floor is sacred." },
            ]},
          ] as const).map(b => (
            <div key={b.title} className="ts-rb">
              <p className="ts-rt">{b.title}</p>
              <div className="ts-rr-wrap">
                {b.rules.map(r => (
                  <div key={r.n} className="ts-rr ts-rr-pad">
                    <span className="ts-arr">{r.n}</span>
                    <div><div className="ts-rm">{r.m}</div><div className="ts-rs">{r.s}</div></div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <style>{`
        .ts-panel  { margin-top: 24px; }
        .ts-header { margin-bottom: 18px; }
        .ts-sub    { color: var(--muted); font-size: 0.88rem; line-height: 1.6; margin: 0; max-width: 80ch; }

        .ts-controls {
          display: flex; flex-wrap: wrap; align-items: flex-end; gap: 14px;
          padding: 18px; background: rgba(255,255,255,0.52);
          border: 1px solid var(--line); border-radius: 18px; margin-bottom: 14px;
        }
        .ts-cg    { display: flex; flex-direction: column; gap: 6px; }
        .ts-label { font-size: 0.82rem; color: var(--muted); }
        .ts-input {
          border: 1px solid var(--line); border-radius: 12px;
          background: rgba(255,255,255,0.8); color: var(--text);
          padding: 9px 12px; font: inherit; width: 150px;
        }
        .ts-run-btn { align-self: flex-end; padding: 10px 24px; font-size: 0.95rem; min-width: 164px; }

        .ts-prog-outer  { margin-bottom: 14px; }
        .ts-prog-track  { height: 6px; background: var(--bg-deep,#f0ece8); border-radius: 3px; overflow: hidden; margin-bottom: 6px; }
        .ts-prog-fill   { height: 100%; background: linear-gradient(90deg,#f16800,#ff9a2f); border-radius: 3px; transition: width .4s ease; }
        @keyframes ts-slide { 0%{transform:translateX(-100%)}100%{transform:translateX(350%)} }
        .ts-prog-text   { font-size: 0.82rem; color: var(--muted); }

        .ts-notice { color: var(--muted); font-size: 0.88rem; margin: 0 0 14px; }
        .ts-error  { color: var(--red);   font-size: 0.88rem; margin: 0 0 14px; }

        .ts-summary {
          display: grid; grid-template-columns: repeat(4,1fr);
          gap: 12px; margin-bottom: 18px;
        }
        .ts-stat   { background: rgba(255,255,255,0.58); border: 1px solid var(--line); border-radius: 16px; padding: 14px 16px; }
        .ts-stat--g{ background: rgba(22,108,90,0.06); border-color: rgba(22,108,90,0.18); }
        .ts-stat-l { display: block; font-size: 0.78rem; color: var(--muted); text-transform: uppercase; letter-spacing:.08em; margin-bottom: 5px; }
        .ts-stat strong { font-size: 1.3rem; font-family: var(--font-space-grotesk),sans-serif; }
        .ts-stat--g strong { color: var(--green); }

        .ts-tabs  { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 18px; }
        .ts-tab   { padding: 8px 18px; border-radius: 999px; border: 1px solid var(--line); background: rgba(255,255,255,0.55); color: var(--muted); font-size: 0.88rem; font-weight: 500; cursor: pointer; transition: all 120ms ease; }
        .ts-tab:hover { border-color: rgba(241,104,0,0.28); color: var(--text); }
        .ts-tab--on   { background: linear-gradient(135deg,#f16800,#ff9a2f); color: #fff7ef; border-color: transparent; }

        .ts-fade { animation: ts-in 180ms ease; }
        @keyframes ts-in { from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:translateY(0)} }

        .ts-tbl { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
        .ts-tbl th { color: var(--muted); font-size: 0.76rem; text-transform: uppercase; letter-spacing:.08em; padding: 10px; border-bottom: 1px solid var(--line); text-align: left; font-weight: 500; white-space: nowrap; }
        .ts-tbl td { padding: 11px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
        .ts-tbl tr:last-child td { border-bottom: none; }
        .ts-top td { background: rgba(255,122,0,0.04); }
        .ts-star   { color: var(--accent); font-size: 1rem; padding-top: 1px; }

        .ts-pb-row { display:flex; align-items:center; gap:6px; font-size:0.85rem; }
        .ts-pb     { flex:1; height:5px; background:var(--bg-deep,#f0ece8); border-radius:3px; min-width:50px; overflow:hidden; }
        .ts-pb-fill{ height:100%; background:linear-gradient(90deg,#f16800,#ff9a2f); border-radius:3px; }

        .ts-intro { color: var(--muted); font-size: 0.88rem; line-height: 1.6; margin: 0 0 16px; }
        .ts-sb { background: rgba(255,255,255,0.52); border: 1px solid var(--line); border-radius: 16px; padding: 14px 16px; margin-bottom: 12px; }
        .ts-sh { display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; }
        .ts-st { font-weight:600; font-size:0.94rem; }
        .ts-badge { font-size:0.74rem; font-weight:600; padding:3px 10px; border-radius:999px; }
        .ts-badge--green { background:rgba(22,108,90,0.1); color:var(--green); }
        .ts-badge--amber { background:rgba(180,120,0,0.1); color:#8a5c00; }

        .ts-rr     { display:flex; gap:10px; padding:7px 0; border-bottom:1px solid var(--line); }
        .ts-rr:last-child { border-bottom:none; padding-bottom:0; }
        .ts-rr-pad { padding:12px 14px; }
        .ts-arr    { color:var(--accent); font-weight:700; font-size:0.8rem; padding-top:2px; min-width:18px; }
        .ts-rm     { font-size:0.9rem; font-weight:500; }
        .ts-rs     { font-size:0.82rem; color:var(--muted); margin-top:2px; }

        .ts-rb     { margin-bottom:16px; }
        .ts-rt     { font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:.1em; color:var(--muted); margin:0 0 8px; }
        .ts-rr-wrap{ background:rgba(255,255,255,0.52); border:1px solid var(--line); border-radius:16px; overflow:hidden; }

        @media (max-width:960px) {
          .ts-summary  { grid-template-columns: 1fr 1fr; }
          .ts-controls { flex-direction:column; }
          .ts-input    { width:100%; }
        }
      `}</style>
    </section>
  );
}
