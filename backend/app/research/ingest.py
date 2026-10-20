"""Ingestion orchestrator. One run per company (or per news pull), recorded in rs_ingestion_runs.

Guarantees:
  * Idempotent: re-running with unchanged source data adds nothing (raw archive only bumps
    last_seen_at; facts, prices, actions, announcements, news are keyed).
  * Corrections are versions: a changed value for the same fact creates version N+1, marks the
    old one superseded, and logs a 'conflict' data issue — the old value is kept.
  * Every missing field, failed fetch and stale source becomes an rs_data_issues row; issues not
    seen again on a later run are resolved automatically.
"""
from __future__ import annotations

import logging
import math
import re
import threading
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.engine import Connection

from app.config import settings
from app.research import models as m
from app.research.archive import archive, archive_error
from app.research.db import now, tx
from app.research.documents import classify_doc, store_document
from app.research.providers.base import ERROR, OK, FactRecord, ProviderResult, SecurityRef
from app.research.providers.fixtures import FixtureProvider
from app.research.providers.rss import company_keys, dup_group, match_company

logger = logging.getLogger(__name__)

STALE_PRICE_DAYS = 6
STALE_FY_DAYS = 500
MAX_ATTACHMENTS_PER_RUN = 3
EVENT_TYPES = (
    ("governance", ("resignation of statutory auditor", "auditor", "sebi", "forensic", "default", "fraud", "insolvency", "pledge")),
    ("results", ("financial result", "results")),
    ("order", ("order", "contract", "bagging")),
    ("capital", ("preferential", "allotment", "qip", "rights issue", "warrant", "buyback", "split", "bonus", "dividend")),
    ("corporate", ("acquisition", "amalgamation", "merger", "scheme of arrangement", "joint venture", "capacity", "expansion")),
    ("meeting", ("analyst", "investor", "con. call", "meet", "board meeting", "agm", "egm")),
)
ATTACHMENT_TYPES = {"results", "order", "corporate", "meeting"}


def demo_mode() -> bool:
    return settings.market_data_provider == "demo"


def classify_event(category: str | None, text: str | None) -> str:
    t = f"{category or ''} {text or ''}".lower()
    for kind, words in EVENT_TYPES:
        if any(w in t for w in words):
            return kind
    return "other"


class Providers:
    """Chooses sources. Demo mode (MARKET_DATA_PROVIDER=demo) uses fixtures only; otherwise Yahoo +
    NSE + RSS for real companies, and fixtures for the fictional sample companies."""

    def __init__(self, demo: bool | None = None) -> None:
        self.demo = demo_mode() if demo is None else demo
        self.fixture = FixtureProvider()
        if not self.demo:
            from app.research.providers.nse import NseProvider
            from app.research.providers.rss import RssProvider
            from app.research.providers.yahoo import YahooProvider

            self.yahoo, self.nse, self.rss = YahooProvider(), NseProvider(), RssProvider()

    def for_company(self, is_sample: bool):
        if self.demo or is_sample:
            return {"prices": self.fixture, "metadata": self.fixture, "financials": self.fixture,
                    "announcements": self.fixture, "documents": self.fixture}
        return {"prices": self.yahoo, "metadata": self.yahoo, "financials": self.yahoo,
                "announcements": self.nse, "documents": self.nse}

    def news(self):
        return self.fixture if self.demo else self.rss

    def describe(self) -> list[dict]:
        if self.demo:
            return [{"name": "fixture", "role": "all data (demo mode)", "status": "sample data — fictional companies"}]
        return [
            {"name": "yahoo", "role": "prices, corporate actions, metadata, statements", "status": "live (yfinance)"},
            {"name": "nse", "role": "corporate announcements + attachments", "status": "live (may be blocked from cloud hosts)"},
            {"name": "rss", "role": "news headlines", "status": "live (publisher RSS)"},
            {"name": "manual", "role": "CSV / Excel facts, PDF documents", "status": "upload"},
            {"name": "fixture", "role": "fictional sample companies", "status": "always available"},
        ]


# ── data issues ──────────────────────────────────────────────────────────────

