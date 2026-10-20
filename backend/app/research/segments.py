"""Market-cap segments: Large cap (NIFTY 100), Mid cap (NIFTY Midcap 150), Small cap (NIFTY Smallcap 250).

Together they are exactly the NIFTY 500, which follows SEBI's size classification (largest 100 listed
companies by full market cap = large, 101-250 = mid, 251-500 = small).

Constituent lists come from NSE Indices' public CSV files (niftyindices.com). Each download is archived
with its hash like every other source. If the download fails (the site may block cloud hosts) the lists
bundled with the app are used instead, and the fallback is logged on the Data quality page.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path

import requests
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from app.research import models as m
from app.research.archive import archive, archive_error
from app.research.db import now, tx
from app.research.master import DATA, upsert_company
from app.research.providers.base import ERROR, OK, ProviderResult, RawPayload, with_retries

BASE = "https://www.niftyindices.com/IndexConstituent/"
SEGMENTS: dict[str, dict] = {
    "large": {"label": "Large cap", "index": "NIFTY 100", "file": "ind_nifty100list.csv", "expected": 100,
              "rank": "the 100 largest listed companies by market cap"},
    "mid": {"label": "Mid cap", "index": "NIFTY Midcap 150", "file": "ind_niftymidcap150list.csv", "expected": 150,
            "rank": "companies ranked 101-250 by market cap"},
    "small": {"label": "Small cap", "index": "NIFTY Smallcap 250", "file": "ind_niftysmallcap250list.csv", "expected": 250,
              "rank": "companies ranked 251-500 by market cap"},
}
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
           "Accept": "text/csv,*/*", "Referer": "https://www.niftyindices.com/"}
TEMPLATE_PREFIX = {"large": "L", "mid": "M", "small": "S"}


def check_segment(seg: str) -> str:
    seg = (seg or "").lower().strip()
    if seg not in SEGMENTS:
        raise KeyError(f"Unknown segment '{seg}'. Use one of: {', '.join(SEGMENTS)}")
    return seg


# ── parsing ──────────────────────────────────────────────────────────────────

def parse_constituents(content: bytes) -> list[dict]:
    """Parse an NSE Indices constituent CSV (Company Name, Industry, Symbol, Series, ISIN Code)."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    cols = {c.strip().lower() for c in (reader.fieldnames or [])}
    if not {"symbol", "company name"} <= cols:
        raise ValueError(f"not a constituent list (columns: {sorted(cols)[:6]})")
    out, seen = [], set()
    for row in reader:
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        sym = row.get("symbol", "").upper()
        if not sym or sym in seen or sym.startswith("DUMMY"):   # NSE placeholders during demergers (e.g. DUMMYVEDL1)
            continue
        seen.add(sym)
        out.append({"symbol": sym, "name": row.get("company name") or sym, "industry": row.get("industry") or None,
                    "isin": row.get("isin code") or None, "series": row.get("series") or "EQ"})
    return out


def _read(path: Path) -> list[dict]:
    return parse_constituents(path.read_bytes()) if path.exists() else []


def bundled(seg: str) -> list[dict]:
    """Lists shipped with the app. Mid cap = NIFTY 500 minus NIFTY 100 minus NIFTY Smallcap 250."""
    seg = check_segment(seg)
    if seg == "large":
        return _read(DATA / "nifty100.csv")
    if seg == "small":
        return _read(DATA / "nifty_smallcap_250.csv")
    other = {r["symbol"] for r in bundled("large")} | {r["symbol"] for r in bundled("small")}
    return [r for r in _read(DATA / "nifty500.csv") if r["symbol"] not in other]


def fetch_live(seg: str, timeout: int = 20, session: requests.Session | None = None) -> ProviderResult:
    seg = check_segment(seg)
    spec = SEGMENTS[seg]
    url = BASE + spec["file"]
    try:
        get = (session or requests).get
        resp = with_retries(lambda: get(url, headers=HEADERS, timeout=timeout), attempts=2)
        resp.raise_for_status()
        rows = parse_constituents(resp.content)
        if not spec["expected"] * 0.9 <= len(rows) <= spec["expected"] * 1.1:
            raise ValueError(f"only {len(rows)} rows, expected about {spec['expected']}")
    except Exception as exc:  # noqa: BLE001 — any failure falls back to the bundled list
        return ProviderResult(status=ERROR, error=f"{url}: {str(exc)[:200]}")
    return ProviderResult(status=OK, data=rows, raw=[RawPayload(f"niftyindices:{spec['file']}", resp.content, "text/csv", url=url)])


# ── membership ───────────────────────────────────────────────────────────────

def _company_for(conn: Connection, row: dict, seg: str) -> int:
    s = m.securities.c
    hit = conn.execute(select(s.company_id).where((s.exchange == "NSE") & (s.symbol == row["symbol"]) & (s.status == "active"))).first()
    if hit and not row.get("industry"):
        return hit[0]
    cid, _ = upsert_company(conn, symbol=row["symbol"], name=row["name"], isin=row.get("isin"), sector=row.get("industry"),
                            industry=row.get("industry"), listing_date=None, source=f"index:{seg}", series=row.get("series") or "EQ")
    return cid


