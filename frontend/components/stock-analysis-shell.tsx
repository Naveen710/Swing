"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { analyzeStock } from "../lib/api";
import { exportWorkbook } from "../lib/excel";
import {
  JournalTrade,
  WATCH_DAYS,
  fmtINR,
  fmtPattern,
  positionQty,
  todayISO,
  uid,
  useJournal,
  useTradingSettings,
  useWatchlist,
} from "../lib/store";
import { AnalysisSection, StockAnalysis } from "../types";
import { AppNav } from "./app-nav";
import { PriceChart } from "./price-chart";

const GRADE_TONE: Record<string, string> = { A: "a", B: "b", C: "c", D: "d", E: "e" };

export function StockAnalysisShell({ symbol }: { symbol: string }) {
  const [data, setData] = useState<StockAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [journal, setJournal] = useJournal();
  const [watchlist, setWatchlist] = useWatchlist();
  const [settings] = useTradingSettings();
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    analyzeStock(symbol)
      .then((d) => alive && setData(d))
      .catch(() => alive && setError(`Couldn't analyse ${symbol.toUpperCase()}. Check the NSE symbol and try again.`))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [symbol]);

  if (loading) {
    return (
      <main className="page-shell">
        <AppNav />
        <div className="an-loading"><div className="an-spinner" /><p className="muted">Analysing {symbol.toUpperCase()} — fetching prices and financials…</p></div>
        <AnalysisStyles />
      </main>
    );
  }
  if (error || !data) {
    return (
      <main className="page-shell">
        <AppNav />
        <section className="panel"><p className="error-text">{error ?? "Analysis unavailable."}</p></section>
        <AnalysisStyles />
      </main>
    );
  }

  const d = data;
  const tone = GRADE_TONE[d.overall_grade] ?? "c";
  const base = d.symbol.replace(/\.NS$/, "");
  const setup = d.setup;
  const held = journal.some((t) => t.status === "open" && t.symbol === d.symbol);
  const watched = watchlist.some((w) => w.symbol === d.symbol);

  function watch() {
    if (!setup) return;
    const now = new Date();
    setWatchlist((prev) => [{
      id: uid(), symbol: d.symbol, company_name: d.company_name, sector: d.sector, pattern: setup.pattern,
      trigger_price: setup.entry, stop_loss: setup.stop, target_price: setup.target,
      added_at: now.toISOString(), expires_at: new Date(now.getTime() + WATCH_DAYS * 86400000).toISOString(), last_close: d.price,
    }, ...prev]);
    setNotice(`Added to watchlist for ${WATCH_DAYS} days.`);
  }
  function logTrade() {
    if (!setup) return;
    const qty = positionQty(setup.entry, setup.stop, settings);
    const trade: JournalTrade = {
      id: uid(), symbol: d.symbol, company_name: d.company_name, sector: d.sector, pattern: setup.pattern, status: "open",
      entry_date: todayISO(), planned_entry: setup.entry, entry_price: setup.entry, qty, stop_loss: setup.stop,
      target_price: setup.target, backtest_win_rate: null, probability_score: null,
    };
    setJournal((prev) => [trade, ...prev]);
    setNotice(`Logged ${qty} shares — confirm the fill price in the Journal.`);
  }

  function exportExcel() {
    const rows = (section: AnalysisSection) => section.categories.flatMap((c) =>
      c.factors.map((f) => ({ Category: c.name, "Category score": c.score, Factor: f.name, Value: f.value, Assessment: f.status, Note: f.note || null })));
    void exportWorkbook(`analysis-${base}`, [
      {
        name: "Summary",
        rows: [
          { Item: "Company", Value: d.company_name }, { Item: "Symbol", Value: base }, { Item: "Sector", Value: d.sector },
          { Item: "Price (₹)", Value: d.price }, { Item: "As of", Value: d.as_of },
          { Item: "Overall rating", Value: `${d.overall_grade} (${d.overall_label})` }, { Item: "Overall score", Value: d.overall_score },
          { Item: "Technical score", Value: d.technical.score }, { Item: "Fundamental score", Value: d.fundamental.score },
          ...d.strengths.map((s) => ({ Item: "Strength", Value: s })), ...d.concerns.map((s) => ({ Item: "Concern", Value: s })),
        ],
      },
      { name: "Technical", rows: rows(d.technical) },
      { name: "Fundamental", rows: rows(d.fundamental) },
    ]);
  }

  return (
    <main className="page-shell">
      <AppNav />

      <section className="an-hero">
        <div>
          <span className="eyebrow">{d.sector}{d.industry ? ` · ${d.industry}` : ""}</span>
          <h1 className="an-company">{d.company_name}</h1>
          <div className="an-meta">
            <span className="an-symbol">{base}</span>
            <span className="an-price">{fmtINR(d.price)}</span>
            <span className={d.change_pct >= 0 ? "q-ok" : "q-bad"}>{d.change_pct >= 0 ? "+" : ""}{d.change_pct.toFixed(2)}%</span>
            <span className="muted">as of {new Date(d.as_of).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}</span>
            {d.market_cap_cr && <span className="muted">Mcap ₹{Math.round(d.market_cap_cr).toLocaleString("en-IN")} Cr</span>}
          </div>
          <p className="an-summary">{d.summary}</p>
          <div className="an-actions">
            <button className="mini-btn" onClick={exportExcel}>Export to Excel</button>
            {d.in_scan_universe && <Link className="mini-btn" href={`/stocks/${d.symbol}`}>Scanner view</Link>}
          </div>
        </div>

        <div className={`an-rating an-rating--${tone}`}>
          <div className="an-grade">{d.overall_grade}</div>
          <div className="an-rating-label">{d.overall_label}</div>
          <div className="an-rating-score">{d.overall_score.toFixed(0)}<span>/100</span></div>
          <ScoreBar label="Technical" section={d.technical} />
          <ScoreBar label="Fundamental" section={d.fundamental} />
          <p className="an-rating-note">Rules-based score of current conditions — not a price forecast or investment advice.</p>
        </div>
      </section>

      {d.data_notes.length > 0 && <div className="an-notes">{d.data_notes.map((n) => <p key={n}>ℹ {n}</p>)}</div>}

      <div className="an-2col">
        <section className="panel an-list an-list--ok">
          <h2>Strengths</h2>
          {d.strengths.length ? <ul>{d.strengths.map((s) => <li key={s}>{s}</li>)}</ul> : <p className="muted">No standout strengths.</p>}
        </section>
        <section className="panel an-list an-list--bad">
          <h2>Concerns</h2>
          {d.concerns.length ? <ul>{d.concerns.map((s) => <li key={s}>{s}</li>)}</ul> : <p className="muted">No major red flags in the data checked.</p>}
        </section>
      </div>

      <section className="panel an-panel">
        <h2>Price — last 120 sessions</h2>
        <PriceChart candles={d.candles} signal={null} />
        <div className="an-levels">
          <Level label="20-day support / resistance" value={`${fmtINR(d.levels.support_20d)} – ${fmtINR(d.levels.resistance_20d)}`} />
          <Level label="60-day support / resistance" value={`${fmtINR(d.levels.support_60d)} – ${fmtINR(d.levels.resistance_60d)}`} />
          <Level label="52-week range" value={`${fmtINR(d.levels.low_52w)} – ${fmtINR(d.levels.high_52w)}`} />
          <Level label="Daily volatility (ATR)" value={`${d.levels.atr_pct.toFixed(1)}% a day`} />
        </div>
      </section>

      <div className="an-2col">
        <SectionPanel title="Technical analysis" section={d.technical} />
        <SectionPanel title="Fundamental analysis" section={d.fundamental} />
      </div>

      <section className="panel an-panel">
        <h2>Swing setup</h2>
        {setup ? (
          <>
            <p className="an-setup-head">
              <span className="pill-tag pill-tag--ok">{fmtPattern(setup.pattern)}</span>
              {!setup.tradeable && <span className="pill-tag pill-tag--warn">Stop too wide to trade (over 8%)</span>}
            </p>
            <p className="muted">{setup.explanation}</p>
            <div className="an-levels">
              <Level label="Entry (buy-stop)" value={fmtINR(setup.entry)} />
              <Level label="Stop loss" value={`${fmtINR(setup.stop)} (−${((1 - setup.stop / setup.entry) * 100).toFixed(1)}%)`} tone="bad" />
              <Level label="Target" value={`${fmtINR(setup.target)} (+${((setup.target / setup.entry - 1) * 100).toFixed(1)}%)`} tone="ok" />
              <Level label="Risk : reward" value={`${setup.risk_reward.toFixed(1)}×`} />
            </div>
            <div className="an-actions">
              <button className="mini-btn mini-btn--primary" onClick={logTrade} disabled={held || !setup.tradeable}>{held ? "In journal" : "Log trade"}</button>
              <button className="mini-btn" onClick={watch} disabled={watched}>{watched ? "Watching" : "Watch"}</button>
              {notice && <span className="an-notice">{notice}</span>}
            </div>
          </>
        ) : (
          <p className="muted">No swing pattern is active right now. A good company can still be a poor entry — wait for a setup.</p>
        )}
      </section>

      {d.description && (
        <section className="panel an-panel">
          <h2>About the company</h2>
          <p className="an-about">{d.description}</p>
        </section>
      )}

      <AnalysisStyles />
    </main>
  );
}

