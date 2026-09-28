"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { getStockDetail } from "../lib/api";
import {
  JournalTrade,
  WATCH_DAYS,
  WatchItem,
  fmtINR,
  fmtPattern,
  positionQty,
  todayISO,
  uid,
  useJournal,
  useTradingSettings,
  useWatchlist,
} from "../lib/store";
import { exportWorkbook, watchlistRows } from "../lib/excel";
import { AppNav } from "./app-nav";

type Status = "triggered" | "touched" | "waiting" | "invalidated" | "expired" | "unchecked";

function statusOf(w: WatchItem): Status {
  if (w.last_close !== undefined && w.last_close < w.stop_loss) return "invalidated";
  if (w.last_close !== undefined && w.last_close >= w.trigger_price) return "triggered";
  if (w.last_high !== undefined && w.last_high >= w.trigger_price) return "touched";
  if (Date.now() > new Date(w.expires_at).getTime()) return "expired";
  if (!w.last_checked) return "unchecked";
  return "waiting";
}

const STATUS_UI: Record<Status, { label: string; cls: string; order: number }> = {
  triggered: { label: "Triggered — closed above entry", cls: "pill-tag--ok", order: 0 },
  touched: { label: "Touched entry intraday", cls: "pill-tag--warn", order: 1 },
  waiting: { label: "Waiting", cls: "pill-tag--muted", order: 2 },
  unchecked: { label: "Not checked yet", cls: "pill-tag--muted", order: 3 },
  invalidated: { label: "Invalidated — closed below stop", cls: "pill-tag--bad", order: 4 },
  expired: { label: "Expired", cls: "pill-tag--muted", order: 5 },
};

