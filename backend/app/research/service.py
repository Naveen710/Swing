"""Research platform service layer: seeding, queries for pages, discovery queue, data health."""
from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, func, or_, select

from app.research import models as m
from app.research.db import is_sqlite, now, tx
from app.research.engine import build_snapshot, company_row, latest_snapshot
from app.research.ingest import Providers, ingest_company, ingest_news, upsert_fact, IssueLog
from app.research.master import load_master
from app.research.providers.fixtures import FixtureProvider, SPECS

logger = logging.getLogger(__name__)
_seed_lock = threading.Lock()


def ensure_master() -> dict:
    with tx() as conn:
        n = conn.execute(select(func.count()).select_from(m.companies)).scalar()
    if n:
        return {"loaded": False, "companies": n}
    with _seed_lock:
        with tx() as conn:
            if conn.execute(select(func.count()).select_from(m.companies)).scalar():
                return {"loaded": False}
            res = load_master(conn)
    return {"loaded": True, **res}


def seed_samples(verify: bool = True) -> dict:
    """Ingest all 20 fictional sample companies, apply the restatement case, pull sample news,
    build snapshots and run the sample verification cases. Safe to call repeatedly."""
    ensure_master()
    providers = Providers(demo=True)
    out = []
    for s in SPECS:
        r = ingest_company(s.symbol, providers)
        out.append({"symbol": s.symbol, "status": r["status"]})
    # restatement: SMPTEXT's FY26 annual report restates FY25. Recorded as a manual import (a
    # different source), so the original provider figure is kept and the corrected one wins.
    with tx() as conn:
        comp = company_row(conn, "SMPTEXT")
        run_id = conn.execute(m.ingestion_runs.insert().values(kind="manual_import", company_id=comp["id"], started_at=now(), status="ok",
                                                               finished_at=now(), detail={"symbol": "SMPTEXT", "note": "restated FY25 figures from the FY26 annual report"})).inserted_primary_key[0]
        issues = IssueLog(conn, comp["id"], run_id)
        for f in FixtureProvider().restated("SMPTEXT"):
            _, action = upsert_fact(conn, comp["id"], f, "manual", None, issues, verification="manual")
            if action == "inserted":
                issues.add("conflict", f"financials.{f.metric}:{f.period_end}",
                           f"FY25 {f.metric} restated in the FY26 annual report; the provider still shows the original figure (both kept)", "info")
    news = ingest_news(providers)
    snaps = [build_snapshot(s.symbol)["status"] for s in SPECS]
    ver = None
    if verify:
        from app.research.verify import HERE, run

        ver = run((HERE / "sample_cases.csv").read_bytes(), "sample_cases.csv")["summary"]
        snaps = [build_snapshot(s.symbol)["status"] for s in SPECS]    # pick up verified-fact counts
    return {"companies": out, "news": news, "statuses": dict(zip([s.symbol for s in SPECS], snaps)), "verification": ver}


# ── helpers ──────────────────────────────────────────────────────────────────

def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _row(r) -> dict:
    return {k: _iso(v) for k, v in dict(r).items()}


def _company_or_404(conn, symbol: str):
    comp = company_row(conn, symbol)
    if comp is None:
        raise KeyError(f"{symbol.upper()} is not in the company master")
    return comp


def last_price(conn, security_id: int) -> tuple[float | None, str | None]:
    p = m.prices_daily.c
    r = conn.execute(select(p.close, p.date).where(p.security_id == security_id).order_by(p.date.desc()).limit(1)).first()
    return (r.close, str(r.date)) if r else (None, None)


def freshness(conn, comp) -> dict:
    price, pdate = last_price(conn, comp["security_id"])
    f = m.financial_facts.c
    fy = conn.execute(select(func.max(f.period_end)).where((f.company_id == comp["id"]) & (f.period_type == "FY") & f.is_current.is_(True))).scalar()
    q = conn.execute(select(func.max(f.period_end)).where((f.company_id == comp["id"]) & (f.period_type == "Q") & f.is_current.is_(True))).scalar()
    r = m.ingestion_runs.c
    run = conn.execute(select(r.finished_at, r.status).where((r.company_id == comp["id"]) & (r.kind == "company")).order_by(r.id.desc()).limit(1)).first()
    today = datetime.now(UTC).date()
    lag = (today - date.fromisoformat(pdate)).days if pdate else None
    return {"last_price": price, "last_price_date": pdate, "price_age_days": lag, "price_stale": lag is None or lag > 6,
            "latest_fy": _iso(fy), "latest_quarter": _iso(q), "fy_stale": fy is None or (today - fy).days > 500,
            "last_ingest": _iso(run.finished_at) if run else None, "last_ingest_status": run.status if run else None}


# ── companies / explorer ─────────────────────────────────────────────────────

