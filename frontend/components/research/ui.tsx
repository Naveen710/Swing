"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useCallback, useEffect, useState } from "react";

import { ApiError, Claim, DIM_SHORT, Dimension, Freshness as FreshnessT, STATUS_LABEL, Status, evidenceHref, fmtDate, research, setToken } from "../../lib/research";
import { AppNav } from "../app-nav";

const SECTIONS = [
  { href: "/research", label: "Dashboard" },
  { href: "/research/segment/large", label: "Large · Mid · Small" },
  { href: "/research/companies", label: "Companies" },
  { href: "/research/discovery", label: "Discovery queue" },
  { href: "/research/news", label: "News & filings" },
  { href: "/research/watchlists", label: "Watchlists" },
  { href: "/research/journal", label: "Journal" },
  { href: "/research/data", label: "Data quality" },
  { href: "/research/settings", label: "Settings" },
];

/* Load data with loading / error state and a reload function. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: ApiError | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [n, setN] = useState(0);
  useEffect(() => {
    let live = true;
    setLoading(true);
    fn().then((d) => { if (live) { setData(d); setError(null); } })
      .catch((e) => { if (live) setError(e instanceof ApiError ? e : new ApiError(0, String(e))); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n]);
  const reload = useCallback(() => setN((x) => x + 1), []);
  return { data, error, loading, reload };
}

function SignIn({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await research.login(pw);
      setToken(r.token);
      onDone();
    } catch (x) {
      setErr(x instanceof ApiError ? x.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel rs-signin">
      <h2>Sign in to Research</h2>
      <p className="muted">This section is protected because your watchlists and journal live on the server. Use the password set as RESEARCH_PASSWORD.</p>
      <form onSubmit={submit} className="rs-inline-form">
        <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="Password" aria-label="Password" autoFocus />
        <button className="primary-button" disabled={busy || !pw}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
      {err && <p className="error-text">{err}</p>}
    </section>
  );
}

export function ResearchShell({ title, intro, actions, children, sample }: { title: string; intro?: ReactNode; actions?: ReactNode; children: ReactNode; sample?: boolean }) {
  const path = usePathname();
  const [needAuth, setNeedAuth] = useState(false);
  const [key, setKey] = useState(0);
  useEffect(() => {
    const on = () => setNeedAuth(true);
    window.addEventListener("research-auth-required", on);
    return () => window.removeEventListener("research-auth-required", on);
  }, []);
  return (
    <main className="page-shell">
      <AppNav />
      <nav className="rs-subnav" aria-label="Research sections">
        {SECTIONS.map((s) => {
          const on = s.href === "/research" ? path === "/research"
            : s.href.startsWith("/research/segment/") ? Boolean(path?.startsWith("/research/segment/"))
            : path?.startsWith(s.href) || (s.href === "/research/companies" && path?.startsWith("/research/company/"));
          return <Link key={s.href} href={s.href} className={`rs-subnav-link ${on ? "rs-subnav-link--on" : ""}`} aria-current={on ? "page" : undefined}>{s.label}</Link>;
        })}
      </nav>
      <header className="rs-head">
        <div>
          <h1 className="rs-title">{title}</h1>
          {intro && <div className="muted rs-intro">{intro}</div>}
        </div>
        {actions && <div className="rs-actions">{actions}</div>}
      </header>
      {sample && <SampleBanner />}
      {needAuth ? <SignIn onDone={() => { setNeedAuth(false); setKey((k) => k + 1); }} /> : <div key={key}>{children}</div>}
      <p className="rs-footnote">Read-only research tool. Nothing here is investment advice or a recommendation to buy or sell; scores rank companies for further reading and do not predict returns.</p>
    </main>
  );
}

export function SampleBanner() {
  return <div className="rs-sample" role="note"><b>Sample data.</b> This is a fictional company used to test the platform — none of these numbers are real.</div>;
}

export function StatusBadge({ status, size = "md" }: { status: Status | null | undefined; size?: "sm" | "md" }) {
  if (!status) return <span className="rs-status rs-status--none">Not researched</span>;
  const tone = { ELIGIBLE_FOR_RESEARCH: "ok", REVIEW_REQUIRED: "warn", BLOCKED: "bad" }[status];
  return <span className={`rs-status rs-status--${tone} rs-status--${size}`}>{STATUS_LABEL[status]}</span>;
}

export function Score({ value, label }: { value: number | null | undefined; label?: string }) {
  if (value === null || value === undefined) return <span className="rs-score rs-score--na" title={label}>—</span>;
  const tone = value >= 70 ? "hi" : value >= 45 ? "mid" : "lo";
  return (
    <span className={`rs-score rs-score--${tone}`} title={label ? `${label}: ${value.toFixed(0)}/100` : undefined}>
      <span className="rs-score-fill" style={{ width: `${Math.max(4, Math.min(100, value))}%` }} />
      <span className="rs-score-num">{value.toFixed(0)}</span>
    </span>
  );
}

export function DimensionGrid({ dims, snapshotId }: { dims: Dimension[]; snapshotId?: number }) {
  return (
    <div className="rs-dims">
      {dims.map((d) => (
        <details key={d.key} className="rs-dim">
          <summary>
            <span className="rs-dim-label">{d.label}</span>
            <Score value={d.score} label={d.label} />
            <span className="rs-dim-cov">{d.coverage_pct}% data</span>
          </summary>
          <ul>
            {d.parts.map((p) => (
              <li key={p.metric}>
                <span>{p.reason}</span>
                <span className="rs-dim-part">{p.score === null ? "no data" : p.score.toFixed(0)}</span>
              </li>
            ))}
          </ul>
          {snapshotId && <Link className="rs-link-sm" href={evidenceHref(`dimension:${d.key}`, snapshotId)}>How this is scored</Link>}
        </details>
      ))}
    </div>
  );
}

const TYPE_LABEL: Record<string, string> = { computed: "Computed", sourced: "Sourced", inference: "Inference", unverified: "Unverified" };

export function EvidenceChips({ refs, snapshotId, max = 4 }: { refs: string[]; snapshotId?: number; max?: number }) {
  if (!refs.length) return <span className="rs-ev rs-ev--none">no evidence</span>;
  return (
    <span className="rs-evs">
      {refs.slice(0, max).map((r) => <Link key={r} href={evidenceHref(r, snapshotId)} className="rs-ev">{r}</Link>)}
      {refs.length > max && <span className="rs-ev rs-ev--more">+{refs.length - max}</span>}
    </span>
  );
}

export function ClaimList({ claims, snapshotId, empty }: { claims: Claim[]; snapshotId?: number; empty: string }) {
  if (!claims.length) return <p className="muted rs-empty-line">{empty}</p>;
  return (
    <ul className="rs-claims">
      {claims.map((c) => (
        <li key={c.id} className={`rs-claim rs-claim--${c.type}`}>
          <span className={`rs-claim-type rs-claim-type--${c.type}`}>{TYPE_LABEL[c.type] ?? c.type}</span>
          <span className="rs-claim-text">{c.text}</span>
          <EvidenceChips refs={c.evidence} snapshotId={snapshotId} />
        </li>
      ))}
    </ul>
  );
}

export function FreshnessLine({ f }: { f: FreshnessT }) {
  return (
    <div className="rs-fresh">
      <span className={f.price_stale ? "rs-fresh-bad" : ""}>Prices: {f.last_price_date ? `${fmtDate(f.last_price_date)}${f.price_age_days !== null ? ` (${f.price_age_days}d old)` : ""}` : "none"}</span>
      <span className={f.fy_stale ? "rs-fresh-bad" : ""}>Latest annual: {f.latest_fy ? fmtDate(f.latest_fy) : "none"}</span>
      <span>Latest quarter: {f.latest_quarter ? fmtDate(f.latest_quarter) : "none"}</span>
      <span>Last refresh: {f.last_ingest ? `${fmtDate(f.last_ingest)} (${f.last_ingest_status})` : "never"}</span>
    </div>
  );
}

export function StateBox({ loading, error, empty, emptyText, onRetry, children }: { loading?: boolean; error?: ApiError | null; empty?: boolean; emptyText?: ReactNode; onRetry?: () => void; children?: ReactNode }) {
  if (error && error.status !== 401) {
    return (
      <div className="panel rs-state rs-state--error" role="alert">
        <p><b>Couldn&apos;t load this.</b> {error.message}</p>
        {onRetry && <button className="mini-btn" onClick={onRetry}>Try again</button>}
      </div>
    );
  }
  if (error) return null;
  if (loading) return <div className="panel rs-state" aria-busy="true"><span className="rs-spinner" aria-hidden /> Loading…</div>;
  if (empty) return <div className="panel rs-state rs-state--empty">{emptyText}</div>;
  return <>{children}</>;
}

export function DimShort({ k }: { k: string }) {
  return <>{DIM_SHORT[k] ?? k}</>;
}

/* Poll the ingestion job until it finishes, then call onDone. */
export function useIngestPoll(onDone: () => void) {
  const [state, setState] = useState<Awaited<ReturnType<typeof research.ingestStatus>> | null>(null);
  const [active, setActive] = useState(false);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(async () => {
      try {
        const s = await research.ingestStatus();
        setState(s);
        if (!s.running) { setActive(false); onDone(); }
      } catch { /* keep polling */ }
    }, 2000);
    return () => clearInterval(t);
  }, [active, onDone]);
  return { state, start: () => setActive(true), active };
}