function ScoreBar({ label, section }: { label: string; section: AnalysisSection }) {
  return (
    <div className="an-bar">
      <div className="an-bar-head">
        <span>{label}</span>
        <b>{section.available && section.score !== null ? `${section.score.toFixed(0)} · ${section.grade}` : "n/a"}</b>
      </div>
      <div className="an-bar-track"><div className={`an-bar-fill an-bar-fill--${GRADE_TONE[section.grade ?? ""] ?? "na"}`} style={{ width: `${section.score ?? 0}%` }} /></div>
    </div>
  );
}

function SectionPanel({ title, section }: { title: string; section: AnalysisSection }) {
  return (
    <section className="panel an-panel">
      <div className="an-sec-head">
        <h2>{title}</h2>
        {section.available && section.score !== null
          ? <span className={`an-chip an-chip--${GRADE_TONE[section.grade ?? ""]}`}>{section.grade} · {section.label} · {section.score.toFixed(0)}</span>
          : <span className="an-chip">Not rated</span>}
      </div>
      {section.note && <p className="muted an-sec-note">{section.note}</p>}
      {section.categories.map((c) => (
        <div key={c.name} className="an-cat">
          <div className="an-cat-head">
            <span>{c.name} <small>({c.weight}%)</small></span>
            <b>{c.score === null ? "—" : c.score.toFixed(0)}</b>
          </div>
          <div className="an-bar-track an-bar-track--thin"><div className="an-bar-fill" style={{ width: `${c.score ?? 0}%` }} /></div>
          <table className="an-factors">
            <tbody>
              {c.factors.map((f) => (
                <tr key={f.name} title={f.note || undefined}>
                  <td>{f.name}{f.note && <div className="an-factor-note">{f.note}</div>}</td>
                  <td className={`an-f an-f--${f.status}`}>{f.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </section>
  );
}

function Level({ label, value, tone }: { label: string; value: string; tone?: "ok" | "bad" }) {
  return (
    <div className="an-level">
      <span>{label}</span>
      <strong className={tone === "ok" ? "q-ok" : tone === "bad" ? "q-bad" : ""}>{value}</strong>
    </div>
  );
}

function AnalysisStyles() {
  return (
    <style>{`
      .an-loading { display:flex; flex-direction:column; align-items:center; gap:14px; padding:80px 0; }
      .an-spinner { width:34px; height:34px; border:3px solid var(--line); border-top-color:var(--accent); border-radius:50%; animation:an-spin .7s linear infinite; }
      @keyframes an-spin { to { transform:rotate(360deg); } }
      @media (prefers-reduced-motion: reduce) { .an-spinner { animation:none; } }
      .an-hero { display:grid; grid-template-columns:1fr 300px; gap:24px; align-items:start; margin-bottom:20px; }
      .an-company { font-family:var(--font-space-grotesk),sans-serif; font-size:clamp(1.6rem,3.6vw,2.5rem); line-height:1.1; margin:8px 0 10px; }
      .an-meta { display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 14px; font-size:0.92rem; }
      .an-symbol { background:var(--accent-soft); color:var(--accent); font-weight:700; padding:2px 12px; border-radius:999px; }
      .an-price { font-family:var(--font-space-grotesk),sans-serif; font-size:1.35rem; font-weight:700; }
      .an-summary { margin:14px 0 12px; line-height:1.6; max-width:70ch; }
      .an-actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:10px; }
      .an-notice { font-size:0.82rem; color:var(--accent); }

      .an-rating { border-radius:24px; padding:20px; text-align:center; border:1px solid var(--line); background:var(--panel); box-shadow:var(--shadow); }
      .an-grade { width:84px; height:84px; margin:0 auto 6px; border-radius:50%; display:grid; place-items:center; font-family:var(--font-space-grotesk),sans-serif; font-size:2.6rem; font-weight:700; color:#fff; }
      .an-rating--a .an-grade { background:#166c5a; } .an-rating--b .an-grade { background:#3f8f5f; }
      .an-rating--c .an-grade { background:#c08a12; } .an-rating--d .an-grade { background:#d4682c; } .an-rating--e .an-grade { background:#b94b51; }
      .an-rating-label { font-weight:700; font-size:1.05rem; }
      .an-rating-score { font-family:var(--font-space-grotesk),sans-serif; font-size:1.6rem; font-weight:700; margin:2px 0 14px; }
      .an-rating-score span { font-size:0.9rem; color:var(--muted); font-weight:400; }
      .an-rating-note { font-size:0.72rem; color:var(--muted); margin:12px 0 0; line-height:1.4; }
      .an-bar { text-align:left; margin-top:10px; }
      .an-bar-head { display:flex; justify-content:space-between; font-size:0.82rem; margin-bottom:4px; }
      .an-bar-track { height:8px; background:var(--bg-deep); border-radius:4px; overflow:hidden; }
      .an-bar-track--thin { height:5px; margin:4px 0 6px; }
      .an-bar-fill { height:100%; border-radius:4px; background:linear-gradient(90deg,#f16800,#ff9a2f); }
      .an-bar-fill--a, .an-bar-fill--b { background:#2f8a64; } .an-bar-fill--c { background:#c08a12; }
      .an-bar-fill--d { background:#d4682c; } .an-bar-fill--e { background:#b94b51; }

      .an-notes { background:var(--accent-soft); border-radius:14px; padding:8px 14px; margin-bottom:18px; font-size:0.86rem; }
      .an-notes p { margin:4px 0; }
      .an-2col { display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-bottom:20px; }
      .an-panel { margin-bottom:20px; }
      .an-2col .an-panel { margin-bottom:0; }
      .an-panel h2, .an-list h2 { font-size:1.12rem; margin:0 0 12px; }
      .an-list ul { margin:0; padding-left:20px; line-height:1.6; font-size:0.92rem; }
      .an-list--ok { border-top:3px solid #2f8a64; } .an-list--bad { border-top:3px solid #b94b51; }

      .an-levels { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-top:14px; }
      .an-level { background:rgba(255,255,255,0.6); border:1px solid var(--line); border-radius:14px; padding:10px 12px; min-width:0; }
      .an-level span { display:block; font-size:0.74rem; color:var(--muted); margin-bottom:3px; }
      .an-level strong { font-size:0.95rem; }

      .an-sec-head { display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:6px; }
      .an-sec-head h2 { margin:0; }
      .an-sec-note { font-size:0.84rem; margin:4px 0 10px; }
      .an-chip { font-size:0.8rem; font-weight:700; padding:4px 12px; border-radius:999px; background:var(--bg-deep); color:var(--muted); white-space:nowrap; }
      .an-chip--a, .an-chip--b { background:rgba(22,108,90,0.12); color:var(--green); }
      .an-chip--c { background:rgba(192,138,18,0.14); color:#8a5c00; }
      .an-chip--d, .an-chip--e { background:rgba(185,75,81,0.12); color:var(--red); }
      .an-cat { padding:12px 0; border-top:1px solid var(--line); }
      .an-cat-head { display:flex; justify-content:space-between; font-weight:600; font-size:0.92rem; }
      .an-cat-head small { color:var(--muted); font-weight:400; }
      .an-factors { width:100%; border-collapse:collapse; font-size:0.86rem; }
      .an-factors td { padding:4px 0; vertical-align:top; border:none; }
      .an-factors td:last-child { text-align:right; font-weight:600; white-space:nowrap; padding-left:12px; }
      .an-factor-note { font-size:0.74rem; color:var(--muted); }
      .an-f--good { color:var(--green); } .an-f--bad { color:var(--red); } .an-f--neutral { color:#8a5c00; } .an-f--na { color:var(--text); }
      .an-setup-head { display:flex; gap:8px; margin:0 0 6px; }
      .an-about { line-height:1.7; color:var(--muted); max-width:85ch; margin:0; }

      @media (max-width:960px) {
        .an-hero, .an-2col { grid-template-columns:1fr; }
        .an-levels { grid-template-columns:1fr 1fr; }
      }
    `}</style>
  );
}