def search_companies(q: str | None = None, sector: str | None = None, scope: str = "all", status: str | None = None,
                     coverage: str | None = None, limit: int = 50, offset: int = 0, segment: str | None = None) -> dict:
    ensure_master()
    c, s = m.companies.c, m.securities.c
    snap = m.research_snapshots.c
    latest = select(snap.company_id, func.max(snap.id).label("sid")).group_by(snap.company_id).subquery()
    facts = select(m.financial_facts.c.company_id, func.count().label("n")).where(m.financial_facts.c.is_current.is_(True)).group_by(m.financial_facts.c.company_id).subquery()
    query = (select(c.id, c.name, c.isin, c.sector, c.industry, c.is_sample, c.deep_research, c.status, c.is_financial, s.symbol, s.exchange,
                    m.research_snapshots.c.status.label("research_status"), m.research_snapshots.c.as_of, func.coalesce(facts.c.n, 0).label("facts"))
             .join(m.securities, (s.company_id == c.id) & (s.status == "active"))
             .outerjoin(latest, latest.c.company_id == c.id).outerjoin(m.research_snapshots, m.research_snapshots.c.id == latest.c.sid)
             .outerjoin(facts, facts.c.company_id == c.id))
    conds = []
    if q:
        term = q.strip()
        if is_sqlite() and len(term) >= 2 and term.replace(" ", "").isalnum():
            with tx() as conn:
                hit_ids = [r[0] for r in conn.exec_driver_sql("SELECT rowid FROM rs_companies_fts WHERE rs_companies_fts MATCH ?",
                                                              (" ".join(w + "*" for w in term.split()),)).all()]
            conds.append(or_(c.id.in_(hit_ids or [-1]), s.symbol.ilike(f"{term}%")))
        else:
            conds.append(or_(c.name.ilike(f"%{term}%"), s.symbol.ilike(f"{term}%"), c.isin == term.upper()))
    if sector:
        conds.append(c.sector == sector)
    if scope == "deep":
        conds.append(c.deep_research.is_(True) & c.is_sample.is_(False))
    elif scope == "sample":
        conds.append(c.is_sample.is_(True))
    elif scope == "researched":
        conds.append(latest.c.sid.is_not(None))
    if status:
        conds.append(m.research_snapshots.c.status == status)
    if segment:
        from app.research.segments import check_segment, ensure_segments

        ensure_segments()
        im = m.index_members.c
        conds.append(c.id.in_(select(im.company_id).where(im.segment == check_segment(segment))))
    if coverage == "with_data":
        conds.append(facts.c.n > 0)
    elif coverage == "no_data":
        conds.append(facts.c.n.is_(None))
    if conds:
        query = query.where(and_(*conds))
    with tx() as conn:
        total = conn.execute(select(func.count()).select_from(query.subquery())).scalar()
        rows = conn.execute(query.order_by(c.deep_research.desc(), facts.c.n.desc().nulls_last() if not is_sqlite() else facts.c.n.desc(), c.name)
                            .limit(min(limit, 200)).offset(offset)).mappings().all()
    return {"total": total, "items": [_row(r) for r in rows]}


def sectors() -> list[dict]:
    ensure_master()
    c = m.companies.c
    with tx() as conn:
        rows = conn.execute(select(c.sector, func.count()).where(c.sector.is_not(None)).group_by(c.sector).order_by(c.sector)).all()
    return [{"sector": r[0], "companies": r[1]} for r in rows]


def company_detail(symbol: str) -> dict:
    with tx() as conn:
        comp = _company_or_404(conn, symbol)
        snap = latest_snapshot(conn, comp["id"])
        s = m.research_snapshots.c
        history = conn.execute(select(s.id, s.as_of, s.status, s.dimensions, s.created_at, s.code_version, s.model_version, s.output_hash)
                               .where(s.company_id == comp["id"]).order_by(s.id.desc()).limit(30)).mappings().all()
        secs = conn.execute(select(m.securities).where(m.securities.c.company_id == comp["id"])).mappings().all()
        ih = conn.execute(select(m.identifier_history).where(m.identifier_history.c.security_id.in_([x["id"] for x in secs]))).mappings().all()
        issues = conn.execute(select(m.data_issues).where((m.data_issues.c.company_id == comp["id"]) & (m.data_issues.c.status == "open"))
                              .order_by(m.data_issues.c.severity.desc())).mappings().all()
        profile = conn.execute(select(m.settings_kv.c.value).where(m.settings_kv.c.key == f"profile:{comp['id']}")).scalar() or {}
        wl = conn.execute(select(m.watchlists.c.id, m.watchlists.c.name).join(m.watchlist_items, m.watchlist_items.c.watchlist_id == m.watchlists.c.id)
                          .where((m.watchlist_items.c.company_id == comp["id"]) & m.watchlist_items.c.removed_at.is_(None))).all()
        dec = conn.execute(select(func.count()).select_from(m.decisions).where(m.decisions.c.company_id == comp["id"])).scalar()
        fresh = freshness(conn, comp)
    return {"company": {k: _iso(v) for k, v in comp.items()}, "securities": [_row(x) for x in secs], "identifier_history": [_row(x) for x in ih],
            "profile": {k: profile.get(k) for k in ("longBusinessSummary", "website", "fullTimeEmployees", "marketCap", "heldPercentInsiders", "heldPercentInstitutions") if profile.get(k) is not None},
            "freshness": fresh, "snapshot": None if snap is None else {"id": snap["id"], "created_at": _iso(snap["created_at"]), "output": snap["output"],
                                                                       "input_hash": snap["input_hash"], "output_hash": snap["output_hash"], "model_version": snap["model_version"]},
            "history": [_row(h) for h in history], "issues": [_row(i) for i in issues], "watchlists": [{"id": w.id, "name": w.name} for w in wl],
            "decisions": dec}


def company_prices(symbol: str, days: int = 400) -> dict:
    with tx() as conn:
        comp = _company_or_404(conn, symbol)
        p = m.prices_daily.c
        rows = conn.execute(select(p.date, p.open, p.high, p.low, p.close, p.volume, p.provider).where(p.security_id == comp["security_id"])
                            .order_by(p.date.desc()).limit(min(days, 2000))).all()
        a = m.corporate_actions.c
        acts = conn.execute(select(a.date, a.action, a.value, a.provider).where(a.security_id == comp["security_id"]).order_by(a.date)).all()
    rows = sorted(rows, key=lambda r: r.date)
    return {"symbol": comp["symbol"], "provider": rows[-1].provider if rows else None,
            "adjustment": "Close is split-adjusted, not dividend-adjusted", "bars": [[str(r.date), r.open, r.high, r.low, r.close, r.volume] for r in rows],
            "actions": [{"date": str(x.date), "action": x.action, "value": x.value, "provider": x.provider} for x in acts]}


def company_facts(symbol: str) -> dict:
    with tx() as conn:
        comp = _company_or_404(conn, symbol)
        f = m.financial_facts.c
        rows = conn.execute(select(m.financial_facts).where(f.company_id == comp["id"]).order_by(f.metric, f.period_type, f.period_end, f.version)).mappings().all()
    return {"symbol": comp["symbol"], "facts": [_row(r) for r in rows]}


