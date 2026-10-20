"""HTTP routes for the research platform, mounted at /api/research. Read-only research: there
are no trading or order endpoints anywhere in this module."""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from app.research import auth, service
from app.research.db import get_engine, schema_versions

logger = logging.getLogger(__name__)
public = APIRouter(prefix="/research", tags=["research"])
router = APIRouter(prefix="/research", tags=["research"], dependencies=[Depends(auth.require_auth)])
SYMBOL = re.compile(r"^[A-Za-z0-9&^.\-]{1,30}$")
MAX_UPLOAD = 15 * 1024 * 1024


def _sym(symbol: str) -> str:
    if not SYMBOL.match(symbol):
        raise HTTPException(status_code=400, detail="Use an NSE symbol such as DIXON.")
    return symbol.upper().removesuffix(".NS")


def _404(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── public ───────────────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    password: str = Field(max_length=200)


@public.get("/health")
def health():
    from app.research.ingest import demo_mode
    from app.research.llm import config

    try:
        engine = get_engine()
        db = {"ok": True, "dialect": engine.dialect.name, "schema": [v["version"] for v in schema_versions()]}
    except Exception as exc:  # noqa: BLE001
        db = {"ok": False, "error": str(exc)[:200]}
    return {"status": "ok" if db["ok"] else "degraded", "database": db, "demo_mode": demo_mode(), "ai_enabled": config()["enabled"],
            "protected": auth.protected()}


@public.get("/auth/status")
def auth_status():
    return {"protected": auth.protected()}


@public.post("/auth/login")
def auth_login(body: LoginBody):
    res = auth.login(body.password)
    if res is None:
        raise HTTPException(status_code=401, detail="Wrong password.")
    token, exp = res
    return {"token": token, "expires_at": exp}


# ── read ─────────────────────────────────────────────────────────────────────

@router.get("/dashboard")
def dashboard():
    return service.dashboard()


@router.get("/companies")
def companies(q: str | None = Query(default=None, max_length=80), sector: str | None = None, scope: str = "all", status: str | None = None,
              coverage: str | None = None, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    if scope not in ("all", "deep", "sample", "researched"):
        raise HTTPException(status_code=400, detail="scope must be all, deep, sample or researched")
    return service.search_companies(q, sector, scope, status, coverage, limit, offset)


@router.get("/sectors")
def sectors():
    return service.sectors()


@router.get("/company/{symbol}")
def company(symbol: str):
    return _404(service.company_detail, _sym(symbol))


@router.get("/company/{symbol}/prices")
def company_prices(symbol: str, days: int = Query(default=400, ge=20, le=2000)):
    return _404(service.company_prices, _sym(symbol), days)


@router.get("/company/{symbol}/facts")
def company_facts(symbol: str):
    return _404(service.company_facts, _sym(symbol))


@router.get("/company/{symbol}/dossier.md", response_class=PlainTextResponse)
def dossier_md(symbol: str):
    from app.research.engine import build_snapshot, to_markdown

    return _404(lambda: to_markdown(build_snapshot(_sym(symbol))["output"]))


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: int):
    return _404(service.snapshot, snapshot_id)


@router.post("/snapshots/{snapshot_id}/replay")
def replay(snapshot_id: int):
    from app.research.engine import replay_snapshot

    return _404(replay_snapshot, snapshot_id)


@router.get("/discovery")
def discovery(sort: str = "growth_inflection", status: str | None = None, sector: str | None = None, include_sample: bool = True,
              min_confidence: float | None = Query(default=None, ge=0, le=100)):
    return service.discovery(sort, status, sector, include_sample, min_confidence)


@router.get("/news")
def news(symbol: str | None = None, q: str | None = Query(default=None, max_length=80), limit: int = Query(default=60, ge=1, le=300),
         include_duplicates: bool = False, matched_only: bool = False):
    return _404(service.news, _sym(symbol) if symbol else None, q, limit, include_duplicates, matched_only)


@router.get("/announcements")
def announcements(symbol: str | None = None, event_type: str | None = None, limit: int = Query(default=60, ge=1, le=300)):
    return _404(service.announcements, _sym(symbol) if symbol else None, event_type, limit)


@router.get("/evidence/{ref}")
def evidence(ref: str, snapshot_id: int | None = None):
    if not re.match(r"^(fact|ann|chunk|news|raw|metric|check|valuation|dimension):[\w.\-]+$", ref):
        raise HTTPException(status_code=400, detail="Unknown evidence reference")
    return _404(service.evidence, ref, snapshot_id)


@router.get("/raw/{raw_id}/content")
def raw(raw_id: int):
    meta, body = _404(service.raw_content, raw_id)
    if body is None:
        raise HTTPException(status_code=404, detail="Original content not stored (fetch error or file over the size limit)")
    ctype = meta.get("content_type") or "application/octet-stream"
    safe = ctype if ctype in ("application/pdf", "text/csv", "application/json", "application/rss+xml") else "text/plain"
    return Response(content=body, media_type=safe, headers={"X-Source-Url": meta.get("url") or "", "X-Sha256": meta["sha256"],
                                                            "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})


@router.get("/documents/{doc_id}")
def document(doc_id: int):
    return _404(service.document, doc_id)


@router.get("/search")
def search(q: str = Query(min_length=2, max_length=80)):
    return service.search(q)


@router.get("/watchlists")
def watchlists():
    return service.watchlists()


@router.get("/decisions")
def decisions(symbol: str | None = None):
    return _404(service.decisions, _sym(symbol) if symbol else None)


@router.get("/data-quality")
def data_quality(status: str = "open", kind: str | None = None):
    if status not in ("open", "resolved", "ignored", "all"):
        raise HTTPException(status_code=400, detail="status must be open, resolved, ignored or all")
    return service.data_quality(status, kind)


@router.get("/settings")
def settings_view():
    return service.settings_view()


@router.get("/ingest/status")
def ingest_status():
    from app.research.ingest import job

    return job.get()


@router.get("/backup")
def backup():
    return service.backup()


# ── write ────────────────────────────────────────────────────────────────────

class IngestBody(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=60)
    deep: bool = False
    news: bool = True


@router.post("/ingest")
def ingest(body: IngestBody):
    from app.research.ingest import job
    from app.research.master import deep_symbols

    service.ensure_master()
    symbols = [_sym(s) for s in body.symbols] or (list(deep_symbols()) if body.deep else [])
    if not symbols:
        raise HTTPException(status_code=400, detail="Give symbols or set deep=true")
    started = job.start(symbols, with_news=body.news)
    return {"started": started, "state": job.get(), **({} if started else {"reason": "An ingestion job is already running."})}


@router.post("/seed-samples")
def seed_samples():
    return service.seed_samples()


@router.post("/news/refresh")
def refresh_news():
    from app.research.ingest import ingest_news

    return ingest_news()


class WatchlistBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class WatchItemBody(BaseModel):
    symbol: str
    reason: str | None = Field(default=None, max_length=4000)


@router.post("/watchlists")
def create_watchlist(body: WatchlistBody):
    return service.create_watchlist(body.name, body.description)


@router.put("/watchlists/{wid}")
def update_watchlist(wid: int, body: WatchlistBody):
    _404(service.update_watchlist, wid, body.name, body.description)
    return {"ok": True}


@router.delete("/watchlists/{wid}")
def delete_watchlist(wid: int):
    _404(service.delete_watchlist, wid)
    return {"ok": True}


@router.post("/watchlists/{wid}/items")
def add_item(wid: int, body: WatchItemBody):
    return _404(service.add_to_watchlist, wid, _sym(body.symbol), body.reason)


@router.delete("/watchlists/{wid}/items/{item_id}")
def remove_item(wid: int, item_id: int):
    _404(service.remove_from_watchlist, wid, item_id)
    return {"ok": True}


class DecisionBody(BaseModel):
    symbol: str | None = None
    title: str | None = Field(default=None, max_length=200)
    thesis: str | None = Field(default=None, max_length=8000)
    drivers: str | None = Field(default=None, max_length=8000)
    valuation_assumptions: str | None = Field(default=None, max_length=8000)
    downside_risks: str | None = Field(default=None, max_length=8000)
    invalidation: str | None = Field(default=None, max_length=8000)
    status: str | None = None
    outcome: str | None = Field(default=None, max_length=8000)


class FollowUpBody(BaseModel):
    note: str = Field(min_length=1, max_length=4000)
    outcome: str | None = Field(default=None, max_length=200)


@router.post("/decisions")
def create_decision(body: DecisionBody):
    if not body.symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    return _404(service.create_decision, _sym(body.symbol), body.model_dump())


@router.put("/decisions/{did}")
def update_decision(did: int, body: DecisionBody):
    _404(service.update_decision, did, body.model_dump(exclude_unset=True, exclude={"symbol"}))
    return {"ok": True}


@router.delete("/decisions/{did}")
def delete_decision(did: int):
    _404(service.delete_decision, did)
    return {"ok": True}


@router.post("/decisions/{did}/follow-ups")
def follow_up(did: int, body: FollowUpBody):
    return _404(service.add_follow_up, did, body.note, body.outcome)


class IssueBody(BaseModel):
    status: str


@router.post("/issues/{issue_id}")
def set_issue(issue_id: int, body: IssueBody):
    _404(service.set_issue_status, issue_id, body.status)
    return {"ok": True}


async def _read(upload: UploadFile) -> bytes:
    data = await upload.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="File larger than 15 MB")
    return data


@router.post("/import/facts")
async def import_facts(file: UploadFile = File(...)):
    from app.research.ingest import import_facts as run

    name = file.filename or "upload.csv"
    if not name.lower().endswith((".csv", ".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Upload a .csv or .xlsx file")
    return run(await _read(file), name)


@router.post("/import/document")
async def import_document(file: UploadFile = File(...), symbol: str | None = Form(default=None), title: str | None = Form(default=None)):
    from app.research.ingest import import_document as run

    data = await _read(file)
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Upload a PDF")
    return _404(run, data, file.filename or "document.pdf", _sym(symbol) if symbol else None, title)


@router.post("/verify")
async def verify(file: UploadFile = File(...)):
    from app.research.verify import run

    return run(await _read(file), file.filename or "verification.csv")


@router.post("/company/{symbol}/refresh")
def refresh(symbol: str):
    from app.research.ingest import job

    service.ensure_master()
    sym = _sym(symbol)
    _404(service.company_detail, sym)
    started = job.start([sym], with_news=False)
    return {"started": started, "state": job.get(), **({} if started else {"reason": "An ingestion job is already running."})}


@router.post("/ai/extract/{doc_id}")
def ai_extract(doc_id: int, force: bool = False):
    from app.research import llm
    from app.research.engine import latest_snapshot
    from app.research.db import tx
    from app.research import models as m
    from sqlalchemy import select

    if not llm.config()["enabled"]:
        raise HTTPException(status_code=409, detail="AI is not configured. Set RESEARCH_LLM_API_KEY on the API service.")
    with tx() as conn:
        cid = conn.execute(select(m.documents.c.company_id).where(m.documents.c.id == doc_id)).scalar()
        snap = latest_snapshot(conn, cid) if cid else None
    sheet, numbers = "", []
    if snap:
        for k, v in snap["output"]["metrics"].items():
            if v["value"] is not None:
                sheet += f"- {v['label']}: {v['value']:.2f} ({v['unit']})\n"
                numbers.append(v["value"])
    try:
        return _404(llm.extract_document, doc_id, sheet, numbers, None, force)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/ai/narrate/{snapshot_id}")
def ai_narrate(snapshot_id: int):
    from app.research import llm

    if not llm.config()["enabled"]:
        raise HTTPException(status_code=409, detail="AI is not configured. Set RESEARCH_LLM_API_KEY on the API service.")
    snap = _404(service.snapshot, snapshot_id)
    try:
        return llm.narrate(snap["output"], snap["output_hash"])
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class PrefsBody(BaseModel):
    preferences: dict


@router.put("/settings")
def save_settings(body: PrefsBody):
    service.save_preferences(body.preferences)
    return {"ok": True}


@router.post("/restore")
def restore(body: dict):
    return _404(service.restore, body)
