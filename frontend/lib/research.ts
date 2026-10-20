/* Research platform API client. Read-only research — no trading calls exist. */

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api";
const TOKEN_KEY = "swing.research.token.v1";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export function getToken(): string | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable — sign-in lasts for this page only */
  }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  if (!(init.body instanceof FormData) && init.body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${API}/research${path}`, { ...init, headers, cache: "no-store" });
  } catch {
    throw new ApiError(0, "Can't reach the API. On Render's free plan it may take a minute to wake up — try again shortly.");
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON */
    }
    if (res.status === 401 && typeof window !== "undefined") window.dispatchEvent(new CustomEvent("research-auth-required"));
    throw new ApiError(res.status, detail);
  }
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

const get = <T,>(p: string) => call<T>(p);
const post = <T,>(p: string, body?: unknown) => call<T>(p, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const put = <T,>(p: string, body: unknown) => call<T>(p, { method: "PUT", body: JSON.stringify(body) });
const del = <T,>(p: string) => call<T>(p, { method: "DELETE" });
const qs = (o: Record<string, string | number | boolean | null | undefined>) => {
  const q = new URLSearchParams();
  Object.entries(o).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") q.set(k, String(v)); });
  const s = q.toString();
  return s ? `?${s}` : "";
};

export function rawUrl(rawId: number) {
  return `${API}/research/raw/${rawId}/content`;
}

/* ── types (shapes returned by backend/app/research) ─────────────────── */

export type Status = "BLOCKED" | "REVIEW_REQUIRED" | "ELIGIBLE_FOR_RESEARCH";
export interface Metric { key: string; label: string; value: number | null; unit: string; status: string; reason: string | null; formula: string; version: string; inputs: number[]; period: string | null }
export interface Dimension { key: string; label: string; score: number | null; coverage_pct: number; parts: { metric: string; weight: number; score: number | null; reason: string }[] }
export interface Check { key: string; label: string; status: string; detail: string; metrics: string[]; severity: string }
export interface Claim { id: string; section: string; text: string; type: "computed" | "sourced" | "inference" | "unverified"; evidence: string[]; status: string }
export interface Flag { level: "blocker" | "review" | "watch"; text: string; source: string }
export interface Scenario { value_per_share: number | null; upside_pct?: number; reason?: string; justified_pb?: number; terminal_share_pct?: number | null; assumptions: Record<string, number> }
export interface Valuation { label: string; method: string | null; unavailable_reason: string | null; scenarios: Record<string, Scenario>; reverse_dcf: { implied_growth_pct: number | null; delivered_growth_pct: number | null; note: string } | null; inputs: number[] }
export interface FinRow { period_end: string; revenue_cr: number | null; ebit_cr: number | null; net_income_cr: number | null; cfo_cr: number | null; fcf_cr: number | null; debt_cr: number | null; equity_cr: number | null; ebit_margin_pct: number | null; net_margin_pct: number | null; fact_ids: number[] }
export interface DossierOutput {
  code_version: string; as_of: string | null; status: Status; status_note: string; flags: Flag[]; dimensions: Dimension[];
  metrics: Record<string, Metric>; checks: Check[]; valuation: Valuation; claims: Claim[]; financial_table: FinRow[];
  price: { last: number | null; date: string | null; points: number };
  company: { id: number; name: string; symbol: string; isin: string | null; sector: string | null; industry: string | null; is_financial: boolean; is_sample: boolean; status: string };
  provenance: { providers: string[]; facts: number; fact_issues: string[]; sample_data: boolean; index: { name: string; ret_1y: number; as_of: string } | null; documents: number; announcements: number; ai_extractions: number };
}
export interface Freshness { last_price: number | null; last_price_date: string | null; price_age_days: number | null; price_stale: boolean; latest_fy: string | null; latest_quarter: string | null; fy_stale: boolean; last_ingest: string | null; last_ingest_status: string | null }
export interface Issue { id: number; company_id: number | null; symbol?: string | null; name?: string | null; kind: string; field: string; detail: string; severity: string; status: string; first_seen: string; last_seen: string }
export interface CompanyDetail {
  company: { id: number; name: string; symbol: string; isin: string | null; sector: string | null; industry: string | null; is_sample: boolean; deep_research: boolean; is_financial: boolean; status: string; listing_date: string | null; source: string };
  securities: { exchange: string; symbol: string; provider_symbol: string | null; status: string }[];
  identifier_history: { field: string; old_value: string; new_value: string; recorded_at: string }[];
  profile: Record<string, string | number>;
  freshness: Freshness;
  snapshot: { id: number; created_at: string; output: DossierOutput; input_hash: string; output_hash: string; model_version: string | null } | null;
  history: { id: number; as_of: string; status: Status; dimensions: Record<string, number | null>; created_at: string; code_version: string }[];
  issues: Issue[]; watchlists: { id: number; name: string }[]; decisions: number;
}
export interface CompanyRow { id: number; name: string; symbol: string; isin: string | null; sector: string | null; industry: string | null; is_sample: boolean; deep_research: boolean; status: string; is_financial: boolean; research_status: Status | null; as_of: string | null; facts: number }
export interface QueueItem { snapshot_id: number; symbol: string; name: string; sector: string | null; is_sample: boolean; is_financial: boolean; as_of: string; status: Status; dimensions: Record<string, number | null>; flags: Flag[]; flag_counts: Record<string, number>; percentiles: Record<string, number | null>; percentile_basis: Record<string, string> }
export interface NewsItem { id: number; headline: string; publisher: string | null; url: string; published_at: string | null; retrieved_at: string; summary: string | null; symbol: string | null; company_name: string | null; is_sample: boolean | null; match_method: string; duplicates?: { publisher: string; url: string; published_at: string }[] }
export interface Announcement { id: number; symbol: string; company_name: string; is_sample: boolean; category: string | null; event_type: string | null; text: string | null; published_at: string | null; attachment_url: string | null; document_id: number | null; raw_source_id: number | null }
export interface WatchItem { id: number; symbol: string; name: string; is_sample: boolean; reason: string | null; added_at: string; added_price: number | null; price: number | null; price_date: string | null; price_change_pct: number | null; status_then: Status | null; status_now: Status | null; dimension_changes: Record<string, number>; dimensions_now: Record<string, number | null> }
export interface Watchlist { id: number; name: string; description: string | null; created_at: string; items: WatchItem[] }
export interface FollowUp { date: string; note: string; outcome: string | null; price: number | null; price_date: string | null; snapshot_id: number | null; status: Status | null }
export interface Decision { id: number; symbol: string; name: string; is_sample: boolean; title: string | null; thesis: string | null; drivers: string | null; valuation_assumptions: string | null; downside_risks: string | null; invalidation: string | null; status: string; price_at_entry: number | null; price_now: number | null; change_since_entry_pct: number | null; follow_ups: FollowUp[]; outcome: string | null; created_at: string; updated_at: string; snapshot_id: number | null }
export interface Run { id: number; kind: string; symbol: string | null; started_at: string; finished_at: string | null; status: string; detail: Record<string, unknown> }
export interface Verification { case_id: string; symbol: string; kind: string; field: string; period: string; expected: number | null; actual: number | null; tolerance_pct: number; status: string; source_note: string | null; explanation: string | null; checked_at: string }
export interface DataQuality { issues: Issue[]; counts: { kind: string; severity: string; count: number }[]; runs: Run[]; verification: Verification[]; source_errors: { id: number; source_key: string; provider: string; retrieved_at: string; error: string }[]; sources: { provider: string; payloads: number; last_retrieved: string }[] }
export interface IngestState { running: boolean; stage: string; done?: number; total?: number; current?: string | null; results?: { symbol: string; status: string; error?: string }[]; error?: string; started_at?: string; finished_at?: string;
  label?: string | null; segment?: SegmentKey | null; counts?: { ok: number; partial: number; error: number; fresh: number }; documents?: boolean }

export type SegmentKey = "large" | "mid" | "small";
export const SEGMENT_KEYS: SegmentKey[] = ["large", "mid", "small"];
type StatusTotals = Record<Status, number>;
export interface SegmentSummary { key: SegmentKey; label: string; index: string; rank: string; count: number; source: "niftyindices" | "bundled" | null; list_as_of: string | null; researched: number; totals: StatusTotals }
export interface SegmentItem {
  symbol: string; name: string; industry: string | null; is_financial: boolean; snapshot_id: number | null; status: Status | null; as_of: string | null;
  last_refresh: string | null; price: number | null; price_date: string | null; dimensions: Record<string, number | null>;
  metrics: Record<string, { value: number | null; unit: string }>; flag_counts: { blocker: number; review: number; watch: number };
}
export interface SegmentDetail extends SegmentSummary { items: SegmentItem[]; job: IngestState; metrics: string[]; note: string }

/* ── calls ────────────────────────────────────────────────────────────── */

export const research = {
  health: () => get<{ status: string; database: { ok: boolean; dialect: string; schema: number[] }; demo_mode: boolean; ai_enabled: boolean; protected: boolean }>("/health"),
  authStatus: () => get<{ protected: boolean }>("/auth/status"),
  login: (password: string) => post<{ token: string; expires_at: number }>("/auth/login", { password }),
  dashboard: () => get<{ market: { name: string; ret_1y: number; ret_3m: number; as_of: string } | null; demo_mode: boolean; totals: Record<string, number>; candidates: QueueItem[]; watchlist_moves: WatchItem[]; filings: Announcement[]; news: NewsItem[]; data_health: { counts: { kind: string; severity: string; count: number }[]; last_run: { kind: string; finished_at: string; status: string } | null }; queue_totals: Record<string, number>; note: string }>("/dashboard"),
  companies: (p: { q?: string; sector?: string; scope?: string; status?: string; coverage?: string; limit?: number; offset?: number }) => get<{ total: number; items: CompanyRow[] }>(`/companies${qs(p)}`),
  sectors: () => get<{ sector: string; companies: number }[]>("/sectors"),
  company: (s: string) => get<CompanyDetail>(`/company/${encodeURIComponent(s)}`),
  prices: (s: string, days = 400) => get<{ bars: [string, number, number, number, number, number][]; actions: { date: string; action: string; value: number }[]; provider: string | null; adjustment: string }>(`/company/${encodeURIComponent(s)}/prices?days=${days}`),
  facts: (s: string) => get<{ facts: Record<string, string | number | boolean | null>[] }>(`/company/${encodeURIComponent(s)}/facts`),
  dossierMd: (s: string) => get<string>(`/company/${encodeURIComponent(s)}/dossier.md`),
  segments: () => get<{ segments: SegmentSummary[]; job: IngestState; note: string }>("/segments"),
  segment: (k: SegmentKey) => get<SegmentDetail>(`/segments/${k}`),
  fetchSegment: (k: SegmentKey, o: { force?: boolean; refresh_list?: boolean; documents?: boolean } = {}) => post<{ started: boolean; state: IngestState; reason?: string }>(`/segments/${k}/fetch`, o),
  segmentTemplate: (k: SegmentKey) => get<string>(`/segments/${k}/verification-template.csv`),
  stopIngest: () => post<{ stopping: boolean; state: IngestState }>("/ingest/stop"),
  refresh: (s: string) => post<{ started: boolean; state: IngestState; reason?: string }>(`/company/${encodeURIComponent(s)}/refresh`),
  replay: (id: number) => post<{ reproducible: boolean | null; stored_hash?: string; replayed_hash?: string; reason?: string }>(`/snapshots/${id}/replay`),
  discovery: (p: { sort?: string; status?: string; sector?: string; include_sample?: boolean; min_confidence?: number }) => get<{ sort: string; items: QueueItem[]; totals: Record<string, number>; note: string }>(`/discovery${qs(p)}`),
  news: (p: { symbol?: string; q?: string; limit?: number; include_duplicates?: boolean; matched_only?: boolean }) => get<{ items: NewsItem[]; note: string }>(`/news${qs(p)}`),
  announcements: (p: { symbol?: string; event_type?: string; limit?: number }) => get<{ items: Announcement[] }>(`/announcements${qs(p)}`),
  evidence: (ref: string, snapshotId?: number) => get<Record<string, unknown>>(`/evidence/${encodeURIComponent(ref)}${qs({ snapshot_id: snapshotId })}`),
  document: (id: number) => get<{ document: Record<string, string | number | null>; company: { name: string; symbol: string } | null; chunks: { id: number; page: number; seq: number; text: string }[]; extractions: { model: string; created_at: string; claims: { section: string; text: string; quote: string; chunk_id: number; status: string; reason: string | null }[]; language_changes: string | null }[] }>(`/documents/${id}`),
  search: (q: string) => get<{ companies: CompanyRow[]; passages: { chunk_id: number; document_id: number; page: number; snippet: string }[]; news: NewsItem[] }>(`/search${qs({ q })}`),
  watchlists: () => get<Watchlist[]>("/watchlists"),
  createWatchlist: (name: string, description?: string) => post<{ id: number }>("/watchlists", { name, description }),
  renameWatchlist: (id: number, name: string, description?: string | null) => put<{ ok: boolean }>(`/watchlists/${id}`, { name, description }),
  deleteWatchlist: (id: number) => del<{ ok: boolean }>(`/watchlists/${id}`),
  addToWatchlist: (id: number, symbol: string, reason: string) => post<{ id: number }>(`/watchlists/${id}/items`, { symbol, reason }),
  removeFromWatchlist: (id: number, itemId: number) => del<{ ok: boolean }>(`/watchlists/${id}/items/${itemId}`),
  decisions: (symbol?: string) => get<Decision[]>(`/decisions${qs({ symbol })}`),
  createDecision: (body: Partial<Decision> & { symbol: string }) => post<{ id: number }>("/decisions", body),
  updateDecision: (id: number, body: Partial<Decision>) => put<{ ok: boolean }>(`/decisions/${id}`, body),
  deleteDecision: (id: number) => del<{ ok: boolean }>(`/decisions/${id}`),
  followUp: (id: number, note: string, outcome: string | null) => post<FollowUp>(`/decisions/${id}/follow-ups`, { note, outcome }),
  dataQuality: (status = "open", kind?: string) => get<DataQuality>(`/data-quality${qs({ status, kind })}`),
  setIssue: (id: number, status: string) => post<{ ok: boolean }>(`/issues/${id}`, { status }),
  settings: () => get<{ sources: { name: string; role: string; status: string }[]; news_feeds: string[]; deep_symbols: string[]; refresh: string; ai: { enabled: boolean; model: string; provider: string; prompt_version: string; calls_cached: number; input_tokens: number; output_tokens: number }; protected: boolean; schema: { version: number; name: string; applied_at: string }[]; preferences: Record<string, unknown>; backup: string }>("/settings"),
  savePreferences: (preferences: Record<string, unknown>) => put<{ ok: boolean }>("/settings", { preferences }),
  ingest: (body: { symbols?: string[]; deep?: boolean; news?: boolean }) => post<{ started: boolean; state: IngestState; reason?: string }>("/ingest", body),
  ingestStatus: () => get<IngestState>("/ingest/status"),
  seedSamples: () => post<{ statuses: Record<string, Status>; verification: Record<string, number> | null }>("/seed-samples"),
  refreshNews: () => post<{ status: string; new?: number; duplicates?: number; error?: string }>("/news/refresh"),
  backup: () => get<Record<string, unknown>>("/backup"),
  restore: (data: unknown) => post<{ restored: boolean; watchlists: number; decisions: number; skipped: string[] }>("/restore", data),
  importFacts: (file: File) => { const f = new FormData(); f.append("file", file); return call<{ status: string; inserted: number; unchanged: number; restated: number; unknown_symbol: number; errors: string[] }>("/import/facts", { method: "POST", body: f }); },
  importDocument: (file: File, symbol?: string, title?: string) => { const f = new FormData(); f.append("file", file); if (symbol) f.append("symbol", symbol); if (title) f.append("title", title); return call<{ document_id: number; created: boolean; text_status: string; pages: number }>("/import/document", { method: "POST", body: f }); },
  verify: (file: File) => { const f = new FormData(); f.append("file", file); return call<{ summary: Record<string, number> }>("/verify", { method: "POST", body: f }); },
  extract: (docId: number) => post<{ claims: { section: string; text: string; quote: string; chunk_id: number; status: string; reason: string | null }[]; cached: boolean }>(`/ai/extract/${docId}`),
  narrate: (snapshotId: number) => post<{ paragraphs: { text: string; status: string; reason: string | null }[]; model: string; cached: boolean }>(`/ai/narrate/${snapshotId}`),
};

/* ── formatting helpers ───────────────────────────────────────────────── */

export const STATUS_LABEL: Record<Status, string> = { ELIGIBLE_FOR_RESEARCH: "Eligible for research", REVIEW_REQUIRED: "Review required", BLOCKED: "Blocked" };
export const DIM_SHORT: Record<string, string> = {
  business_quality: "Quality", growth_inflection: "Growth", financial_strength: "Strength", valuation: "Valuation",
  governance_risk: "Governance", data_confidence: "Confidence", recognition_gap: "Recognition gap",
};

export function fmtMetric(m: Pick<Metric, "value" | "unit"> | undefined | null): string {
  if (!m || m.value === null || m.value === undefined) return "—";
  const v = m.value;
  switch (m.unit) {
    case "pct": return `${v.toFixed(1)}%`;
    case "pp": return `${v >= 0 ? "+" : ""}${v.toFixed(1)} pp`;
    case "x": return `${v.toFixed(2)}×`;
    case "days": return `${v.toFixed(0)} days`;
    case "INR": return `₹${(v / 1e7).toLocaleString("en-IN", { maximumFractionDigits: 0 })} cr`;
    default: return v.toFixed(2);
  }
}

export const fmtCr = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toLocaleString("en-IN", { maximumFractionDigits: 1 }));
export const fmtDate = (s: string | null | undefined) => (s ? new Date(s.length <= 10 ? `${s}T00:00:00` : s).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "—");
export const fmtPrice = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`);

export const isSegment = (k: string | null | undefined): k is SegmentKey => k === "large" || k === "mid" || k === "small";

/* Progress of a background fetch, 0-100 (null when nothing is known yet). */
export function jobProgress(j: Pick<IngestState, "done" | "total"> | null | undefined): number | null {
  if (!j || !j.total) return null;
  return Math.min(100, Math.round(((j.done ?? 0) / j.total) * 100));
}

export function evidenceHref(ref: string, snapshotId?: number): string {
  return `/research/evidence/${encodeURIComponent(ref)}${snapshotId ? `?snapshot=${snapshotId}` : ""}`;
}