def sync(conn: Connection, seg: str, rows: list[dict], source: str, raw_id: int | None = None) -> dict:
    """Replace the segment's membership with `rows`. Returns counts of added / removed symbols."""
    im = m.index_members.c
    before = {r[0] for r in conn.execute(select(im.symbol).where(im.segment == seg)).all()}
    conn.execute(m.index_members.delete().where(im.segment == seg))
    ts, done = now(), set()
    for r in rows:
        cid = _company_for(conn, r, seg)
        if cid in done:
            continue
        done.add(cid)
        conn.execute(m.index_members.insert().values(segment=seg, index_name=SEGMENTS[seg]["index"], company_id=cid, symbol=r["symbol"],
                                                     source=source, raw_source_id=raw_id, as_of=ts))
    after = {r["symbol"] for r in rows}
    return {"count": len(done), "added": sorted(after - before) if before else [], "removed": sorted(before - after)}


def ensure_segments() -> None:
    """Load the bundled lists the first time segments are used."""
    with tx() as conn:
        if conn.execute(select(func.count()).select_from(m.index_members)).scalar():
            return
        for seg in SEGMENTS:
            sync(conn, seg, bundled(seg), "bundled")


def refresh_lists(segments: list[str] | None = None, live: bool = True) -> dict:
    """Download the current constituent lists (falling back to the bundled ones) and update membership."""
    from app.research.ingest import IssueLog, demo_mode

    out = {}
    for seg in segments or list(SEGMENTS):
        seg = check_segment(seg)
        with tx() as conn:
            run_id = conn.execute(m.ingestion_runs.insert().values(kind="index_list", started_at=now(), status="running",
                                                                   detail={"segment": seg})).inserted_primary_key[0]
            issues = IssueLog(conn, None, run_id)
            res = fetch_live(seg) if live and not demo_mode() else ProviderResult(status=ERROR, error="live download skipped (demo mode)")
            if res.ok:
                raw_id, _ = archive(conn, source_key=res.raw[0].source_key, provider="niftyindices", content=res.raw[0].content,
                                    content_type="text/csv", url=res.raw[0].url)
                counts = sync(conn, seg, res.data, "niftyindices", raw_id)
                source, status = "niftyindices", "ok"
                di = m.data_issues.c
                conn.execute(m.data_issues.update().where((di.company_id.is_(None)) & (di.kind == "fetch_error") & (di.field == f"index.{seg}")
                                                          & (di.status == "open")).values(status="resolved", resolved_at=now()))
            else:
                if live and not demo_mode():
                    archive_error(conn, source_key=f"niftyindices:{SEGMENTS[seg]['file']}", provider="niftyindices",
                                  error=res.error or "error", url=BASE + SEGMENTS[seg]["file"])
                    issues.add("fetch_error", f"index.{seg}", f"Could not download the {SEGMENTS[seg]['index']} list ({res.error}); "
                               "using the list bundled with the app, which may be out of date.", "warn")
                counts = sync(conn, seg, bundled(seg), "bundled")
                source, status = "bundled", "partial" if live and not demo_mode() else "ok"
            detail = {"segment": seg, "source": source, **counts, "error": None if res.ok else res.error}
            conn.execute(m.ingestion_runs.update().where(m.ingestion_runs.c.id == run_id).values(finished_at=now(), status=status, detail=detail))
        out[seg] = detail
    return out


def members(conn: Connection, seg: str) -> list[str]:
    im = m.index_members.c
    return [r[0] for r in conn.execute(select(im.symbol).where(im.segment == seg).order_by(im.symbol)).all()]


# ── verification templates ───────────────────────────────────────────────────

def last_fiscal_year_end(today: date | None = None) -> date:
    """Indian FY ends 31 March; annual results are mostly out by end of May."""
    today = today or date.today()
    return date(today.year, 3, 31) if today >= date(today.year, 6, 1) else date(today.year - 1, 3, 31)


TEMPLATE_COLUMNS = ["case_id", "symbol", "kind", "field", "period", "period_type", "expected", "unit", "tolerance_pct", "source_note", "explanation"]


def template_csv(seg: str, symbols: list[tuple[str, bool]], fy_end: date | None = None) -> str:
    """Blank verification rows (FY revenue and net profit per company) to fill in from annual reports.
    `symbols` is a list of (symbol, is_financial)."""
    seg = check_segment(seg)
    fy = fy_end or last_fiscal_year_end()
    label = f"FY{str(fy.year)[2:]}"
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(TEMPLATE_COLUMNS)
    n = 0
    for sym, fin in symbols:
        for field, what in (("revenue", "total income" if fin else "revenue from operations"), ("net_income", "net profit")):
            n += 1
            w.writerow([f"{TEMPLATE_PREFIX[seg]}{n:03d}", sym, "fact", field, fy.isoformat(), "FY", "", "INR_cr", "1",
                        f"{SEGMENTS[seg]['label']}: fill {label} consolidated {what} in ₹ crore from the annual report / results filing; note the page", ""])
    return buf.getvalue()


def template_for(seg: str) -> str:
    ensure_segments()
    seg = check_segment(seg)
    im, c = m.index_members.c, m.companies.c
    with tx() as conn:
        rows = conn.execute(select(im.symbol, c.is_financial).join(m.companies, c.id == im.company_id)
                            .where(im.segment == seg).order_by(im.symbol)).all()
    return template_csv(seg, [(r[0], bool(r[1])) for r in rows])


def bundled_template(seg: str, fy_end: date | None = None) -> str:
    from app.research.master import is_financial

    return template_csv(seg, [(r["symbol"], is_financial(r.get("industry"), r.get("industry"))) for r in bundled(seg)], fy_end)