def snapshot(snapshot_id: int) -> dict:
    with tx() as conn:
        r = conn.execute(select(m.research_snapshots).where(m.research_snapshots.c.id == snapshot_id)).mappings().first()
    if not r:
        raise KeyError(snapshot_id)
    return {k: _iso(v) for k, v in dict(r).items()}


# ── discovery ────────────────────────────────────────────────────────────────

DIM_KEYS = ["business_quality", "growth_inflection", "financial_strength", "valuation", "governance_risk", "data_confidence", "recognition_gap"]


def discovery(sort: str = "growth_inflection", status: str | None = None, sector: str | None = None, include_sample: bool = True,
              min_confidence: float | None = None) -> dict:
    s = m.research_snapshots.c
    latest = select(s.company_id, func.max(s.id).label("sid")).group_by(s.company_id).subquery()
    with tx() as conn:
        rows = conn.execute(select(m.research_snapshots.c.id, m.research_snapshots.c.as_of, m.research_snapshots.c.status, m.research_snapshots.c.dimensions,
                                   m.research_snapshots.c.flags, m.companies.c.name, m.companies.c.sector, m.companies.c.is_sample, m.companies.c.is_financial,
                                   m.securities.c.symbol)
                            .join(latest, latest.c.sid == m.research_snapshots.c.id)
                            .join(m.companies, m.companies.c.id == m.research_snapshots.c.company_id)
                            .join(m.securities, (m.securities.c.company_id == m.companies.c.id) & (m.securities.c.status == "active"))).mappings().all()
    items = []
    for r in rows:
        if not include_sample and r["is_sample"]:
            continue
        dims = r["dimensions"] or {}
        if min_confidence is not None and (dims.get("data_confidence") or 0) < min_confidence:
            continue
        flags = r["flags"] or []
        items.append({"snapshot_id": r["id"], "symbol": r["symbol"], "name": r["name"], "sector": r["sector"], "is_sample": r["is_sample"],
                      "is_financial": r["is_financial"], "as_of": _iso(r["as_of"]), "status": r["status"], "dimensions": dims,
                      "flags": flags[:4], "flag_counts": {lvl: sum(1 for f in flags if f["level"] == lvl) for lvl in ("blocker", "review", "watch")}})
    # sector-relative percentiles where a sector has ≥ 3 researched companies, else across everything
    for key in DIM_KEYS:
        by_sector: dict[str, list[float]] = {}
        for it in items:
            v = it["dimensions"].get(key)
            if v is not None:
                by_sector.setdefault(it["sector"] or "Unknown", []).append(v)
        everything = [v for vs in by_sector.values() for v in vs]
        for it in items:
            v = it["dimensions"].get(key)
            peers = by_sector.get(it["sector"] or "Unknown", [])
            pool, basis = (peers, "sector") if len(peers) >= 3 else (everything, "all")
            it.setdefault("percentiles", {})[key] = None if v is None or not pool else round(sum(1 for x in pool if x <= v) / len(pool) * 100)
            it.setdefault("percentile_basis", {})[key] = basis
    totals = {st: sum(1 for i in items if i["status"] == st) for st in ("ELIGIBLE_FOR_RESEARCH", "REVIEW_REQUIRED", "BLOCKED")}
    if status:
        items = [i for i in items if i["status"] == status]
    if sector:
        items = [i for i in items if i["sector"] == sector]
    key = sort if sort in DIM_KEYS else "growth_inflection"
    status_rank = {"ELIGIBLE_FOR_RESEARCH": 0, "REVIEW_REQUIRED": 1, "BLOCKED": 2}
    items.sort(key=lambda i: (status_rank[i["status"]], -(i["dimensions"].get(key) if i["dimensions"].get(key) is not None else -1), i["symbol"]))
    return {"sort": key, "items": items, "totals": totals, "dimensions": DIM_KEYS,
            "note": "A research queue, not a buy list. Dimensions are transparent rules and are not combined into one score."}


# ── news / events ────────────────────────────────────────────────────────────

def news(symbol: str | None = None, q: str | None = None, limit: int = 60, include_duplicates: bool = False, matched_only: bool = False) -> dict:
    n = m.news_items.c
    query = select(m.news_items, m.securities.c.symbol, m.companies.c.name.label("company_name"), m.companies.c.is_sample).outerjoin(
        m.companies, m.companies.c.id == n.company_id).outerjoin(m.securities, (m.securities.c.company_id == m.companies.c.id) & (m.securities.c.status == "active"))
    with tx() as conn:
        if symbol:
            comp = _company_or_404(conn, symbol)
            query = query.where(n.company_id == comp["id"])
        if matched_only:
            query = query.where(n.company_id.is_not(None))
        if q:
            if is_sqlite() and q.replace(" ", "").isalnum():
                ids = [r[0] for r in conn.exec_driver_sql("SELECT rowid FROM rs_news_fts WHERE rs_news_fts MATCH ?", (" ".join(w + "*" for w in q.split()),)).all()]
                query = query.where(n.id.in_(ids or [-1]))
            else:
                query = query.where(n.headline.ilike(f"%{q}%"))
        rows = conn.execute(query.order_by(n.published_at.desc().nulls_last() if not is_sqlite() else n.published_at.desc()).limit(min(limit, 300) * 2)).mappings().all()
    seen: dict[str, dict] = {}
    out = []
    for r in rows:
        item = _row(r)
        if r["dup_group"] in seen:
            seen[r["dup_group"]].setdefault("duplicates", []).append({"publisher": r["publisher"], "url": r["url"], "published_at": item["published_at"]})
            if not include_duplicates:
                continue
        else:
            seen[r["dup_group"]] = item
        out.append(item)
        if len(out) >= limit:
            break
    return {"items": out, "note": "Headlines are leads, not verified facts. Open the source and the company's filings before relying on them."}


