"""PDF documents → page-referenced text chunks (pdfplumber). Page numbers are preserved so
every extracted claim can point at "document N, page P"."""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Connection

from app.research import models as m
from app.research.archive import archive, sha256
from app.research.db import now

logger = logging.getLogger(__name__)
PARSER_VERSION = "pdfplumber-1"
CHUNK_CHARS = 1200
MAX_PAGES = 120

DOC_TYPES = (
    ("results", ("financial result", "results", "quarterly result")),
    ("presentation", ("presentation", "investor meet", "analyst", "con. call", "earnings call")),
    ("annual_report", ("annual report",)),
)


def classify_doc(title: str) -> str:
    t = (title or "").lower()
    for kind, words in DOC_TYPES:
        if any(w in t for w in words):
            return kind
    return "announcement"


def extract_pages(content: bytes) -> tuple[list[str], str]:
    """Returns (page texts, status). status: ok | no_text (scanned/image PDF) | error."""
    try:
        import pdfplumber

        pages = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages[:MAX_PAGES]:
                pages.append(page.extract_text() or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF parse failed: %s", exc)
        return [], "error"
    return pages, "ok" if any(p.strip() for p in pages) else "no_text"


def chunk_page(text: str, size: int = CHUNK_CHARS) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[A-Z0-9•\-])", text) if p.strip()]
    out, buf = [], ""
    for p in paras:
        if len(buf) + len(p) + 1 > size and buf:
            out.append(buf)
            buf = ""
        while len(p) > size:
            out.append(p[:size])
            p = p[size:]
        buf = f"{buf}\n{p}".strip()
    if buf:
        out.append(buf)
    return out


def store_document(conn: Connection, *, content: bytes, title: str, company_id: int | None, url: str | None,
                   provider: str, source_key: str, published_at: datetime | None = None,
                   doc_type: str | None = None) -> tuple[int, bool]:
    """Archive + parse a PDF. Idempotent by content hash. Returns (document_id, created)."""
    digest = sha256(content)
    existing = conn.execute(select(m.documents.c.id).where((m.documents.c.sha256 == digest) & (m.documents.c.company_id == company_id))).first()
    raw_id, _ = archive(conn, source_key=source_key, provider=provider, content=content, content_type="application/pdf",
                        url=url, company_id=company_id, published_at=published_at, parser_version=PARSER_VERSION)
    if existing:
        return existing.id, False
    pages, status = extract_pages(content)
    doc_id = conn.execute(m.documents.insert().values(
        company_id=company_id, raw_source_id=raw_id, title=title[:300], doc_type=doc_type or classify_doc(title), url=url,
        published_at=published_at, pages=len(pages), text_status=status, parser_version=PARSER_VERSION, sha256=digest, created_at=now(),
    )).inserted_primary_key[0]
    rows = []
    for page_no, text in enumerate(pages, start=1):
        for seq, chunk in enumerate(chunk_page(text)):
            rows.append({"document_id": doc_id, "page": page_no, "seq": seq, "text": chunk})
    if rows:
        conn.execute(m.chunks.insert(), rows)
    return doc_id, True