export function WatchlistShell() {
  const [items, setItems, ready] = useWatchlist();
  const [journal, setJournal] = useJournal();
  const [settings] = useTradingSettings();
  const [checking, setChecking] = useState(false);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">("default");
  const autoChecked = useRef(false);

  useEffect(() => {
    setPermission(typeof Notification === "undefined" ? "unsupported" : Notification.permission);
  }, []);

  useEffect(() => {
    if (ready && items.length && !autoChecked.current) {
      autoChecked.current = true;
      void checkAll();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  async function checkAll() {
    const targets = items.filter((w) => Date.now() <= new Date(w.expires_at).getTime() || statusOf(w) === "triggered");
    if (!targets.length) return;
    setChecking(true);
    setProgress(0);
    const updates = new Map<string, Partial<WatchItem>>();
    let done = 0;
    const queue = [...targets];
    const worker = async () => {
      while (queue.length) {
        const w = queue.shift()!;
        try {
          const detail = await getStockDetail(w.symbol);
          const last = detail.candles[detail.candles.length - 1];
          if (last) updates.set(w.id, { last_close: last.close, last_high: last.high, last_checked: new Date().toISOString() });
        } catch { /* leave unchanged */ }
        done += 1;
        setProgress(done / targets.length);
      }
    };
    await Promise.all([worker(), worker(), worker()]);

    const newlyTriggered: WatchItem[] = [];
    setItems((prev) => prev.map((w) => {
      const patch = updates.get(w.id);
      if (!patch) return w;
      const next = { ...w, ...patch };
      if (statusOf(next) === "triggered" && !w.notified) {
        newlyTriggered.push(next);
        next.notified = true;
      }
      return next;
    }));
    setChecking(false);

    if (newlyTriggered.length) {
      const names = newlyTriggered.map((w) => w.symbol).join(", ");
      setMessage(`Entry triggered: ${names}`);
      if (typeof Notification !== "undefined" && Notification.permission === "granted") {
        newlyTriggered.forEach((w) => new Notification(`${w.symbol} triggered`, {
          body: `Closed at ${fmtINR(w.last_close ?? 0)} above entry ${fmtINR(w.trigger_price)}. Stop ${fmtINR(w.stop_loss)}.`,
        }));
      }
    } else {
      setMessage(`Checked ${updates.size} stock${updates.size === 1 ? "" : "s"} — no new triggers.`);
    }
  }

  async function enableAlerts() {
    if (typeof Notification === "undefined") return;
    setPermission(await Notification.requestPermission());
  }

  function remove(id: string) {
    setItems((prev) => prev.filter((w) => w.id !== id));
  }
  function extend(w: WatchItem) {
    const base = Math.max(Date.now(), new Date(w.expires_at).getTime());
    setItems((prev) => prev.map((x) => x.id === w.id ? { ...x, expires_at: new Date(base + WATCH_DAYS * 86400000).toISOString() } : x));
  }
  function logTrade(w: WatchItem) {
    if (journal.some((t) => t.status === "open" && t.symbol === w.symbol)) {
      setMessage(`${w.symbol} is already an open position.`);
      return;
    }
    const entry = w.last_close && w.last_close >= w.trigger_price ? w.last_close : w.trigger_price;
    const trade: JournalTrade = {
      id: uid(), symbol: w.symbol, company_name: w.company_name, sector: w.sector, pattern: w.pattern,
      status: "open", entry_date: todayISO(), planned_entry: w.trigger_price, entry_price: entry,
      qty: positionQty(entry, w.stop_loss, settings), stop_loss: w.stop_loss, target_price: w.target_price,
      backtest_win_rate: null, probability_score: null,
    };
    setJournal((prev) => [trade, ...prev]);
    remove(w.id);
    setMessage(`${w.symbol} moved to the Journal — confirm the fill price there.`);
  }

  const sorted = [...items].sort((a, b) => STATUS_UI[statusOf(a)].order - STATUS_UI[statusOf(b)].order);
  const expired = items.filter((w) => statusOf(w) === "expired" || statusOf(w) === "invalidated");

  return (
    <main className="page-shell">
      <AppNav />
      <section className="wl-head">
        <div>
          <p className="eyebrow">Watchlist</p>
          <h1 className="wl-title">Setups waiting for their trigger.</h1>
          <p className="muted">
            Stocks you marked with <strong>Watch</strong> stay here for {WATCH_DAYS} days. Each check compares the latest
            daily close to the entry trigger and the stop.
          </p>
        </div>
        <div className="wl-actions">
          <button className="primary-button" onClick={() => void checkAll()} disabled={checking || !items.length}>
            {checking ? `Checking… ${Math.round(progress * 100)}%` : "Check triggers now"}
          </button>
          <button className="secondary-button" disabled={!items.length}
            onClick={() => void exportWorkbook("swing-watchlist", [{ name: "Watchlist", rows: watchlistRows(items) }])}>
            Export to Excel
          </button>
          {permission === "default" && <button className="secondary-button" onClick={() => void enableAlerts()}>Enable browser alerts</button>}
          {permission === "granted" && <span className="pill-tag pill-tag--ok">Browser alerts on</span>}
          {permission === "denied" && <span className="pill-tag pill-tag--muted">Alerts blocked in browser settings</span>}
          {expired.length > 0 && (
            <button className="mini-btn mini-btn--danger" onClick={() => setItems((prev) => prev.filter((w) => !expired.includes(w)))}>
              Clear {expired.length} expired / invalidated
            </button>
          )}
        </div>
      </section>

      {message && <p className="wl-msg" onClick={() => setMessage(null)}>{message}</p>}

      <section className="panel">
        {!ready ? null : items.length === 0 ? (
          <div className="empty-state">
            <p>Your watchlist is empty. Click <strong>Watch</strong> on any system pick or stock page.</p>
            <Link href="/" className="text-link">Go to the scanner →</Link>
          </div>
        ) : (
          <div className="table-shell">
            <table className="wl-tbl">
              <thead>
                <tr><th>Stock</th><th>Status</th><th>Last close</th><th>Entry trigger</th><th>Stop</th><th>Target</th><th>Expires</th><th></th></tr>
              </thead>
              <tbody>
                {sorted.map((w) => {
                  const status = statusOf(w);
                  const dist = w.last_close ? ((w.trigger_price - w.last_close) / w.last_close) * 100 : null;
                  const daysLeft = Math.ceil((new Date(w.expires_at).getTime() - Date.now()) / 86400000);
                  return (
                    <tr key={w.id}>
                      <td>
                        <Link href={`/stocks/${w.symbol}`} className="stock-link">{w.symbol}</Link>
                        <div className="table-subtext">{w.sector} · {fmtPattern(w.pattern)}</div>
                      </td>
                      <td><span className={`pill-tag ${STATUS_UI[status].cls}`}>{STATUS_UI[status].label}</span></td>
                      <td>
                        {w.last_close ? fmtINR(w.last_close) : "—"}
                        {dist !== null && status === "waiting" && <div className="table-subtext">{dist.toFixed(1)}% below trigger</div>}
                        {w.last_checked && <div className="table-subtext">checked {new Date(w.last_checked).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</div>}
                      </td>
                      <td><strong>{fmtINR(w.trigger_price)}</strong></td>
                      <td className="q-bad">{fmtINR(w.stop_loss)}</td>
                      <td className="q-ok">{fmtINR(w.target_price)}</td>
                      <td>{daysLeft > 0 ? `${daysLeft}d left` : "expired"}</td>
                      <td>
                        <div className="wl-row-actions">
                          {(status === "triggered" || status === "touched") && (
                            <button className="mini-btn mini-btn--primary" onClick={() => logTrade(w)}>Log trade</button>
                          )}
                          <button className="mini-btn" onClick={() => extend(w)}>+{WATCH_DAYS}d</button>
                          <button className="mini-btn mini-btn--danger" onClick={() => remove(w.id)}>Remove</button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="wl-note">
          Checks use end-of-day data from the scanner backend. Browser alerts fire only while this page is open; for
          email/WhatsApp alerts while you&apos;re away, the watchlist needs server-side storage (see the roadmap).
        </p>
      </section>

      <style>{`
        .wl-head { display:grid; grid-template-columns:1fr auto; gap:24px; align-items:end; margin-bottom:20px; }
        .wl-title { font-family:var(--font-space-grotesk),sans-serif; font-size:clamp(1.6rem,3.5vw,2.4rem); margin:8px 0 10px; line-height:1.1; }
        .wl-actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; justify-content:flex-end; }
        .wl-msg { background:var(--accent-soft); color:var(--accent); border-radius:12px; padding:8px 14px; font-size:0.88rem; cursor:pointer; }
        .wl-tbl { width:100%; border-collapse:collapse; font-size:0.87rem; }
        .wl-tbl th { font-size:0.74rem; padding:10px; white-space:nowrap; }
        .wl-tbl td { padding:10px; vertical-align:top; }
        .wl-row-actions { display:flex; flex-wrap:wrap; gap:6px; }
        .wl-note { font-size:0.8rem; color:var(--muted); margin:14px 0 0; }
        @media (max-width:960px) { .wl-head { grid-template-columns:1fr; } .wl-actions { justify-content:flex-start; } }
      `}</style>
    </main>
  );
}