def announcements(symbol: str | None = None, event_type: str | None = None, limit: int = 60) -> dict:
    a = m.announcements.c
    query = select(m.announcements, m.securities.c.symbol, m.companies.c.name.label("company_name"), m.companies.c.is_sample).join(
        m.companies, m.companies.c.id == a.company_id).join(m.securities, (m.securities.c.company_id == m.companies.c.id) & (m.securities.c.status == "active"))
    with tx() as conn:
        if symbol:
            query = query.where(a.company_id == _company_or_404(conn, symbol)["id"])
        if event_type:
            query = query.where(a.event_type == event_type)
        rows = conn.execute(query.order_by(a.published_at.desc()).limit(min(limit, 300))).mappings().all()
    return {"items": [_row(r) for r in rows]}


# ── evidence ─────────────────────────────────────────────────────────────────

def _raw_meta(conn, raw_id):
    if raw_id is None:
        return None
    r = conn.execute(select(*[c for c in m.raw_sources.c if c.name != "content"]).where(m.raw_sources.c.id == raw_id)).mappings().first()
    return _row(r) if r else None


def evidence(ref: str, snapshot_id: int | None = None) -> dict:
    kind, _, ident = ref.partition(":")
    with tx() as conn:
        if kind == "fact":
            r = conn.execute(select(m.financial_facts).where(m.financial_facts.c.id == int(ident))).mappings().first()
            if not r:
                raise KeyError(ref)
            versions = conn.execute(select(m.financial_facts).where((m.financial_facts.c.company_id == r["company_id"]) & (m.financial_facts.c.metric == r["metric"]) &
                                    (m.financial_facts.c.period_end == r["period_end"]) & (m.financial_facts.c.period_type == r["period_type"]))
                                    .order_by(m.financial_facts.c.version)).mappings().all()
            return {"kind": "fact", "ref": ref, "fact": _row(r), "versions": [_row(v) for v in versions], "source": _raw_meta(conn, r["raw_source_id"])}
        if kind == "ann":
            r = conn.execute(select(m.announcements).where(m.announcements.c.id == int(ident))).mappings().first()
            if not r:
                raise KeyError(ref)
            doc = conn.execute(select(m.documents).where(m.documents.c.id == r["document_id"])).mappings().first() if r["document_id"] else None
            return {"kind": "announcement", "ref": ref, "announcement": _row(r), "document": _row(doc) if doc else None, "source": _raw_meta(conn, r["raw_source_id"])}
        if kind == "chunk":
            ch = conn.execute(select(m.chunks).where(m.chunks.c.id == int(ident))).mappings().first()
            if not ch:
                raise KeyError(ref)
            doc = conn.execute(select(m.documents).where(m.documents.c.id == ch["document_id"])).mappings().first()
            return {"kind": "chunk", "ref": ref, "chunk": _row(ch), "document": _row(doc), "source": _raw_meta(conn, doc["raw_source_id"])}
        if kind == "news":
            r = conn.execute(select(m.news_items).where(m.news_items.c.id == int(ident))).mappings().first()
            if not r:
                raise KeyError(ref)
            return {"kind": "news", "ref": ref, "news": _row(r), "source": _raw_meta(conn, r["raw_source_id"])}
        if kind == "raw":
            meta = _raw_meta(conn, int(ident))
            if not meta:
                raise KeyError(ref)
            return {"kind": "raw", "ref": ref, "source": meta}
        if kind in ("metric", "check", "valuation", "dimension"):
            if snapshot_id is None:
                raise KeyError(f"{ref} needs a snapshot id")
            snap = conn.execute(select(m.research_snapshots.c.output).where(m.research_snapshots.c.id == snapshot_id)).scalar()
            if snap is None:
                raise KeyError(snapshot_id)
            if kind == "metric":
                item = snap["metrics"].get(ident)
                facts = [evidence(f"fact:{i}") for i in (item or {}).get("inputs", [])]
                return {"kind": "metric", "ref": ref, "metric": item, "inputs": facts}
            if kind == "check":
                item = next((c for c in snap["checks"] if c["key"] == ident), None)
                return {"kind": "check", "ref": ref, "check": item, "metrics": {k: snap["metrics"].get(k) for k in (item or {}).get("metrics", [])}}
            if kind == "valuation":
                return {"kind": "valuation", "ref": ref, "valuation": snap["valuation"], "part": ident}
            return {"kind": "dimension", "ref": ref, "dimension": next((d for d in snap["dimensions"] if d["key"] == ident), None)}
    raise KeyError(ref)


def raw_content(raw_id: int) -> tuple[dict, bytes | None]:
    from app.research.archive import read_content
    with tx() as conn:
        return read_content(conn, raw_id)


def document(doc_id: int) -> dict:
    with tx() as conn:
        d = conn.execute(select(m.documents).where(m.documents.c.id == doc_id)).mappings().first()
        if not d:
            raise KeyError(doc_id)
        chunks = conn.execute(select(m.chunks).where(m.chunks.c.document_id == doc_id).order_by(m.chunks.c.page, m.chunks.c.seq)).mappings().all()
        comp = conn.execute(select(m.companies.c.name, m.securities.c.symbol).join(m.securities, m.securities.c.company_id == m.companies.c.id)
                            .where(m.companies.c.id == d["company_id"])).first() if d["company_id"] else None
        llm_rows = conn.execute(select(m.llm_cache.c.output, m.llm_cache.c.model, m.llm_cache.c.created_at).where(
            (m.llm_cache.c.task == "extract") & (m.llm_cache.c.input_hash == d["sha256"]))).all()
    return {"document": _row(d), "company": {"name": comp.name, "symbol": comp.symbol} if comp else None, "chunks": [_row(c) for c in chunks],
            "extractions": [{"model": r.model, "created_at": _iso(r.created_at), **(r.output or {})} for r in llm_rows]}


