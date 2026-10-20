"""Research database access: own engine (foreign keys enforced on SQLite), versioned
migrations, small helpers. Same DATABASE_URL as the rest of the app."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine

from app.research import models as m
from app.services.db import database_url

_engine: Engine | None = None
_lock = threading.Lock()


def now() -> datetime:
    return datetime.now(UTC)


def _fk_on(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.close()


# ── migrations ───────────────────────────────────────────────────────────────

def _m1_base(conn: Connection, dialect: str) -> None:
    m.metadata.create_all(conn)


def _m2_fts(conn: Connection, dialect: str) -> None:
    """Full-text search. SQLite: FTS5 external-content tables kept in sync by triggers.
    Postgres: no-op (search.py falls back to ILIKE)."""
    if dialect != "sqlite":
        return
    stmts = [
        "CREATE VIRTUAL TABLE IF NOT EXISTS rs_chunks_fts USING fts5(text, content='rs_chunks', content_rowid='id', tokenize='porter unicode61')",
        "CREATE TRIGGER IF NOT EXISTS rs_chunks_ai AFTER INSERT ON rs_chunks BEGIN INSERT INTO rs_chunks_fts(rowid, text) VALUES (new.id, new.text); END",
        "CREATE TRIGGER IF NOT EXISTS rs_chunks_ad AFTER DELETE ON rs_chunks BEGIN INSERT INTO rs_chunks_fts(rs_chunks_fts, rowid, text) VALUES('delete', old.id, old.text); END",
        "CREATE VIRTUAL TABLE IF NOT EXISTS rs_companies_fts USING fts5(name, sector, industry, content='rs_companies', content_rowid='id', tokenize='unicode61')",
        "CREATE TRIGGER IF NOT EXISTS rs_companies_ai AFTER INSERT ON rs_companies BEGIN INSERT INTO rs_companies_fts(rowid, name, sector, industry) VALUES (new.id, new.name, new.sector, new.industry); END",
        "CREATE TRIGGER IF NOT EXISTS rs_companies_ad AFTER DELETE ON rs_companies BEGIN INSERT INTO rs_companies_fts(rs_companies_fts, rowid, name, sector, industry) VALUES('delete', old.id, old.name, old.sector, old.industry); END",
        "CREATE TRIGGER IF NOT EXISTS rs_companies_au AFTER UPDATE ON rs_companies BEGIN INSERT INTO rs_companies_fts(rs_companies_fts, rowid, name, sector, industry) VALUES('delete', old.id, old.name, old.sector, old.industry); INSERT INTO rs_companies_fts(rowid, name, sector, industry) VALUES (new.id, new.name, new.sector, new.industry); END",
        "CREATE VIRTUAL TABLE IF NOT EXISTS rs_news_fts USING fts5(headline, summary, content='rs_news_items', content_rowid='id', tokenize='porter unicode61')",
        "CREATE TRIGGER IF NOT EXISTS rs_news_ai AFTER INSERT ON rs_news_items BEGIN INSERT INTO rs_news_fts(rowid, headline, summary) VALUES (new.id, new.headline, new.summary); END",
        "CREATE TRIGGER IF NOT EXISTS rs_news_ad AFTER DELETE ON rs_news_items BEGIN INSERT INTO rs_news_fts(rs_news_fts, rowid, headline, summary) VALUES('delete', old.id, old.headline, old.summary); END",
    ]
    for s in stmts:
        conn.execute(text(s))


MIGRATIONS = [
    (1, "base research tables", _m1_base),
    (2, "full-text search (SQLite FTS5)", _m2_fts),
]


def migrate(engine: Engine) -> list[int]:
    """Apply pending migrations in order, each in its own transaction. Returns applied versions."""
    dialect = engine.dialect.name
    applied: list[int] = []
    with engine.begin() as conn:
        m.schema_version.create(conn, checkfirst=True)
        done = {r[0] for r in conn.execute(m.schema_version.select().with_only_columns(m.schema_version.c.version))}
    for version, name, fn in MIGRATIONS:
        if version in done:
            continue
        with engine.begin() as conn:
            fn(conn, dialect)
            conn.execute(m.schema_version.insert().values(version=version, name=name, applied_at=now()))
        applied.append(version)
    return applied


def get_engine() -> Engine:
    global _engine
    with _lock:
        if _engine is None:
            url = database_url()
            kwargs: dict = {"pool_pre_ping": True}
            if url.startswith("sqlite"):
                kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            engine = create_engine(url, **kwargs)
            if url.startswith("sqlite"):
                event.listen(engine, "connect", _fk_on)
            migrate(engine)
            _engine = engine
        return _engine


def reset_engine_for_tests() -> None:
    global _engine
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None


@contextmanager
def tx():
    """One transaction. Commits on success, rolls back on error."""
    with get_engine().begin() as conn:
        yield conn


def is_sqlite() -> bool:
    return get_engine().dialect.name == "sqlite"


def schema_versions() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(m.schema_version.select().order_by(m.schema_version.c.version)).mappings().all()
    return [dict(r) for r in rows]
