"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getStockDetail } from "../lib/api";
import { StockDetailResponse } from "../types";
import {
  fmtINR as fmtRupee,
  positionQty,
  tradeFromSetup,
  useJournal,
  useTradingSettings,
  useWatchlist,
  watchFromSetup,
} from "../lib/store";
import { AppNav } from "./app-nav";
import { PriceChart } from "./price-chart";

export function StockDetailShell({ symbol }: { symbol: string }) {
  const [detail,     setDetail]     = useState<StockDetailResponse | null>(null);
  const [loading,    setLoading]    = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error,      setError]      = useState<string | null>(null);
  const [journal, setJournal] = useJournal();
  const [watchlist, setWatchlist] = useWatchlist();
  const [settings] = useTradingSettings();
  const [notice, setNotice] = useState<string | null>(null);

  async function loadDetail(options?: { silent?: boolean }) {
    if (!options?.silent) setRefreshing(true);
    try {
      const response = await getStockDetail(symbol);
      setDetail(response);
      setError(null);
    } catch {
      setError("Unable to load this stock. Verify the backend is running.");
    } finally {
      if (!options?.silent) setRefreshing(false);
      setLoading(false);
    }
  }

  useEffect(() => { void loadDetail({ silent: true }); }, [symbol]);

  if (loading) {
    return (
      <main className="page-shell">
        <div className="sd-loading">
          <div className="sd-spinner" />
          <p className="muted">Loading stock detail…</p>
        </div>
      </main>
    );
  }

  if (error || !detail) {
    return (
      <main className="page-shell">
        <Link href="/" className="sd-back">← Back to scanner</Link>
        <p className="error-text" style={{ marginTop: 16 }}>{error ?? "Stock detail unavailable."}</p>
      </main>
    );
  }

  const s = detail.latest_signal;

  return (
    <main className="page-shell">
      <AppNav />

      {/* ── Nav ── */}
      <div className="sd-nav">
        <Link href="/" className="sd-back">← Back to scanner</Link>
        <button className="secondary-button sd-refresh-btn"
          onClick={() => void loadDetail()} disabled={refreshing} type="button">
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {/* ── Hero ── */}
      <section className="sd-hero">
        <div className="sd-hero-left">
          <span className="eyebrow">{detail.stock.sector} · {detail.stock.market_cap_bucket.replace("_cap","").toUpperCase()} CAP</span>
          <h1 className="sd-company">{detail.stock.company_name}</h1>
          <span className="sd-symbol-chip">{detail.stock.symbol}</span>
        </div>

        {s && (
          <div className="sd-signal-card">
            <div className="sd-sc-top">
              <span className="sd-pattern-badge">{fmtPattern(s.pattern)}</span>
              <span className="sd-conf">{Math.round(s.probability_score * 100)}% confidence</span>
            </div>
            <div className="sd-sc-prices">
              <div className="sd-sc-price">
                <span className="sd-sc-lbl">Entry</span>
                <strong className="sd-sc-val">{fmtINR(s.entry_price)}</strong>
                <span className="sd-sc-sub">CMP {fmtINR(s.current_price)}</span>
              </div>
              <div className="sd-sc-arrow">→</div>
              <div className="sd-sc-price">
                <span className="sd-sc-lbl">Stop</span>
                <strong className="sd-sc-val sd-sc-val--sl">{fmtINR(s.stop_loss)}</strong>
                <span className="sd-sc-sub">−{Math.abs((s.entry_price - s.stop_loss) / s.entry_price * 100).toFixed(1)}%</span>
              </div>
              <div className="sd-sc-arrow">→</div>
              <div className="sd-sc-price">
                <span className="sd-sc-lbl">Target</span>
                <strong className="sd-sc-val sd-sc-val--tgt">{fmtINR(s.target_price)}</strong>
                <span className="sd-sc-sub">+{s.expected_return_pct.toFixed(1)}%</span>
              </div>
            </div>
            <div className="sd-sc-chips">
              <span className="sd-chip">R:R {s.risk_reward_ratio.toFixed(1)}×</span>
              <span className="sd-chip">{s.estimated_target_sessions} sessions</span>
              {s.historical_win_rate != null && (
                <span className="sd-chip" title={`Win rate of similarly scored setups across ${s.calibration_samples ?? 0} graded trades`}>
                  Historically won {Math.round(s.historical_win_rate * 100)}%
                </span>
              )}
              <span className="sd-chip">{fmtDate(String(s.estimated_target_date))}</span>
            </div>
            <div className="sd-sc-actions">
              {(() => {
                const held = journal.some((t) => t.status === "open" && t.symbol === s.symbol);
                const watched = watchlist.some((w) => w.symbol === s.symbol);
                const qty = positionQty(s.entry_price, s.stop_loss, settings);
                return (
                  <>
                    <button className="mini-btn mini-btn--primary" disabled={held}
                      onClick={() => { setJournal((prev) => [tradeFromSetup(s, qty), ...prev]); setNotice(`Logged ${qty} shares — confirm the fill in the Journal.`); }}>
                      {held ? "In journal" : `Log trade · ${qty} sh`}
                    </button>
                    <button className="mini-btn" disabled={watched}
                      onClick={() => { setWatchlist((prev) => [watchFromSetup(s), ...prev]); setNotice("Added to watchlist for 7 days."); }}>
                      {watched ? "Watching" : "Watch"}
                    </button>
                  </>
                );
              })()}
            </div>
            {notice && <p className="sd-notice">{notice}</p>}
          </div>
        )}
      </section>

      {/* ── Chart ── */}
      <section className="panel sd-chart-panel">
        <div className="panel-header">
          <div>
            <h2>Price chart</h2>
            <p>Last 90 sessions with entry, stop loss and target levels marked.</p>
          </div>
        </div>
        <PriceChart candles={detail.candles} signal={s} />
      </section>

      {s && (
        <>
          {/* ── Quality gates ── */}
          <section className="panel sd-panel">
            <h2 className="sd-ptitle">Quality gates</h2>
            {s.quality_flags && s.quality_flags.length > 0 ? (
              <div className="sd-flags">
                {s.quality_flags.map((f) => <div key={f} className="sd-flag">⚠ {f}</div>)}
              </div>
            ) : (
              <div className="sd-flags"><div className="sd-flag sd-flag--ok">✓ No blocking issues found by the overlay checks</div></div>
            )}
            <div className="sd-ind-grid">
              {s.price_levels && (
                <IndCard label="52-week high"
                  value={s.price_levels.price_discovery ? "New high" : `${s.price_levels.distance_from_52w_high_pct.toFixed(1)}% below`}
                  sub={`High ${fmtRupee(s.price_levels.high_52w)} · Low ${fmtRupee(s.price_levels.low_52w)}`}
                  ok={s.price_levels.near_52w_high} />
              )}
              {s.weekly_trend && (
                <IndCard label="Weekly alignment" value={`${s.weekly_trend.checks_passed}/3 checks`}
                  sub={[
                    s.weekly_trend.above_weekly_ema20 ? "✓ above 20W EMA" : "✗ below 20W EMA",
                    s.weekly_trend.weekly_rsi_above_50 ? `✓ RSI ${s.weekly_trend.weekly_rsi14.toFixed(0)}` : `✗ RSI ${s.weekly_trend.weekly_rsi14.toFixed(0)}`,
                    s.weekly_trend.weekly_volume_rising ? "✓ volume rising" : "✗ volume fading",
                  ].join(" · ")}
                  ok={s.weekly_trend.aligned} />
              )}
              {s.peer_rank && (
                <IndCard label={`Rank in ${s.peer_rank.sector}`} value={`#${s.peer_rank.rank} of ${s.peer_rank.peer_count}`}
                  sub={s.peer_rank.sector_leader ? "Sector leader ✓" : s.peer_rank.top_peers.length ? `Stronger: ${s.peer_rank.top_peers.join(", ")}` : "Mid-pack"}
                  ok={s.peer_rank.sector_leader ? true : s.peer_rank.sector_laggard ? false : undefined} />
              )}
              <IndCard label="Next results"
                value={s.event_risk.earnings_date ? fmtDate(String(s.event_risk.earnings_date)) : "Unknown"}
                sub={s.event_risk.blackout ? "Inside blackout — do not enter" : s.event_risk.days_to_earnings !== null ? `${s.event_risk.days_to_earnings} days away` : "Check the NSE corporate calendar"}
                ok={s.event_risk.blackout ? false : s.event_risk.earnings_date ? true : undefined} />
              <IndCard label="Ex-dividend"
                value={s.event_risk.ex_dividend_date ? fmtDate(String(s.event_risk.ex_dividend_date)) : "None scheduled"}
                sub={s.event_risk.days_to_ex_dividend != null ? `${s.event_risk.days_to_ex_dividend} days — price drops by the dividend` : "No upcoming ex-date found"} />
            </div>

            {s.fundamentals && s.fundamentals.checks_available > 0 && (
              <>
                <h3 className="sd-subtitle">
                  Fundamental quality · {s.fundamentals.quality_score}/{s.fundamentals.checks_available}
                  {s.fundamentals.passes === false && <span className="q-bad"> — fails the gate</span>}
                  {s.fundamentals.passes === true && <span className="q-ok"> — passes</span>}
                </h3>
                <div className="sd-ind-grid">
                  <IndCard label="Revenue growth" value={fmtPctOrDash(s.fundamentals.revenue_growth_pct)} sub="Target > 0%"
                    ok={s.fundamentals.revenue_growth_pct === null ? undefined : s.fundamentals.revenue_growth_pct > 0} />
                  <IndCard label="Profit margin" value={fmtPctOrDash(s.fundamentals.profit_margin_pct)} sub="Target > 8%"
                    ok={s.fundamentals.profit_margin_pct === null ? undefined : s.fundamentals.profit_margin_pct > 8} />
                  <IndCard label="Debt / equity" value={s.fundamentals.debt_to_equity === null ? "—" : `${s.fundamentals.debt_to_equity.toFixed(2)}×`} sub="Target < 1.5×"
                    ok={s.fundamentals.debt_to_equity === null ? undefined : s.fundamentals.debt_to_equity < 1.5} />
                  <IndCard label="ROE" value={fmtPctOrDash(s.fundamentals.return_on_equity_pct)} sub="Target > 12%"
                    ok={s.fundamentals.return_on_equity_pct === null ? undefined : s.fundamentals.return_on_equity_pct > 12} />
                  <IndCard label="Insider / promoter holding" value={fmtPctOrDash(s.fundamentals.insider_holding_pct)} sub="Pledge data not included" />
                  <IndCard label="Institutional holding" value={fmtPctOrDash(s.fundamentals.institutional_holding_pct)} sub="FII + DII + funds" />
                </div>
              </>
            )}
          </section>

          {/* ── Trade setup ── */}
          <section className="panel sd-panel">
            <h2 className="sd-ptitle">Trade setup</h2>
            <div className="sd-setup-grid">
              <SetupCard label="Entry" value={fmtINR(s.entry_price)} sub={`CMP ${fmtINR(s.current_price)}`} variant="entry" />
              <SetupCard label="Stop loss" value={fmtINR(s.stop_loss)}
                sub={`−${Math.abs((s.entry_price - s.stop_loss) / s.entry_price * 100).toFixed(1)}% below entry`} variant="sl" />
              <SetupCard label="Target" value={fmtINR(s.target_price)} sub={`+${s.expected_return_pct.toFixed(1)}% upside`} variant="tgt" />
              <SetupCard label="Risk : Reward" value={`${s.risk_reward_ratio.toFixed(1)}×`} sub="Minimum 2.5× for system trades" />
              <SetupCard label="Target by" value={fmtDate(String(s.estimated_target_date))} sub={`${s.estimated_target_sessions} trading sessions`} />
              <SetupCard label="Expected return" value={`+${s.expected_return_pct.toFixed(1)}%`} sub="At target price" variant="ret" />
            </div>
          </section>

          {/* ── Indicators ── */}
          <section className="panel sd-panel">
            <h2 className="sd-ptitle">Indicator snapshot</h2>
            <div className="sd-ind-grid">
              <IndCard label="EMA 20" value={fmtNum(s.indicators.ema20)}
                sub={s.current_price > s.indicators.ema20 ? "Price above ✓" : "Price below ✗"}
                ok={s.current_price > s.indicators.ema20} />
              <IndCard label="EMA 50" value={fmtNum(s.indicators.ema50)}
                sub={s.current_price > s.indicators.ema50 ? "Price above ✓" : "Price below ✗"}
                ok={s.current_price > s.indicators.ema50} />
              <IndCard label="EMA 200" value={fmtNum(s.indicators.ema200)}
                sub={s.current_price > s.indicators.ema200 ? "Price above ✓" : "Price below ✗"}
                ok={s.current_price > s.indicators.ema200} />
              <IndCard label="RSI 14" value={s.indicators.rsi14.toFixed(1)}
                sub={s.indicators.rsi14 >= 55 && s.indicators.rsi14 <= 75 ? "In target zone ✓" : "Outside 55–75"}
                ok={s.indicators.rsi14 >= 55 && s.indicators.rsi14 <= 75} />
              <IndCard label="ATR 14" value={fmtNum(s.indicators.atr14)} sub="Daily volatility range" />
              <IndCard label="Volume ratio" value={`${s.indicators.volume_ratio.toFixed(2)}×`}
                sub={s.indicators.volume_ratio >= 1.5 ? "Above 1.5× avg ✓" : "Below 1.5× avg"}
                ok={s.indicators.volume_ratio >= 1.5} />
            </div>

            {/* RSI gauge */}
            <div className="sd-rsi-gauge">
              <div className="sd-rsi-labels">
                <span>0</span>
                <span>Oversold (30)</span>
                <span className="sd-rsi-zone-lbl">Target zone 55–75</span>
                <span>Overbought (80)</span>
                <span>100</span>
              </div>
              <div className="sd-rsi-track">
                <div className="sd-rsi-zone" />
                <div className="sd-rsi-needle" style={{ left: `${Math.min(100, s.indicators.rsi14)}%` }} />
              </div>
              <p style={{ fontSize:"0.8rem", color:"var(--muted)", marginTop:8, textAlign:"center" }}>
                RSI {s.indicators.rsi14.toFixed(1)} — {s.indicators.rsi14 >= 55 && s.indicators.rsi14 <= 75 ? "✓ in the target zone (bullish momentum, not overbought)" : s.indicators.rsi14 > 75 ? "⚠ above target zone — approaching overbought" : "⚠ below target zone"}
              </p>
            </div>
          </section>

          {/* ── Relative strength ── */}
          <section className="panel sd-panel">
            <h2 className="sd-ptitle">Relative strength vs {s.relative_strength.benchmark_name}</h2>
            <div className="sd-rs-layout">
              <div className="sd-rs-score-box">
                <span className="sd-stat-lbl">RS Score</span>
                <strong className="sd-big-num">{Math.round(s.relative_strength.score * 100)}<span className="sd-big-of">/100</span></strong>
                <div className="sd-bar-track"><div className="sd-bar-fill" style={{ width: Math.round(s.relative_strength.score * 100) + "%" }} /></div>
                <span className="sd-stat-sub">{s.relative_strength.score >= 0.6 ? "✓ Outperforming" : "Underperforming"}</span>
              </div>
              <div className="sd-rs-periods">
                {[
                  { period:"20D", stock:s.relative_strength.stock_return_20d_pct, bench:s.relative_strength.benchmark_return_20d_pct, excess:s.relative_strength.excess_return_20d_pct },
                  { period:"50D", stock:s.relative_strength.stock_return_50d_pct, bench:s.relative_strength.benchmark_return_50d_pct, excess:s.relative_strength.excess_return_50d_pct },
                  { period:"120D",stock:s.relative_strength.stock_return_120d_pct,bench:s.relative_strength.benchmark_return_120d_pct,excess:s.relative_strength.excess_return_120d_pct },
                ].map(p => (
                  <div key={p.period} className="sd-rs-period">
                    <span className="sd-rs-period-lbl">{p.period}</span>
                    <table className="sd-rs-tbl">
                      <tbody>
                        <tr>
                          <td>Stock</td>
                          <td className={p.stock >= 0 ? "sd-green" : "sd-red"}><strong>{fmtSigned(p.stock)}%</strong></td>
                        </tr>
                        <tr>
                          <td>Nifty</td>
                          <td><strong>{fmtSigned(p.bench)}%</strong></td>
                        </tr>
                        <tr className="sd-rs-excess-row">
                          <td>Excess</td>
                          <td className={p.excess >= 0 ? "sd-green" : "sd-red"}><strong>{fmtSigned(p.excess)}%</strong></td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                ))}
              </div>
            </div>
          </section>

          {/* ── Liquidity + Accumulation ── */}
          <div className="sd-2col">
            <section className="panel sd-panel">
              <h2 className="sd-ptitle">Liquidity filter</h2>
              <div className="sd-liq-badge-row">
                <span className={`sd-badge ${s.liquidity.passes_filter ? "sd-badge--pass" : "sd-badge--warn"}`}>
                  {s.liquidity.passes_filter ? "✓ Passes filter" : "⚠ Watch"}
                </span>
                <span className="sd-stat-sub">Score: {Math.round(s.liquidity.score * 100)}/100</span>
              </div>
              <div className="sd-ind-grid" style={{ marginTop:16 }}>
                <IndCard label="20D avg traded value" value={`${s.liquidity.average_traded_value_20d_cr.toFixed(1)} Cr`} sub="Daily average" />
                <IndCard label="50D avg traded value" value={`${s.liquidity.average_traded_value_50d_cr.toFixed(1)} Cr`} sub="Daily average" />
              </div>
            </section>

            <section className="panel sd-panel">
              <h2 className="sd-ptitle">Accumulation quality</h2>
              <div className="sd-ind-grid">
                <IndCard label="Score" value={`${Math.round(s.accumulation.score * 100)}/100`}
                  sub={s.accumulation.score >= 0.55 ? "Strong ✓" : "Weak"} ok={s.accumulation.score >= 0.55} />
                <IndCard label="Up/down volume (10D)" value={s.accumulation.up_volume_ratio_10d.toFixed(2)}
                  sub={s.accumulation.up_volume_ratio_10d >= 1 ? "Buyers dominant ✓" : "Sellers dominant"} ok={s.accumulation.up_volume_ratio_10d >= 1} />
                <IndCard label="ATR contraction" value={s.accumulation.atr_contraction_ratio.toFixed(2)} sub="Volatility squeeze" />
                <IndCard label="High closes (10D)" value={String(s.accumulation.closes_near_high_10d)} sub="Strong close sessions" />
                {s.accumulation.average_delivery_pct_10d !== null && (
                  <IndCard label="Avg delivery (10D)" value={`${s.accumulation.average_delivery_pct_10d.toFixed(1)}%`}
                    sub={s.accumulation.average_delivery_pct_10d >= 40 ? "Good ✓" : "Low"} ok={s.accumulation.average_delivery_pct_10d >= 40} />
                )}
                {s.accumulation.latest_delivery_pct !== null && (
                  <IndCard label="Latest delivery" value={`${s.accumulation.latest_delivery_pct.toFixed(1)}%`} sub="Last session" />
                )}
              </div>
              <p className="sd-source-note">
                Source: {s.accumulation.source === "nse_delivery" ? "NSE delivery data" : "Volume proxy"}
              </p>
            </section>
          </div>

          {/* ── Sector + Event ── */}
          <div className="sd-2col">
            <section className="panel sd-panel">
              <h2 className="sd-ptitle">Sector leadership</h2>
              <div className="sd-sector-row">
                <div>
                  <span className="sd-stat-lbl">Rank in sector</span>
                  <strong className="sd-big-num">{s.sector_strength.rank}<span className="sd-big-of">/{s.sector_strength.sector_count}</span></strong>
                  <div className="sd-bar-track" style={{ marginTop:8 }}>
                    <div className="sd-bar-fill" style={{ width: `${(1 - (s.sector_strength.rank - 1) / Math.max(s.sector_strength.sector_count - 1, 1)) * 100}%` }} />
                  </div>
                </div>
              </div>
              <div className="sd-ind-grid" style={{ marginTop:16 }}>
                <IndCard label="Sector score" value={`${Math.round(s.sector_strength.score * 100)}/100`} sub="Composite" />
                <IndCard label="50D sector excess" value={`${fmtSigned(s.sector_strength.average_excess_return_50d_pct)}%`}
                  ok={s.sector_strength.average_excess_return_50d_pct >= 0} />
                <IndCard label="120D sector excess" value={`${fmtSigned(s.sector_strength.average_excess_return_120d_pct)}%`}
                  ok={s.sector_strength.average_excess_return_120d_pct >= 0} />
              </div>
            </section>

            <section className="panel sd-panel">
              <h2 className="sd-ptitle">Event risk</h2>
              <span className={`sd-badge sd-event--${s.event_risk.risk_level}`} style={{ marginBottom:16, display:"inline-block" }}>
                {capitalize(s.event_risk.risk_level)} risk
              </span>
              <div className="sd-ind-grid">
                <IndCard label="Days to earnings"
                  value={s.event_risk.days_to_earnings !== null ? String(s.event_risk.days_to_earnings) : "Unknown"}
                  sub={s.event_risk.days_to_earnings !== null && s.event_risk.days_to_earnings <= 7 ? "⚠ Results soon" : "Comfortable buffer"}
                  ok={s.event_risk.days_to_earnings === null || s.event_risk.days_to_earnings > 7} />
                <IndCard label="Earnings date"
                  value={s.event_risk.earnings_date ? fmtDate(String(s.event_risk.earnings_date)) : "Unknown"}
                  sub="Next results" />
                <IndCard label="Ranking penalty" value={s.event_risk.ranking_penalty.toFixed(2)} sub="Score deduction" />
              </div>
            </section>
          </div>

          {/* ── Backtest ── */}
          <section className="panel sd-panel">
            <h2 className="sd-ptitle">Backtest — {fmtPattern(s.pattern)}</h2>
            {s.backtest.total_trades > 0 ? (
              <>
                <div className="sd-bt-hero">
                  <div className="sd-bt-main">
                    <span className="sd-stat-lbl">Win rate</span>
                    <strong className="sd-big-num">{Math.round(s.backtest.win_rate * 100)}%</strong>
                    <div className="sd-bar-track"><div className="sd-bar-fill" style={{ width: Math.round(s.backtest.win_rate * 100) + "%" }} /></div>
                  </div>
                  <div className="sd-bt-main">
                    <span className="sd-stat-lbl">Target hit rate</span>
                    <strong className="sd-big-num">{Math.round(s.backtest.target_hit_rate * 100)}%</strong>
                    <div className="sd-bar-track"><div className="sd-bar-fill" style={{ width: Math.round(s.backtest.target_hit_rate * 100) + "%" }} /></div>
                  </div>
                  <div className="sd-bt-main">
                    <span className="sd-stat-lbl">Profit factor</span>
                    <strong className={`sd-big-num ${s.backtest.profit_factor >= 1.5 ? "sd-green" : "sd-red"}`}>
                      {s.backtest.profit_factor.toFixed(2)}
                    </strong>
                    <span className="sd-stat-sub">{s.backtest.profit_factor >= 1.5 ? "✓ Strong" : "Weak"}</span>
                  </div>
                </div>
                <div className="sd-ind-grid" style={{ marginTop:16 }}>
                  <IndCard label="Total trades" value={String(s.backtest.total_trades)} sub="Historical occurrences" />
                  <IndCard label="Avg return" value={`${fmtSigned(s.backtest.average_return_pct)}%`} ok={s.backtest.average_return_pct >= 0} />
                  <IndCard label="Max drawdown" value={`${s.backtest.max_drawdown_pct.toFixed(1)}%`} sub="Worst losing streak" ok={false} />
                  <IndCard label="Avg sessions held" value={s.backtest.average_holding_sessions.toFixed(1)} sub="Average trade duration" />
                  <IndCard label="Avg sessions to target"
                    value={s.backtest.average_target_sessions !== null ? s.backtest.average_target_sessions.toFixed(1) : "—"}
                    sub="When target was hit" />
                </div>
              </>
            ) : (
              <p className="muted">Insufficient historical data for this pattern. Trade sizing is based on current technical setup only.</p>
            )}
          </section>

          {/* ── Rationale ── */}
          <section className="panel sd-panel">
            <h2 className="sd-ptitle">Scanner rationale</h2>
            <p className="sd-rationale">{s.confidence_reason}</p>
          </section>
        </>
      )}

      {!s && (
        <section className="panel sd-panel">
          <h2 className="sd-ptitle">No active signal</h2>
          <p className="muted">This stock is in the universe but didn't rank into the current opportunity list. Run a fresh scan or check back after the next trading session.</p>
        </section>
      )}

      <style>{`
        /* ── Nav ── */
        .sd-nav { display:flex; align-items:center; justify-content:space-between; margin-bottom:24px; }
        .sd-back { color:var(--accent); font-weight:600; font-size:0.92rem; }
        .sd-refresh-btn { padding:8px 18px; font-size:0.88rem; }

        /* ── Loading ── */
        .sd-loading { display:flex; flex-direction:column; align-items:center; gap:16px; padding:80px 0; }
        .sd-spinner { width:36px; height:36px; border:3px solid var(--line); border-top-color:var(--accent); border-radius:50%; animation:sd-spin .7s linear infinite; }
        @keyframes sd-spin { to { transform:rotate(360deg); } }

        /* ── Hero ── */
        .sd-hero { display:grid; grid-template-columns:1fr auto; gap:24px; align-items:start; margin-bottom:24px; }
        .sd-company { font-family:var(--font-space-grotesk),sans-serif; font-size:clamp(1.5rem,3.5vw,2.4rem); margin:8px 0 12px; line-height:1.15; }
        .sd-symbol-chip { display:inline-block; background:var(--accent-soft); color:var(--accent); font-weight:700; font-size:0.9rem; padding:4px 14px; border-radius:999px; }

        /* ── Signal card ── */
        .sd-signal-card {
          background: linear-gradient(140deg,rgba(255,122,0,0.1),rgba(255,255,255,0.7)),var(--panel);
          border:1px solid rgba(255,122,0,0.25); border-radius:22px; padding:18px 22px;
          min-width:320px; max-width:420px; box-shadow:var(--shadow);
        }
        .sd-sc-top { display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; }
        .sd-pattern-badge { font-size:0.8rem; font-weight:600; background:var(--accent-soft); color:var(--accent); padding:3px 10px; border-radius:999px; }
        .sd-conf { font-size:0.9rem; font-weight:700; color:var(--accent); }
        .sd-sc-prices { display:grid; grid-template-columns:1fr auto 1fr auto 1fr; align-items:center; gap:6px; margin-bottom:14px; }
        .sd-sc-price { display:flex; flex-direction:column; min-width:0; }
        .sd-sc-lbl { font-size:0.72rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
        .sd-sc-val { font-size:1.1rem; font-weight:700; font-family:var(--font-space-grotesk),sans-serif; white-space:nowrap; }
        .sd-sc-val--sl  { color:var(--red); }
        .sd-sc-val--tgt { color:var(--green); }
        .sd-sc-sub { font-size:0.72rem; color:var(--muted); }
        .sd-sc-arrow { color:var(--muted); font-size:1rem; text-align:center; padding-top:12px; }
        .sd-sc-chips { display:flex; flex-wrap:wrap; gap:6px; }
        .sd-sc-actions { display:flex; gap:8px; margin-top:14px; }
        .sd-notice { font-size:0.8rem; color:var(--accent); margin:8px 0 0; }
        .sd-flags { display:flex; flex-direction:column; gap:6px; margin-bottom:14px; }
        .sd-flag { font-size:0.88rem; font-weight:600; color:var(--red); background:rgba(185,75,81,0.07); border:1px solid rgba(185,75,81,0.18); border-radius:12px; padding:8px 12px; }
        .sd-flag--ok { color:var(--green); background:rgba(22,108,90,0.07); border-color:rgba(22,108,90,0.18); }
        .sd-subtitle { font-size:0.95rem; margin:20px 0 12px; font-family:var(--font-space-grotesk),sans-serif; }
        .sd-chip { font-size:0.78rem; font-weight:600; padding:4px 10px; border-radius:999px; background:rgba(255,255,255,0.8); border:1px solid var(--line); }

        /* ── Panels ── */
        .sd-chart-panel { margin-bottom:20px; }
        .sd-panel { margin-bottom:20px; }
        .sd-ptitle { font-size:1.1rem; font-family:var(--font-space-grotesk),sans-serif; margin:0 0 18px; }
        .sd-2col { display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-bottom:20px; }

        /* ── Trade setup grid ── */
        .sd-setup-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
        .sd-setup-card {
          background:rgba(255,255,255,0.62); border:1px solid var(--line);
          border-radius:16px; padding:14px 16px; display:flex; flex-direction:column;
          gap:4px; min-width:0; overflow:hidden;
        }
        .sd-setup-card--entry { border-color:rgba(54,89,162,0.25); background:rgba(54,89,162,0.05); }
        .sd-setup-card--sl    { border-color:rgba(185,75,81,0.25); background:rgba(185,75,81,0.05); }
        .sd-setup-card--tgt   { border-color:rgba(22,108,90,0.25); background:rgba(22,108,90,0.05); }
        .sd-setup-card--ret   { border-color:rgba(22,108,90,0.2); background:rgba(22,108,90,0.04); }
        .sd-setup-lbl { font-size:0.74rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); font-weight:500; }
        .sd-setup-val {
          font-size:clamp(1.1rem,2.2vw,1.5rem); font-family:var(--font-space-grotesk),sans-serif;
          font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
        }
        .sd-setup-card--entry .sd-setup-val { color:var(--blue); }
        .sd-setup-card--sl    .sd-setup-val { color:var(--red); }
        .sd-setup-card--tgt   .sd-setup-val { color:var(--green); }
        .sd-setup-card--ret   .sd-setup-val { color:var(--green); }
        .sd-setup-sub { font-size:0.76rem; color:var(--muted); }

        /* ── Indicator grid ── */
        .sd-ind-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
        .sd-ind-card {
          background:rgba(255,255,255,0.58); border:1px solid var(--line);
          border-radius:14px; padding:12px 14px; min-width:0; overflow:hidden;
        }
        .sd-ind-lbl { display:block; font-size:0.72rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin-bottom:5px; }
        .sd-ind-val {
          display:block; font-size:clamp(0.95rem,1.8vw,1.15rem);
          font-weight:700; font-family:var(--font-space-grotesk),sans-serif;
          white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
        }
        .sd-ind-sub { display:block; font-size:0.74rem; margin-top:3px; }
        .sd-ok   { color:var(--green); }
        .sd-warn { color:var(--red); }
        .sd-muted{ color:var(--muted); }

        /* ── RSI gauge ── */
        .sd-rsi-gauge { margin-top:20px; padding:14px 0 0; border-top:1px solid var(--line); }
        .sd-rsi-labels { display:flex; justify-content:space-between; font-size:0.72rem; color:var(--muted); margin-bottom:6px; }
        .sd-rsi-zone-lbl { color:var(--green); font-weight:600; }
        .sd-rsi-track { position:relative; height:10px; background:var(--bg-deep); border-radius:5px; overflow:visible; }
        .sd-rsi-zone  { position:absolute; left:55%; width:20%; height:100%; background:rgba(22,108,90,0.2); border-radius:5px; }
        .sd-rsi-needle{ position:absolute; top:-5px; width:20px; height:20px; background:var(--accent); border-radius:50%; transform:translateX(-50%); border:3px solid white; box-shadow:0 2px 8px rgba(255,122,0,.35); }

        /* ── Relative strength ── */
        .sd-rs-layout { display:grid; grid-template-columns:180px 1fr; gap:28px; align-items:start; }
        .sd-rs-score-box { display:flex; flex-direction:column; gap:4px; }
        .sd-big-num { font-size:clamp(2rem,4vw,3rem); font-family:var(--font-space-grotesk),sans-serif; font-weight:700; line-height:1; }
        .sd-big-of  { font-size:1.2rem; color:var(--muted); font-weight:400; }
        .sd-bar-track { height:7px; background:var(--bg-deep); border-radius:4px; overflow:hidden; margin-top:6px; }
        .sd-bar-fill  { height:100%; background:linear-gradient(90deg,#f16800,#ff9a2f); border-radius:4px; transition:width .5s ease; }
        .sd-rs-periods { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
        .sd-rs-period  { background:rgba(255,255,255,0.58); border:1px solid var(--line); border-radius:14px; padding:12px 14px; }
        .sd-rs-period-lbl { display:block; font-size:0.74rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); font-weight:600; margin-bottom:8px; }
        .sd-rs-tbl { width:100%; border-collapse:collapse; font-size:0.86rem; }
        .sd-rs-tbl td { padding:4px 0; border-bottom:1px solid var(--line); }
        .sd-rs-tbl tr:last-child td { border-bottom:none; font-weight:600; }
        .sd-rs-tbl td:last-child { text-align:right; }
        .sd-rs-excess-row td { padding-top:6px; }

        /* ── Liquidity ── */
        .sd-liq-badge-row { display:flex; align-items:center; gap:12px; }

        /* ── Badges ── */
        .sd-badge { display:inline-block; font-size:0.85rem; font-weight:600; padding:5px 14px; border-radius:999px; }
        .sd-badge--pass { background:rgba(22,108,90,0.1); color:var(--green); }
        .sd-badge--warn { background:rgba(180,120,0,0.1); color:#8a5c00; }
        .sd-event--low     { background:rgba(22,108,90,0.1); color:var(--green); }
        .sd-event--medium  { background:rgba(180,120,0,0.1); color:#8a5c00; }
        .sd-event--high    { background:rgba(185,75,81,0.1); color:var(--red); }
        .sd-event--unknown { background:var(--bg-deep); color:var(--muted); }

        /* ── Sector ── */
        .sd-sector-row { padding-bottom:4px; }

        /* ── Backtest hero ── */
        .sd-bt-hero { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }
        .sd-bt-main { background:rgba(255,122,0,0.05); border:1px solid rgba(255,122,0,0.18); border-radius:16px; padding:16px 18px; display:flex; flex-direction:column; gap:4px; }

        /* ── Source note ── */
        .sd-source-note { font-size:0.76rem; color:var(--muted); margin:10px 0 0; }

        /* ── Rationale ── */
        .sd-rationale { color:var(--muted); line-height:1.75; font-size:0.92rem; max-width:80ch; }

        /* ── Shared helpers ── */
        .sd-stat-lbl { display:block; font-size:0.74rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); font-weight:500; margin-bottom:4px; }
        .sd-stat-sub { font-size:0.78rem; color:var(--muted); margin-top:3px; }
        .sd-green { color:var(--green); }
        .sd-red   { color:var(--red); }

        @media (max-width:960px) {
          .sd-hero        { grid-template-columns:1fr; }
          .sd-signal-card { min-width:unset; max-width:100%; }
          .sd-2col        { grid-template-columns:1fr; }
          .sd-setup-grid  { grid-template-columns:1fr 1fr; }
          .sd-ind-grid    { grid-template-columns:1fr 1fr; }
          .sd-rs-layout   { grid-template-columns:1fr; }
          .sd-rs-periods  { grid-template-columns:1fr; }
          .sd-bt-hero     { grid-template-columns:1fr 1fr; }
          .sd-sc-prices   { grid-template-columns:1fr auto 1fr auto 1fr; }
        }
        @media (max-width:600px) {
          .sd-setup-grid  { grid-template-columns:1fr; }
          .sd-ind-grid    { grid-template-columns:1fr 1fr; }
          .sd-bt-hero     { grid-template-columns:1fr; }
        }
      `}</style>
    </main>
  );
}

