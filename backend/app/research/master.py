"""Company/security master. Source: the NSE equity list bundled with the app (symbol, name,
listing date, ISIN) plus index files for sector/industry. Idempotent: re-loading updates names and
records identifier changes in rs_identifier_history instead of overwriting silently."""
from __future__ import annotations

import csv
import os
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Connection

from app.research import models as m
from app.research.db import now
from app.research.providers.fixtures import FixtureProvider

DATA = Path(__file__).resolve().parents[2] / "data"
FINANCIAL_WORDS = ("bank", "financ", "insurance", "nbfc", "lending", "capital markets", "asset management", "housing finance")

# About 20 representative real companies for deep research: mixed sizes and sectors, including
# banks, an NBFC, an insurer and commodity businesses. Override with RESEARCH_SAMPLE_SYMBOLS.
DEFAULT_DEEP = ("RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "BAJFINANCE", "HDFCLIFE", "HINDUNILVR", "ITC", "LT", "SUNPHARMA",
                "TATASTEEL", "ULTRACEMCO", "MARUTI", "ASIANPAINT", "DIXON", "POLYCAB", "KPITTECH", "DEEPAKNTR", "APARINDS", "CGPOWER")


def deep_symbols() -> tuple[str, ...]:
    raw = os.getenv("RESEARCH_SAMPLE_SYMBOLS", "")
    return tuple(s.strip().upper() for s in raw.split(",") if s.strip()) or DEFAULT_DEEP


def is_financial(sector: str | None, industry: str | None) -> bool:
    text = f"{sector or ''} {industry or ''}".lower()
    return any(w in text for w in FINANCIAL_WORDS)


def _industries() -> dict[str, str]:
    out: dict[str, str] = {}
    for name in ("nifty_smallcap_250.csv", "nifty500.csv"):
        path = DATA / name
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                sym = (row.get("Symbol") or "").strip().upper()
                if sym:
                    out[sym] = (row.get("Industry") or "").strip()
    return out


def _parse_listing(text: str | None) -> date | None:
    try:
        return datetime.strptime((text or "").strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def upsert_company(conn: Connection, *, symbol: str, name: str, isin: str | None, sector: str | None, industry: str | None,
                   listing_date: date | None, source: str, is_sample: bool = False, deep: bool = False,
                   series: str | None = "EQ") -> tuple[int, int]:
    """Returns (company_id, security_id)."""
    ts = now()
    sec = conn.execute(select(m.securities).where((m.securities.c.exchange == "NSE") & (m.securities.c.symbol == symbol))).mappings().first()
    company = None
    if isin:
        company = conn.execute(select(m.companies).where(m.companies.c.isin == isin)).mappings().first()
    if company is None and sec:
        company = conn.execute(select(m.companies).where(m.companies.c.id == sec["company_id"])).mappings().first()
    fin = is_financial(sector, industry)
    if company is None:
        cid = conn.execute(m.companies.insert().values(
            name=name, isin=isin, sector=sector, industry=industry, listing_date=listing_date, status="listed",
            is_financial=fin, is_sample=is_sample, deep_research=deep, source=source, created_at=ts, updated_at=ts,
        )).inserted_primary_key[0]
    else:
        cid = company["id"]
        changes = {}
        if name and name != company["name"]:
            changes["name"] = name
        if sector and not company["sector"]:
            changes["sector"] = sector
        if industry and not company["industry"]:
            changes["industry"] = industry
        if deep and not company["deep_research"]:
            changes["deep_research"] = True
        if changes:
            changes["is_financial"] = is_financial(changes.get("sector", company["sector"]), changes.get("industry", company["industry"]))
            changes["updated_at"] = ts
            conn.execute(m.companies.update().where(m.companies.c.id == cid).values(**changes))
            if "name" in changes and sec:
                conn.execute(m.identifier_history.insert().values(security_id=sec["id"], field="name", old_value=company["name"],
                                                                  new_value=name, source=source, recorded_at=ts))
    if sec is None:
        # same company (ISIN) under a new symbol → record the symbol change
        old = conn.execute(select(m.securities).where((m.securities.c.company_id == cid) & (m.securities.c.exchange == "NSE"))).mappings().first()
        sid = conn.execute(m.securities.insert().values(company_id=cid, exchange="NSE", symbol=symbol,
                                                        provider_symbol=None if is_sample else f"{symbol}.NS",
                                                        series=series, status="active", source=source)).inserted_primary_key[0]
        if old and old["symbol"] != symbol:
            conn.execute(m.securities.update().where(m.securities.c.id == old["id"]).values(status="renamed"))
            conn.execute(m.identifier_history.insert().values(security_id=sid, field="symbol", old_value=old["symbol"], new_value=symbol,
                                                              source=source, recorded_at=ts))
    else:
        sid = sec["id"]
    return cid, sid


def load_master(conn: Connection, include_sample: bool = True) -> dict:
    industries = _industries()
    deep = set(deep_symbols())
    loaded = 0
    path = DATA / "nse_equities.csv"
    if path.exists():
        with path.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
                sym = row.get("SYMBOL", "").upper()
                if not sym or row.get("SERIES") not in ("EQ", "BE", "BZ", "SM", "ST"):
                    continue
                industry = industries.get(sym) or None
                upsert_company(conn, symbol=sym, name=row.get("NAME OF COMPANY") or sym, isin=row.get("ISIN NUMBER") or None,
                               sector=industry, industry=industry, listing_date=_parse_listing(row.get("DATE OF LISTING")),
                               source="nse_equity_list", deep=sym in deep, series=row.get("SERIES"))
                loaded += 1
    samples = 0
    if include_sample:
        for c in FixtureProvider.universe():
            upsert_company(conn, symbol=c["symbol"], name=c["name"], isin=c["isin"], sector=c["sector"], industry=c["industry"],
                           listing_date=None, source="fixture", is_sample=True, deep=True)
            samples += 1
    return {"listed": loaded, "sample": samples}