class IssueLog:
    def __init__(self, conn: Connection, company_id: int | None, run_id: int) -> None:
        self.conn, self.company_id, self.run_id = conn, company_id, run_id
        self.seen: set[tuple[str, str]] = set()

    def add(self, kind: str, field: str, detail: str, severity: str = "warn") -> None:
        self.seen.add((kind, field))
        c = m.data_issues.c
        ts = now()
        cond = (c.kind == kind) & (c.field == field) & (c.status == "open")
        cond &= (c.company_id == self.company_id) if self.company_id is not None else c.company_id.is_(None)
        row = self.conn.execute(select(c.id).where(cond)).first()
        if row:
            self.conn.execute(m.data_issues.update().where(c.id == row.id).values(last_seen=ts, detail=detail[:2000], run_id=self.run_id, severity=severity))
        else:
            self.conn.execute(m.data_issues.insert().values(company_id=self.company_id, kind=kind, field=field, detail=detail[:2000],
                                                            severity=severity, status="open", first_seen=ts, last_seen=ts, run_id=self.run_id))

    def resolve_unseen(self, kinds: tuple[str, ...], fields_prefix: tuple[str, ...] = ()) -> int:
        """Auto-resolve open issues of these kinds for this company that this run did not observe again."""
        c = m.data_issues.c
        if self.company_id is None:
            return 0
        rows = self.conn.execute(select(c.id, c.kind, c.field).where((c.company_id == self.company_id) & (c.status == "open") & c.kind.in_(kinds))).all()
        n = 0
        for r in rows:
            if (r.kind, r.field) in self.seen:
                continue
            if fields_prefix and not r.field.startswith(fields_prefix):
                continue
            self.conn.execute(m.data_issues.update().where(c.id == r.id).values(status="resolved", resolved_at=now()))
            n += 1
        return n


# ── facts ────────────────────────────────────────────────────────────────────

def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


def upsert_fact(conn: Connection, company_id: int, f: FactRecord, provider: str, raw_id: int | None,
                issues: IssueLog | None, verification: str = "unverified") -> tuple[int, str]:
    """Returns (fact_id, action) where action is inserted | unchanged | restated."""
    c = m.financial_facts.c
    key = and_(c.company_id == company_id, c.metric == f.metric, c.period_end == f.period_end, c.period_type == f.period_type,
               c.scope == f.scope, c.provider == provider, c.is_current.is_(True))
    cur = conn.execute(select(c.id, c.value, c.unit, c.version).where(key)).first()
    ts = now()
    values = dict(company_id=company_id, metric=f.metric, value=f.value, unit=f.unit, period_end=f.period_end,
                  period_type=f.period_type, scope=f.scope, published_at=f.published_at, provider=provider,
                  raw_source_id=raw_id, verification=verification, is_current=True, recorded_at=ts, note=f.note)
    if cur is None:
        return conn.execute(m.financial_facts.insert().values(version=1, **values)).inserted_primary_key[0], "inserted"
    if _same(cur.value, f.value) and cur.unit == f.unit:
        return cur.id, "unchanged"
    new_id = conn.execute(m.financial_facts.insert().values(version=cur.version + 1, **values)).inserted_primary_key[0]
    conn.execute(m.financial_facts.update().where(c.id == cur.id).values(is_current=False, superseded_by=new_id))
    if issues:
        issues.add("conflict", f"{f.metric}:{f.period_type}:{f.period_end}",
                   f"{provider} changed {f.metric} for {f.period_type} {f.period_end}: {cur.value} {cur.unit} → {f.value} {f.unit} "
                   f"(version {cur.version} kept as history)", severity="info")
    return new_id, "restated"


def _raw_ids(conn: Connection, res: ProviderResult, provider: str, company_id: int | None) -> dict[str, int]:
    ids = {}
    for r in res.raw:
        ids[r.source_key], _ = archive(conn, source_key=r.source_key, provider=provider, content=r.content,
                                       content_type=r.content_type, url=r.url, company_id=company_id, published_at=r.published_at)
    return ids


# ── per-company ingestion ────────────────────────────────────────────────────

def _company(conn: Connection, symbol: str):
    row = conn.execute(
        select(m.companies, m.securities.c.id.label("security_id"), m.securities.c.symbol, m.securities.c.provider_symbol)
        .join(m.securities, m.securities.c.company_id == m.companies.c.id)
        .where((m.securities.c.symbol == symbol.upper().removesuffix(".NS")) & (m.securities.c.exchange == "NSE"))
    ).mappings().first()
    return row


