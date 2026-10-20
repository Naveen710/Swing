"use client";

import { useState } from "react";

import { fmtDate, research, setToken } from "../../lib/research";
import { ResearchShell, StateBox, useIngestPoll, useLoad } from "./ui";

export function ResearchSettings() {
  const { data, error, loading, reload } = useLoad(() => research.settings(), []);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const poll = useIngestPoll(reload);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(true);
    setMsg(`${label}…`);
    try {
      const r = await fn();
      setMsg(`${label}: done${r && typeof r === "object" && "verification" in r ? ` · verification ${JSON.stringify((r as { verification: unknown }).verification)}` : ""}`);
      reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : `${label} failed`);
    } finally {
      setBusy(false);
    }
  }
  async function download() {
    const b = await research.backup();
    const url = URL.createObjectURL(new Blob([JSON.stringify(b, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `research-backup-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }
  async function restore(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f || !confirm("Restoring replaces your current watchlists, journal and preferences with the backup. Continue?")) return;
    await run("Restore", async () => research.restore(JSON.parse(await f.text())));
  }

  return (
    <ResearchShell title="Settings" intro="Where the data comes from, how fresh it is, the optional AI, and backups.">
      {msg && <p className="rs-small">{msg}</p>}
      <StateBox loading={loading && !data} error={error} onRetry={reload}>
        {data && (
          <>
            <section className="panel">
              <h2>Data sources</h2>
              <table className="rs-tbl"><thead><tr><th>Source</th><th>Used for</th><th>Status</th></tr></thead>
                <tbody>{data.sources.map((s) => <tr key={s.name}><td><b>{s.name}</b></td><td>{s.role}</td><td>{s.status}</td></tr>)}</tbody></table>
              <p className="muted rs-small">News feeds: {data.news_feeds.join(", ")} (set RESEARCH_NEWS_FEEDS to change). Free sources can change or block access; failures appear on the Data quality page and nothing is invented to fill them.</p>
              <p className="muted rs-small">Refresh schedule: {data.refresh}</p>
            </section>

            <section className="panel">
              <h2>Deep-research set ({data.deep_symbols.length} companies)</h2>
              <p className="rs-mono rs-small">{data.deep_symbols.join(", ")}</p>
              <p className="muted rs-small">Change with RESEARCH_SAMPLE_SYMBOLS on the API service. Refreshing fetches prices, statements, filings and their PDFs for each — a few minutes for 20 companies.</p>
              <div className="button-group">
                <button className="primary-button" disabled={poll.active} onClick={() => void research.ingest({ deep: true }).then((r) => { if (!r.started) setMsg(r.reason ?? ""); poll.start(); })}>
                  {poll.active ? `Refreshing ${poll.state?.done ?? 0}/${poll.state?.total ?? "…"} ${poll.state?.current ?? ""}` : "Refresh deep-research set"}</button>
                <button className="secondary-button" disabled={busy} onClick={() => void run("Loading sample companies", () => research.seedSamples())}>Load sample companies</button>
              </div>
              {poll.state && !poll.active && poll.state.results && <p className="muted rs-small">Last run: {poll.state.results.map((r) => `${r.symbol} ${r.status}`).join(" · ")}</p>}
            </section>

            <section className="panel">
              <h2>AI (optional)</h2>
              <dl className="rs-dl">
                <dt>Status</dt><dd className={data.ai.enabled ? "q-ok" : ""}>{data.ai.enabled ? "Enabled" : "Not configured — everything except AI extraction and narration works"}</dd>
                <dt>Model</dt><dd>{data.ai.provider} · {data.ai.model} · prompt {data.ai.prompt_version}</dd>
                <dt>Usage so far</dt><dd>{data.ai.calls_cached} cached results · {data.ai.input_tokens.toLocaleString("en-IN")} input / {data.ai.output_tokens.toLocaleString("en-IN")} output tokens</dd>
              </dl>
              <p className="muted rs-small">Set RESEARCH_LLM_API_KEY (and optionally RESEARCH_LLM_MODEL) on the API service. The AI only extracts quoted statements from filings and rephrases computed claims; Python does every calculation, results are cached per document, and documents are treated as untrusted text.</p>
            </section>

            <section className="panel">
              <h2>Access</h2>
              <p>{data.protected ? "Password protection is on." : <span className="q-bad">Not protected. Anyone with your site&apos;s address can read and edit your watchlists and journal.</span>}</p>
              <p className="muted rs-small">Set RESEARCH_PASSWORD (and RESEARCH_SECRET) on the API service to require sign-in.</p>
              {data.protected && <button className="mini-btn" onClick={() => { setToken(null); location.reload(); }}>Sign out</button>}
            </section>

            <section className="panel">
              <h2>Backup and restore</h2>
              <p className="muted rs-small">{data.backup} Market data is re-fetchable, so it isn&apos;t in the backup.</p>
              <div className="button-group">
                <button className="secondary-button" onClick={() => void download()}>Download backup</button>
                <label className="secondary-button rs-file-btn">Restore from file<input type="file" accept="application/json" onChange={(e) => void restore(e)} /></label>
              </div>
            </section>

            <section className="panel">
              <h2>Database</h2>
              <ul className="rs-plain">{data.schema.map((s) => <li key={s.version}>Migration {s.version}: {s.name} · {fmtDate(s.applied_at)}</li>)}</ul>
            </section>
          </>
        )}
      </StateBox>
    </ResearchShell>
  );
}