/* ── Sub-components ── */

function SetupCard({ label, value, sub, variant }: { label:string; value:string; sub?:string; variant?:string }) {
  return (
    <div className={`sd-setup-card${variant ? ` sd-setup-card--${variant}` : ""}`}>
      <span className="sd-setup-lbl">{label}</span>
      <strong className="sd-setup-val">{value}</strong>
      {sub && <span className="sd-setup-sub">{sub}</span>}
    </div>
  );
}

function IndCard({ label, value, sub, ok }: { label:string; value:string; sub?:string; ok?:boolean }) {
  const valClass = ok === true ? "sd-ok" : ok === false ? "sd-warn" : "sd-muted";
  const subClass = ok === true ? "sd-ok" : ok === false ? "sd-warn" : "sd-muted";
  return (
    <div className="sd-ind-card">
      <span className="sd-ind-lbl">{label}</span>
      <strong className={`sd-ind-val ${valClass}`}>{value}</strong>
      {sub && <span className={`sd-ind-sub ${subClass}`}>{sub}</span>}
    </div>
  );
}

/* ── Helpers ── */

function fmtINR(n: number) {
  return new Intl.NumberFormat("en-IN", { style:"currency", currency:"INR", maximumFractionDigits:0 }).format(n);
}
function fmtNum(n: number) {
  return n.toFixed(2);
}
function fmtDate(s: string) {
  return new Intl.DateTimeFormat("en-IN", { day:"2-digit", month:"short", year:"numeric" }).format(new Date(s));
}
function fmtPctOrDash(n: number | null) {
  return n === null ? "—" : `${n.toFixed(1)}%`;
}
function fmtSigned(n: number) {
  return (n > 0 ? "+" : "") + n.toFixed(1);
}
function fmtPattern(p: string) {
  return p.split("_").map(w => w[0]?.toUpperCase() + w.slice(1)).join(" ");
}
function capitalize(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