def search(q: str, limit: int = 20) -> dict:
    out = {"companies": search_companies(q=q, limit=limit)["items"], "passages": [], "news": news(q=q, limit=limit)["items"]}
    with tx() as conn:
        if is_sqlite() and q.replace(" ", "").isalnum():
            rows = conn.exec_driver_sql("SELECT c.id, c.document_id, c.page, snippet(rs_chunks_fts, 0, '[', ']', '…', 16) FROM rs_chunks_fts "
                                        "JOIN rs_chunks c ON c.id = rs_chunks_fts.rowid WHERE rs_chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                                        (" ".join(w + "*" for w in q.split()), limit)).all()
            out["passages"] = [{"chunk_id": r[0], "document_id": r[1], "page": r[2], "snippet": r[3]} for r in rows]
        else:
            rows = conn.execute(select(m.chunks.c.id, m.chunks.c.document_id, m.chunks.c.page, m.chunks.c.text).where(m.chunks.c.text.ilike(f"%{q}%")).limit(limit)).all()
            out["passages"] = [{"chunk_id": r.id, "document_id": r.document_id, "page": r.page, "snippet": r.text[:200]} for r in rows]
    return out


# ── watchlists ───────────────────────────────────────────────────────────────

def watchlists() -> list[dict]:
    w, i = m.watchlists.c, m.watchlist_items.c
    with tx() as conn:
        lists = conn.execute(select(m.watchlists).order_by(w.id)).mappings().all()
        items = conn.execute(select(m.watchlist_items, m.companies.c.name, m.companies.c.is_sample, m.securities.c.symbol, m.securities.c.id.label("security_id"))
                             .join(m.companies, m.companies.c.id == i.company_id)
                             .join(m.securities, (m.securities.c.company_id == m.companies.c.id) & (m.securities.c.status == "active"))
                             .where(i.removed_at.is_(None)).order_by(i.added_at)).mappings().all()
        enriched = []
        for it in items:
            price, pdate = last_price(conn, it["security_id"])
            latest = latest_snapshot(conn, it["company_id"])
            added = conn.execute(select(m.research_snapshots.c.dimensions, m.research_snapshots.c.status).where(m.research_snapshots.c.id == it["added_snapshot_id"])).first() if it["added_snapshot_id"] else None
            dims_now = (latest["dimensions"] if latest else {}) or {}
            dims_then = (added.dimensions if added else {}) or {}
            changes = {k: round(dims_now[k] - dims_then[k], 1) for k in DIM_KEYS if dims_now.get(k) is not None and dims_then.get(k) is not None and abs(dims_now[k] - dims_then[k]) >= 0.5}
            enriched.append({"id": it["id"], "watchlist_id": it["watchlist_id"], "symbol": it["symbol"], "name": it["name"], "is_sample": it["is_sample"],
                             "reason": it["reason"], "added_at": _iso(it["added_at"]), "added_price": it["added_price"], "price": price, "price_date": pdate,
                             "price_change_pct": round((price / it["added_price"] - 1) * 100, 2) if price and it["added_price"] else None,
                             "status_then": added.status if added else None, "status_now": latest["status"] if latest else None,
                             "dimension_changes": changes, "dimensions_now": dims_now})
    return [{**_row(l), "items": [x for x in enriched if x["watchlist_id"] == l["id"]]} for l in lists]


def create_watchlist(name: str, description: str | None) -> dict:
    with tx() as conn:
        wid = conn.execute(m.watchlists.insert().values(name=name.strip()[:120], description=description, created_at=now())).inserted_primary_key[0]
    return {"id": wid}


def update_watchlist(wid: int, name: str | None, description: str | None) -> None:
    vals = {k: v for k, v in (("name", name), ("description", description)) if v is not None}
    with tx() as conn:
        if not conn.execute(select(m.watchlists.c.id).where(m.watchlists.c.id == wid)).first():
            raise KeyError(wid)
        if vals:
            conn.execute(m.watchlists.update().where(m.watchlists.c.id == wid).values(**vals))


def delete_watchlist(wid: int) -> None:
    with tx() as conn:
        if not conn.execute(m.watchlists.delete().where(m.watchlists.c.id == wid)).rowcount:
            raise KeyError(wid)


def add_to_watchlist(wid: int, symbol: str, reason: str | None) -> dict:
    with tx() as conn:
        if not conn.execute(select(m.watchlists.c.id).where(m.watchlists.c.id == wid)).first():
            raise KeyError(wid)
        comp = _company_or_404(conn, symbol)
        i = m.watchlist_items.c
        if conn.execute(select(i.id).where((i.watchlist_id == wid) & (i.company_id == comp["id"]) & i.removed_at.is_(None))).first():
            raise ValueError(f"{comp['symbol']} is already on this list")
        snap = latest_snapshot(conn, comp["id"])
        price, _ = last_price(conn, comp["security_id"])
        iid = conn.execute(m.watchlist_items.insert().values(watchlist_id=wid, company_id=comp["id"], reason=reason, added_at=now(),
                                                             added_snapshot_id=snap["id"] if snap else None, added_price=price)).inserted_primary_key[0]
    return {"id": iid}


def remove_from_watchlist(wid: int, item_id: int) -> None:
    with tx() as conn:
        if not conn.execute(m.watchlist_items.update().where((m.watchlist_items.c.id == item_id) & (m.watchlist_items.c.watchlist_id == wid)).values(removed_at=now())).rowcount:
            raise KeyError(item_id)


# ── decision journal ─────────────────────────────────────────────────────────

DECISION_FIELDS = ("title", "thesis", "drivers", "valuation_assumptions", "downside_risks", "invalidation", "status", "outcome")


def decisions(symbol: str | None = None) -> list[dict]:
    d = m.decisions.c
    with tx() as conn:
        q = select(m.decisions, m.securities.c.symbol, m.companies.c.name, m.companies.c.is_sample, m.securities.c.id.label("security_id")).join(
            m.companies, m.companies.c.id == d.company_id).join(m.securities, (m.securities.c.company_id == m.companies.c.id) & (m.securities.c.status == "active"))
        if symbol:
            q = q.where(d.company_id == _company_or_404(conn, symbol)["id"])
        rows = conn.execute(q.order_by(d.updated_at.desc())).mappings().all()
        out = []
        for r in rows:
            price, pdate = last_price(conn, r["security_id"])
            item = _row(r)
            item["price_now"], item["price_date"] = price, pdate
            item["change_since_entry_pct"] = round((price / r["price_at_entry"] - 1) * 100, 2) if price and r["price_at_entry"] else None
            out.append(item)
    return out


def create_decision(symbol: str, data: dict) -> dict:
    with tx() as conn:
        comp = _company_or_404(conn, symbol)
        snap = latest_snapshot(conn, comp["id"])
        price, _ = last_price(conn, comp["security_id"])
        ts = now()
        vals = {k: data.get(k) for k in DECISION_FIELDS if data.get(k) is not None}
        vals.setdefault("status", "open")
        did = conn.execute(m.decisions.insert().values(company_id=comp["id"], snapshot_id=snap["id"] if snap else None, price_at_entry=price,
                                                       follow_ups=[], created_at=ts, updated_at=ts, **vals)).inserted_primary_key[0]
    return {"id": did}


def update_decision(did: int, data: dict) -> None:
    vals = {k: data[k] for k in DECISION_FIELDS if k in data}
    if vals.get("status") and vals["status"] not in ("open", "closed", "invalidated"):
        raise ValueError("status must be open, closed or invalidated")
    with tx() as conn:
        if not conn.execute(m.decisions.update().where(m.decisions.c.id == did).values(updated_at=now(), **vals)).rowcount:
            raise KeyError(did)


def delete_decision(did: int) -> None:
    with tx() as conn:
        if not conn.execute(m.decisions.delete().where(m.decisions.c.id == did)).rowcount:
            raise KeyError(did)


def add_follow_up(did: int, note: str, outcome: str | None) -> dict:
    with tx() as conn:
        r = conn.execute(select(m.decisions.c.follow_ups, m.decisions.c.company_id).where(m.decisions.c.id == did)).first()
        if not r:
            raise KeyError(did)
        sec = conn.execute(select(m.securities.c.id).where((m.securities.c.company_id == r.company_id) & (m.securities.c.status == "active"))).scalar()
        price, pdate = last_price(conn, sec)
        snap = latest_snapshot(conn, r.company_id)
        entry = {"date": now().isoformat(), "note": note, "outcome": outcome, "price": price, "price_date": pdate,
                 "snapshot_id": snap["id"] if snap else None, "status": snap["status"] if snap else None}
        conn.execute(m.decisions.update().where(m.decisions.c.id == did).values(follow_ups=list(r.follow_ups or []) + [entry], updated_at=now()))
    return entry


# ── data quality ─────────────────────────────────────────────────────────────

def data_quality(status: str = "open", kind: str | None = None, limit: int = 200) -> dict:
    i, r = m.data_issues.c, m.ingestion_runs.c
    with tx() as conn:
        q = select(m.data_issues, m.securities.c.symbol, m.companies.c.name).outerjoin(m.companies, m.companies.c.id == i.company_id).outerjoin(
            m.securities, (m.securities.c.company_id == m.companies.c.id) & (m.securities.c.status == "active"))
        if status != "all":
            q = q.where(i.status == status)
        if kind:
            q = q.where(i.kind == kind)
        issues = conn.execute(q.order_by(i.last_seen.desc()).limit(min(limit, 1000))).mappings().all()
        counts = conn.execute(select(i.kind, i.severity, func.count()).where(i.status == "open").group_by(i.kind, i.severity)).all()
        runs = conn.execute(select(m.ingestion_runs, m.securities.c.symbol).outerjoin(
            m.securities, (m.securities.c.company_id == r.company_id) & (m.securities.c.status == "active")).order_by(r.id.desc()).limit(60)).mappings().all()
        ver = conn.execute(select(m.verifications).order_by(m.verifications.c.status, m.verifications.c.case_id)).mappings().all()
        rs = m.raw_sources.c
        raw_err = conn.execute(select(rs.id, rs.source_key, rs.provider, rs.retrieved_at, rs.error).where(rs.status == "error").order_by(rs.id.desc()).limit(30)).mappings().all()
        raw_stats = conn.execute(select(rs.provider, func.count(), func.max(rs.retrieved_at)).group_by(rs.provider)).all()
    return {"issues": [_row(x) for x in issues], "counts": [{"kind": c[0], "severity": c[1], "count": c[2]} for c in counts],
            "runs": [_row(x) for x in runs], "verification": [_row(v) for v in ver], "source_errors": [_row(x) for x in raw_err],
            "sources": [{"provider": s[0], "payloads": s[1], "last_retrieved": _iso(s[2])} for s in raw_stats]}


def set_issue_status(issue_id: int, status: str) -> None:
    if status not in ("open", "resolved", "ignored"):
        raise ValueError("status must be open, resolved or ignored")
    with tx() as conn:
        if not conn.execute(m.data_issues.update().where(m.data_issues.c.id == issue_id).values(status=status, resolved_at=now() if status != "open" else None)).rowcount:
            raise KeyError(issue_id)


# ── dashboard ────────────────────────────────────────────────────────────────

def dashboard() -> dict:
    from app.research.engine import index_returns
    from app.research.ingest import demo_mode

    ensure_master()
    disc = discovery()
    candidates = [i for i in disc["items"] if i["status"] == "ELIGIBLE_FOR_RESEARCH"]
    candidates.sort(key=lambda i: -((i["dimensions"].get("growth_inflection") or 0) + (i["dimensions"].get("recognition_gap") or 0)))
    wl = watchlists()
    moves = sorted([x for w in wl for x in w["items"]], key=lambda x: -abs(x["price_change_pct"] or 0))[:8]
    dq = data_quality(limit=1)
    with tx() as conn:
        c = m.companies.c
        totals = {"companies": conn.execute(select(func.count()).select_from(m.companies)).scalar(),
                  "deep": conn.execute(select(func.count()).select_from(m.companies).where(c.deep_research.is_(True) & c.is_sample.is_(False))).scalar(),
                  "researched": conn.execute(select(func.count(func.distinct(m.research_snapshots.c.company_id)))).scalar(),
                  "documents": conn.execute(select(func.count()).select_from(m.documents)).scalar(),
                  "facts": conn.execute(select(func.count()).select_from(m.financial_facts)).scalar()}
        r = m.ingestion_runs.c
        last_run = conn.execute(select(r.kind, r.finished_at, r.status).order_by(r.id.desc()).limit(1)).first()
    return {"market": index_returns(), "demo_mode": demo_mode(), "totals": totals, "candidates": candidates[:6], "watchlist_moves": moves,
            "filings": announcements(limit=12)["items"], "news": news(limit=10, matched_only=True)["items"],
            "data_health": {"counts": dq["counts"], "last_run": {"kind": last_run.kind, "finished_at": _iso(last_run.finished_at), "status": last_run.status} if last_run else None},
            "queue_totals": disc["totals"], "note": "Candidates for review, ranked by growth/inflection and recognition gap among ELIGIBLE companies. Not recommendations."}


# ── settings / backup ────────────────────────────────────────────────────────

def settings_view() -> dict:
    from app.research import llm
    from app.research.auth import protected
    from app.research.db import schema_versions
    from app.research.master import deep_symbols
    from app.research.providers.rss import feeds

    with tx() as conn:
        lc = m.llm_cache.c
        # JSON columns can't be GROUPed on Postgres; the cache is small, so sum in Python.
        usage = [(r[0], 1, r[1]) for r in conn.execute(select(lc.task, lc.usage)).all()]
        prefs = conn.execute(select(m.settings_kv.c.value).where(m.settings_kv.c.key == "preferences")).scalar() or {}
    tokens_in = sum(((u[2] or {}).get("input_tokens", 0) or 0) * u[1] for u in usage)
    tokens_out = sum(((u[2] or {}).get("output_tokens", 0) or 0) * u[1] for u in usage)
    calls = sum(u[1] for u in usage)
    return {"sources": Providers().describe(), "news_feeds": list(feeds()), "deep_symbols": list(deep_symbols()),
            "refresh": "Manual (Refresh buttons) plus the daily cron if you add `python -m app.research.cli ingest-deep` to it.",
            "ai": {**llm.config(), "calls_cached": calls, "input_tokens": tokens_in, "output_tokens": tokens_out},
            "protected": protected(), "schema": schema_versions(), "preferences": prefs,
            "backup": "Download a JSON backup of watchlists, journal and preferences; restore it on any instance."}


def save_preferences(prefs: dict) -> None:
    with tx() as conn:
        conn.execute(m.settings_kv.delete().where(m.settings_kv.c.key == "preferences"))
        conn.execute(m.settings_kv.insert().values(key="preferences", value=prefs, updated_at=now()))


def backup() -> dict:
    """User data only (watchlists, journal, preferences, verification cases). Companies are
    referenced by symbol so a backup restores onto a fresh database."""
    with tx() as conn:
        sym = {r.company_id: r.symbol for r in conn.execute(select(m.securities.c.company_id, m.securities.c.symbol).where(m.securities.c.status == "active"))}
        wls = conn.execute(select(m.watchlists)).mappings().all()
        items = conn.execute(select(m.watchlist_items)).mappings().all()
        decs = conn.execute(select(m.decisions)).mappings().all()
        prefs = conn.execute(select(m.settings_kv.c.value).where(m.settings_kv.c.key == "preferences")).scalar()
        ver = conn.execute(select(m.verifications)).mappings().all()
    return {"format": "swing-research-backup", "version": 1, "created_at": now().isoformat(),
            "watchlists": [{**_row(w), "items": [{**{k: _iso(v) for k, v in i.items() if k not in ("id", "watchlist_id", "company_id", "added_snapshot_id")},
                                                  "symbol": sym.get(i["company_id"])} for i in items if i["watchlist_id"] == w["id"]]} for w in wls],
            "decisions": [{**{k: _iso(v) for k, v in d.items() if k not in ("id", "company_id", "snapshot_id")}, "symbol": sym.get(d["company_id"])} for d in decs],
            "preferences": prefs, "verifications": [{k: _iso(v) for k, v in x.items() if k != "id"} for x in ver]}


def _dt(v):
    if v in (None, ""):
        return None
    return datetime.fromisoformat(v) if isinstance(v, str) else v


def restore(data: dict) -> dict:
    if data.get("format") != "swing-research-backup":
        raise ValueError("Not a research backup file")
    ensure_master()
    skipped: list[str] = []
    with tx() as conn:
        conn.execute(m.watchlist_items.delete())
        conn.execute(m.watchlists.delete())
        conn.execute(m.decisions.delete())
        conn.execute(m.verifications.delete())
        for w in data.get("watchlists", []):
            wid = conn.execute(m.watchlists.insert().values(name=w["name"], description=w.get("description"), created_at=_dt(w.get("created_at")) or now())).inserted_primary_key[0]
            for it in w.get("items", []):
                comp = company_row(conn, it.get("symbol") or "")
                if comp is None:
                    skipped.append(f"watchlist item {it.get('symbol')}")
                    continue
                conn.execute(m.watchlist_items.insert().values(watchlist_id=wid, company_id=comp["id"], reason=it.get("reason"), added_at=_dt(it.get("added_at")),
                                                               added_price=it.get("added_price"), removed_at=_dt(it.get("removed_at"))))
        for d in data.get("decisions", []):
            comp = company_row(conn, d.get("symbol") or "")
            if comp is None:
                skipped.append(f"decision {d.get('symbol')}")
                continue
            conn.execute(m.decisions.insert().values(company_id=comp["id"], price_at_entry=d.get("price_at_entry"), follow_ups=d.get("follow_ups") or [],
                                                     created_at=_dt(d.get("created_at")), updated_at=_dt(d.get("updated_at")),
                                                     **{k: d.get(k) for k in DECISION_FIELDS}))
        for v in data.get("verifications", []):
            conn.execute(m.verifications.insert().values(**{**v, "checked_at": _dt(v.get("checked_at"))}))
        if data.get("preferences") is not None:
            conn.execute(m.settings_kv.delete().where(m.settings_kv.c.key == "preferences"))
            conn.execute(m.settings_kv.insert().values(key="preferences", value=data["preferences"], updated_at=now()))
    return {"restored": True, "watchlists": len(data.get("watchlists", [])), "decisions": len(data.get("decisions", [])), "skipped": skipped}


# ── market-cap segments ──────────────────────────────────────────────────────

SEGMENT_METRICS = ("market_cap", "pe", "pb", "roe", "roce", "revenue_growth", "profit_growth", "debt_equity", "ret_1y")


def _segment_rows(conn, seg: str):
    im, c, s = m.index_members.c, m.companies.c, m.research_snapshots.c
    r = m.ingestion_runs.c
    latest = select(s.company_id, func.max(s.id).label("sid")).group_by(s.company_id).subquery()
    runs = (select(r.company_id, func.max(r.finished_at).label("last"))
            .where((r.kind == "company") & r.status.in_(("ok", "partial"))).group_by(r.company_id).subquery())
    return conn.execute(
        select(im.symbol, im.source, im.as_of.label("listed_as_of"), c.id, c.name, c.industry, c.is_financial,
               m.research_snapshots.c.id.label("snapshot_id"), m.research_snapshots.c.status, m.research_snapshots.c.as_of,
               m.research_snapshots.c.dimensions, m.research_snapshots.c.flags, m.research_snapshots.c.output, runs.c.last)
        .join(m.companies, c.id == im.company_id)
        .outerjoin(latest, latest.c.company_id == c.id).outerjoin(m.research_snapshots, m.research_snapshots.c.id == latest.c.sid)
        .outerjoin(runs, runs.c.company_id == c.id)
        .where(im.segment == seg).order_by(im.symbol)).mappings().all()


def segments_overview() -> dict:
    from app.research.segments import SEGMENTS, ensure_segments

    ensure_master()
    ensure_segments()
    out = []
    with tx() as conn:
        for key, spec in SEGMENTS.items():
            im, c, s = m.index_members.c, m.companies.c, m.research_snapshots.c
            latest = select(s.company_id, func.max(s.id).label("sid")).group_by(s.company_id).subquery()
            rows = conn.execute(select(im.source, im.as_of, m.research_snapshots.c.status)
                                .join(latest, latest.c.company_id == im.company_id, isouter=True)
                                .join(m.research_snapshots, m.research_snapshots.c.id == latest.c.sid, isouter=True)
                                .where(im.segment == key)).all()
            statuses = [r[2] for r in rows if r[2]]
            out.append({"key": key, "label": spec["label"], "index": spec["index"], "rank": spec["rank"], "count": len(rows),
                        "source": rows[0][0] if rows else None, "list_as_of": _iso(max((r[1] for r in rows if r[1]), default=None)),
                        "researched": len(statuses),
                        "totals": {st: statuses.count(st) for st in ("ELIGIBLE_FOR_RESEARCH", "REVIEW_REQUIRED", "BLOCKED")}})
    from app.research.ingest import job

    return {"segments": out, "job": job.get(),
            "note": "Together these three lists are the NIFTY 500 (SEBI size classes: top 100 large, 101-250 mid, 251-500 small)."}


def segment_detail(seg: str) -> dict:
    from app.research.segments import SEGMENTS, check_segment, ensure_segments

    seg = check_segment(seg)
    ensure_master()
    ensure_segments()
    with tx() as conn:
        rows = _segment_rows(conn, seg)
    items = []
    for r in rows:
        out = r["output"] or {}
        metrics = out.get("metrics") or {}
        flags = r["flags"] or []
        items.append({
            "symbol": r["symbol"], "name": r["name"], "industry": r["industry"], "is_financial": r["is_financial"],
            "snapshot_id": r["snapshot_id"], "status": r["status"], "as_of": _iso(r["as_of"]), "last_refresh": _iso(r["last"]),
            "price": (out.get("price") or {}).get("last"), "price_date": (out.get("price") or {}).get("date"),
            "dimensions": r["dimensions"] or {},
            "metrics": {k: {"value": (metrics.get(k) or {}).get("value"), "unit": (metrics.get(k) or {}).get("unit")} for k in SEGMENT_METRICS},
            "flag_counts": {lvl: sum(1 for f in flags if f.get("level") == lvl) for lvl in ("blocker", "review", "watch")},
        })
    spec = SEGMENTS[seg]
    statuses = [i["status"] for i in items if i["status"]]
    from app.research.ingest import job

    return {"key": seg, "label": spec["label"], "index": spec["index"], "rank": spec["rank"],
            "source": rows[0]["source"] if rows else None, "list_as_of": _iso(rows[0]["listed_as_of"]) if rows else None,
            "count": len(items), "researched": len(statuses),
            "totals": {st: statuses.count(st) for st in ("ELIGIBLE_FOR_RESEARCH", "REVIEW_REQUIRED", "BLOCKED")},
            "items": items, "job": job.get(), "metrics": list(SEGMENT_METRICS),
            "note": "Free sources, fetched one company at a time. A company refreshed in the last 24 hours is skipped unless you force it."}


def start_segment_fetch(seg: str, force: bool = False, refresh_list: bool = True, documents: bool = False) -> dict:
    """Refresh the segment's constituent list, then ingest every member in the background."""
    from app.research.ingest import job
    from app.research.segments import SEGMENTS, check_segment, ensure_segments, members, refresh_lists

    seg = check_segment(seg)
    ensure_master()
    ensure_segments()

    def prepare() -> list[str]:
        if refresh_list:
            refresh_lists([seg])
        with tx() as conn:
            return members(conn, seg)

    started = job.start(with_news=True, fetch_documents=documents, skip_fresh_hours=None if force else 24,
                        label=f"{SEGMENTS[seg]['label']} ({SEGMENTS[seg]['index']})", segment=seg, prepare=prepare)
    return {"started": started, "state": job.get(), **({} if started else {"reason": "Another fetch is already running — wait for it or stop it."})}
