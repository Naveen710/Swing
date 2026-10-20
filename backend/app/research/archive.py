"""Raw-source archive. Every payload a provider returns is stored with its SHA-256, retrieval
time, URL/provider and parser version. Changed content becomes a new version; old versions are
never overwritten. Identical content only bumps `last_seen_at`."""
from __future__ import annotations

import hashlib
import zlib
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from app.research import models as m
from app.research.db import now

MAX_STORED_BYTES = 12 * 1024 * 1024     # larger files keep hash + metadata only


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def archive(conn: Connection, *, source_key: str, provider: str, content: bytes, content_type: str,
            url: str | None = None, company_id: int | None = None, published_at: datetime | None = None,
            parser_version: str = "1") -> tuple[int, bool]:
    """Store a payload. Returns (raw_source_id, is_new_version)."""
    digest = sha256(content)
    latest = conn.execute(
        select(m.raw_sources.c.id, m.raw_sources.c.sha256)
        .where((m.raw_sources.c.source_key == source_key) & (m.raw_sources.c.status == "ok"))
        .order_by(m.raw_sources.c.version.desc()).limit(1)
    ).first()
    ts = now()
    if latest and latest.sha256 == digest:
        conn.execute(m.raw_sources.update().where(m.raw_sources.c.id == latest.id).values(last_seen_at=ts))
        return latest.id, False
    version = (conn.execute(select(func.max(m.raw_sources.c.version)).where(m.raw_sources.c.source_key == source_key)).scalar() or 0) + 1
    stored = zlib.compress(content) if len(content) <= MAX_STORED_BYTES else None
    rid = conn.execute(m.raw_sources.insert().values(
        source_key=source_key, version=version, provider=provider, url=url, company_id=company_id,
        retrieved_at=ts, last_seen_at=ts, published_at=published_at, sha256=digest, content_type=content_type,
        size_bytes=len(content), content=stored, parser_version=parser_version, status="ok",
    )).inserted_primary_key[0]
    return rid, True


def archive_error(conn: Connection, *, source_key: str, provider: str, error: str, url: str | None = None,
                  company_id: int | None = None) -> int:
    version = (conn.execute(select(func.max(m.raw_sources.c.version)).where(m.raw_sources.c.source_key == source_key)).scalar() or 0) + 1
    ts = now()
    return conn.execute(m.raw_sources.insert().values(
        source_key=source_key, version=version, provider=provider, url=url, company_id=company_id,
        retrieved_at=ts, last_seen_at=ts, sha256=sha256(b""), content_type=None, size_bytes=0, content=None,
        parser_version=None, status="error", error=error[:2000],
    )).inserted_primary_key[0]


def read_content(conn: Connection, raw_id: int) -> tuple[dict, bytes | None]:
    row = conn.execute(select(m.raw_sources).where(m.raw_sources.c.id == raw_id)).mappings().first()
    if not row:
        raise KeyError(raw_id)
    meta = {k: v for k, v in row.items() if k != "content"}
    return meta, zlib.decompress(row["content"]) if row["content"] else None