def ingest_company(symbol: str, providers: Providers | None = None, fetch_documents: bool = True) -> dict:
    providers = providers or Providers()
    with tx() as conn:
        comp = _company(conn, symbol)
        if comp is None:
            raise KeyError(f"{symbol} is not in the company master")
        run_id = conn.execute(m.ingestion_runs.insert().values(kind="company", company_id=comp["id"], started_at=now(), status="running",
                                                               detail={"symbol": comp["symbol"]})).inserted_primary_key[0]
    src = providers.for_company(comp["is_sample"])
    sec = SecurityRef("NSE", comp["symbol"], comp["provider_symbol"], comp["name"], comp["isin"])
    steps: dict[str, dict] = {}

    def step(name: str, fn) -> None:
        try:
            with tx() as conn:
                issues = IssueLog(conn, comp["id"], run_id)
                steps[name] = fn(conn, issues)
                kinds = {"metadata": ("missing", "fetch_error"), "prices": ("fetch_error", "stale"),
                         "financials": ("missing", "fetch_error", "stale", "unsupported"), "announcements": ("fetch_error",),
                         "documents": ("parse_error", "fetch_error")}[name]
                issues.resolve_unseen(kinds, (f"{name}.",))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Research ingest step %s failed for %s", name, symbol)
            steps[name] = {"status": "error", "error": str(exc)[:300]}
            with tx() as conn:
                IssueLog(conn, comp["id"], run_id).add("fetch_error", f"{name}.exception", str(exc), "error")

    def do_metadata(conn, issues):
        p = src["metadata"]
        res = p.get_metadata(sec)
        if not res.ok:
            archive_error(conn, source_key=f"{p.name}:info:{sec.symbol}", provider=p.name, error=res.error or res.status, company_id=comp["id"])
            issues.add("fetch_error", "metadata.info", res.error or res.status)
            return {"status": res.status, "error": res.error}
        _raw_ids(conn, res, p.name, comp["id"])
        d = res.data or {}
        changes = {}
        if d.get("sector") and not comp["sector"]:
            changes["sector"] = d["sector"]
        if d.get("industry") and not comp["industry"]:
            changes["industry"] = d["industry"]
        if changes:
            from app.research.master import is_financial
            changes["is_financial"] = is_financial(changes.get("sector", comp["sector"]), changes.get("industry", comp["industry"]))
            changes["updated_at"] = now()
            conn.execute(m.companies.update().where(m.companies.c.id == comp["id"]).values(**changes))
        for f in res.missing:
            issues.add("missing", f"metadata.{f}", f"{p.name} did not provide {f}", "info")
        conn.execute(m.settings_kv.delete().where(m.settings_kv.c.key == f"profile:{comp['id']}"))
        conn.execute(m.settings_kv.insert().values(key=f"profile:{comp['id']}", value=d, updated_at=now()))
        return {"status": "ok", "missing": res.missing}

    def do_prices(conn, issues):
        p = src["prices"]
        res = p.get_prices(sec)
        if not res.ok:
            archive_error(conn, source_key=f"{p.name}:prices:{sec.symbol}", provider=p.name, error=res.error or res.status, company_id=comp["id"])
            issues.add("fetch_error", "prices.history", res.error or res.status, "error")
            return {"status": res.status, "error": res.error}
        raw = _raw_ids(conn, res, p.name, comp["id"])
        raw_id = next(iter(raw.values()), None)
        frame = res.data
        c = m.prices_daily.c
        existing = {r.date: (r.id, r.close) for r in conn.execute(select(c.id, c.date, c.close).where((c.security_id == comp["security_id"]) & (c.provider == p.name)))}
        new_rows, changed = [], 0
        for r in frame.itertuples(index=False):
            if r.close is None or (isinstance(r.close, float) and math.isnan(r.close)):
                continue
            vals = dict(open=_f(r.open), high=_f(r.high), low=_f(r.low), close=_f(r.close), adj_close=_f(r.adj_close), volume=_f(r.volume))
            if r.date in existing:
                pid, old = existing[r.date]
                if old is not None and not math.isclose(old, vals["close"], rel_tol=1e-6):
                    conn.execute(m.prices_daily.update().where(c.id == pid).values(raw_source_id=raw_id, **vals))
                    changed += 1
                continue
            new_rows.append(dict(security_id=comp["security_id"], date=r.date, provider=p.name, adjustment="split_adjusted", raw_source_id=raw_id, **vals))
        if new_rows:
            conn.execute(m.prices_daily.insert(), new_rows)
        if changed > 5:
            issues.add("conflict", "prices.readjusted", f"{changed} historical closes changed at {p.name} — usually a split/bonus re-adjustment. Raw versions are kept.", "info")
        acts = 0
        ac = m.corporate_actions.c
        have = {(r.date, r.action) for r in conn.execute(select(ac.date, ac.action).where((ac.security_id == comp["security_id"]) & (ac.provider == p.name)))}
        for r in frame.itertuples(index=False):
            for action, val in (("dividend", getattr(r, "dividend", 0.0)), ("split", getattr(r, "split", 0.0))):
                v = _f(val)
                if v and v > 0 and (r.date, action) not in have:
                    conn.execute(m.corporate_actions.insert().values(security_id=comp["security_id"], date=r.date, action=action, value=v,
                                                                     provider=p.name, raw_source_id=raw_id))
                    acts += 1
        last = frame["date"].max()
        lag = (datetime.now(UTC).date() - last).days
        if lag > STALE_PRICE_DAYS:
            issues.add("stale", "prices.last_date", f"Latest price is from {last} ({lag} days old)")
        return {"status": "ok", "new_bars": len(new_rows), "changed_bars": changed, "new_actions": acts, "last_date": str(last)}

    def do_financials(conn, issues):
        p = src["financials"]
        res = p.get_financials(sec)
        if not res.ok:
            archive_error(conn, source_key=f"{p.name}:financials:{sec.symbol}", provider=p.name, error=res.error or res.status, company_id=comp["id"])
            issues.add("fetch_error", "financials.statements", res.error or res.status, "error")
            return {"status": res.status, "error": res.error}
        raw = _raw_ids(conn, res, p.name, comp["id"])
        counts = {"inserted": 0, "unchanged": 0, "restated": 0}
        for f in res.data:
            _, action = upsert_fact(conn, comp["id"], f, p.name, raw.get(f.raw_key or ""), issues)
            counts[action] += 1
        for miss in res.missing:
            issues.add("missing", f"financials.{miss}", f"{p.name} has no '{miss}' line for this company", "info")
        for note in res.notes:
            if "not INR" in note:
                issues.add("unsupported", "financials.currency", note, "warn")
        fy = [f.period_end for f in res.data if f.period_type == "FY"]
        if fy and (datetime.now(UTC).date() - max(fy)).days > STALE_FY_DAYS:
            issues.add("stale", "financials.latest_fy", f"Latest annual statement is for {max(fy)}")
        return {"status": "ok", **counts, "missing": res.missing}

    def do_announcements(conn, issues):
        p = src["announcements"]
        res = p.get_announcements(sec)
        if res.status == ERROR:
            archive_error(conn, source_key=f"{p.name}:announcements:{sec.symbol}", provider=p.name, error=res.error or "", company_id=comp["id"])
            issues.add("fetch_error", "announcements.feed", res.error or "error")
            return {"status": "error", "error": res.error}
        raw = _raw_ids(conn, res, p.name, comp["id"])
        a = m.announcements.c
        new = 0
        for rec in res.data or []:
            exists = conn.execute(select(a.id).where((a.exchange == "NSE") & (a.external_id == rec.external_id))).first()
            if exists:
                continue
            conn.execute(m.announcements.insert().values(
                company_id=comp["id"], exchange="NSE", external_id=rec.external_id, category=rec.category,
                event_type=classify_event(rec.category, rec.text), text=rec.text, published_at=rec.published_at,
                attachment_url=rec.attachment_url, raw_source_id=raw.get(rec.raw_key or ""), retrieved_at=now()))
            new += 1
        return {"status": res.status, "new": new, "seen": len(res.data or [])}

    def do_documents(conn, issues):
        p = src["documents"]
        a = m.announcements.c
        pending = conn.execute(select(a.id, a.attachment_url, a.category, a.text, a.published_at, a.event_type)
                               .where((a.company_id == comp["id"]) & a.document_id.is_(None) & a.attachment_url.is_not(None) & a.event_type.in_(ATTACHMENT_TYPES))
                               .order_by(a.published_at.desc()).limit(MAX_ATTACHMENTS_PER_RUN)).all()
        stored = 0
        for row in pending:
            res = p.fetch(row.attachment_url)
            if not res.ok:
                issues.add("fetch_error", f"documents.{row.id}", res.error or "fetch failed", "info")
                continue
            title = (row.text or row.category or "Announcement")[:280]
            doc_id, _ = store_document(conn, content=res.data, title=title, company_id=comp["id"], url=row.attachment_url,
                                       provider=p.name, source_key=res.raw[0].source_key if res.raw else f"{p.name}:file:{row.attachment_url}",
                                       published_at=row.published_at, doc_type=classify_doc(f"{row.category} {row.text}"))
            conn.execute(m.announcements.update().where(a.id == row.id).values(document_id=doc_id))
            d = conn.execute(select(m.documents.c.text_status).where(m.documents.c.id == doc_id)).scalar()
            if d != "ok":
                issues.add("parse_error", f"documents.{doc_id}", f"No extractable text ({d}) — may be a scanned PDF", "info")
            stored += 1
        return {"status": "ok", "documents": stored}

    step("metadata", do_metadata)
    step("prices", do_prices)
    step("financials", do_financials)
    step("announcements", do_announcements)
    if fetch_documents:
        step("documents", do_documents)

    statuses = {s.get("status") for s in steps.values()}
    status = "ok" if statuses <= {"ok", "missing"} else ("error" if statuses == {"error"} else "partial")
    with tx() as conn:
        conn.execute(m.ingestion_runs.update().where(m.ingestion_runs.c.id == run_id).values(finished_at=now(), status=status,
                                                                                           detail={"symbol": comp["symbol"], "steps": steps}))
    return {"run_id": run_id, "symbol": comp["symbol"], "status": status, "steps": steps}


def _f(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


# ── news ─────────────────────────────────────────────────────────────────────

def ingest_news(providers: Providers | None = None) -> dict:
    providers = providers or Providers()
    p = providers.news()
    with tx() as conn:
        run_id = conn.execute(m.ingestion_runs.insert().values(kind="news", started_at=now(), status="running", detail={})).inserted_primary_key[0]
        issues = IssueLog(conn, None, run_id)
        res = p.get_news()
        if res.status == ERROR:
            issues.add("fetch_error", "news.feeds", res.error or "error")
            conn.execute(m.ingestion_runs.update().where(m.ingestion_runs.c.id == run_id).values(finished_at=now(), status="error", detail={"error": res.error}))
            return {"status": "error", "error": res.error}
        raw = _raw_ids(conn, res, p.name, None)
        cands = conn.execute(select(m.companies.c.id, m.companies.c.name, m.securities.c.symbol)
                             .join(m.securities, m.securities.c.company_id == m.companies.c.id)
                             .where(m.companies.c.deep_research.is_(True))).all()
        candidates = [(r.id, company_keys(r.name, r.symbol)) for r in cands]
        n = m.news_items.c
        new = dups = 0
        for item in res.data:
            if conn.execute(select(n.id).where(n.url == item.url)).first():
                continue
            group = dup_group(item.headline)
            if conn.execute(select(n.id).where(n.dup_group == group)).first():
                dups += 1
            cid, method = match_company(f"{item.headline} {item.summary or ''}", candidates)
            conn.execute(m.news_items.insert().values(company_id=cid, headline=item.headline, publisher=item.publisher, url=item.url,
                                                      published_at=item.published_at, retrieved_at=now(), summary=item.summary, feed=item.feed,
                                                      dup_group=group, match_method=method, raw_source_id=raw.get(item.raw_key or "")))
            new += 1
        for note in res.notes:
            issues.add("fetch_error", f"news.{note.split(':')[0][:60]}", note, "info")
        detail = {"new": new, "duplicates": dups, "seen": len(res.data)}
        conn.execute(m.ingestion_runs.update().where(m.ingestion_runs.c.id == run_id).values(finished_at=now(), status="ok", detail=detail))
    return {"status": "ok", **detail}


# ── manual import ────────────────────────────────────────────────────────────

def import_facts(content: bytes, filename: str) -> dict:
    from app.research.providers.manual import parse_fact_file

    rows, errors = parse_fact_file(content, filename)
    counts = {"inserted": 0, "unchanged": 0, "restated": 0, "unknown_symbol": 0}
    with tx() as conn:
        run_id = conn.execute(m.ingestion_runs.insert().values(kind="manual_import", started_at=now(), status="running", detail={"file": filename})).inserted_primary_key[0]
        raw_id, _ = archive(conn, source_key=f"manual:{filename}", provider="manual", content=content, content_type="text/csv")
        unknown = []
        for r in rows:
            comp = _company(conn, r.symbol)
            if comp is None:
                counts["unknown_symbol"] += 1
                unknown.append(r.symbol)
                continue
            issues = IssueLog(conn, comp["id"], run_id)
            _, action = upsert_fact(conn, comp["id"], r.fact, "manual", raw_id, issues, verification="manual")
            counts[action] += 1
            # compare with any provider value for the same period
            c = m.financial_facts.c
            other = conn.execute(select(c.provider, c.value, c.unit).where((c.company_id == comp["id"]) & (c.metric == r.fact.metric) &
                                 (c.period_end == r.fact.period_end) & (c.period_type == r.fact.period_type) & (c.provider != "manual") &
                                 c.is_current.is_(True))).first()
            if other and r.fact.value is not None and other.value is not None:
                from app.research.metrics import to_inr
                a, b = to_inr(r.fact.value, r.fact.unit), to_inr(other.value, other.unit)
                if a is not None and b is not None and b != 0 and abs(a / b - 1) > 0.02:
                    issues.add("conflict", f"financials.{r.fact.metric}:{r.fact.period_end}",
                               f"Manual value {r.fact.value} {r.fact.unit} differs from {other.provider} {other.value} {other.unit} by more than 2%", "warn")
        status = "ok" if not errors else "partial"
        conn.execute(m.ingestion_runs.update().where(m.ingestion_runs.c.id == run_id).values(finished_at=now(), status=status,
                                                                                           detail={"file": filename, **counts, "errors": errors[:50], "unknown": unknown[:50]}))
    return {"status": status, **counts, "errors": errors}


def import_document(content: bytes, filename: str, symbol: str | None, title: str | None) -> dict:
    with tx() as conn:
        comp = _company(conn, symbol) if symbol else None
        if symbol and comp is None:
            raise KeyError(f"{symbol} is not in the company master")
        doc_id, created = store_document(conn, content=content, title=title or filename, company_id=comp["id"] if comp else None,
                                         url=None, provider="manual", source_key=f"manual:pdf:{filename}")
        status = conn.execute(select(m.documents.c.text_status, m.documents.c.pages).where(m.documents.c.id == doc_id)).first()
    return {"document_id": doc_id, "created": created, "text_status": status.text_status, "pages": status.pages}


# ── batch job ────────────────────────────────────────────────────────────────

class Job:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state: dict = {"running": False, "stage": "idle"}

    def get(self) -> dict:
        with self._lock:
            return dict(self.state)

    def _set(self, **kw) -> None:
        with self._lock:
            self.state = {**self.state, **kw}

    def start(self, symbols: list[str], with_news: bool = True) -> bool:
        with self._lock:
            if self.state.get("running"):
                return False
            self.state = {"running": True, "stage": "ingesting", "done": 0, "total": len(symbols), "started_at": now().isoformat(), "results": []}
        threading.Thread(target=self._run, args=(symbols, with_news), daemon=True, name="research-ingest").start()
        return True

    def _run(self, symbols: list[str], with_news: bool) -> None:
        from app.research.engine import build_snapshot

        providers = Providers()
        results = []
        try:
            for i, sym in enumerate(symbols, start=1):
                self._set(current=sym)
                try:
                    r = ingest_company(sym, providers)
                    snap = build_snapshot(sym)
                    results.append({"symbol": sym, "status": r["status"], "snapshot": snap.get("snapshot_id")})
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Research batch failed for %s", sym)
                    results.append({"symbol": sym, "status": "error", "error": str(exc)[:200]})
                self._set(done=i, results=results[-50:])
            if with_news:
                self._set(stage="news", current=None)
                results.append({"symbol": "news", **ingest_news(providers)})
            self._set(running=False, stage="done", finished_at=now().isoformat(), results=results[-60:])
        except Exception as exc:  # noqa: BLE001
            logger.exception("Research batch crashed")
            self._set(running=False, stage="error", error=str(exc), finished_at=now().isoformat())


job = Job()
