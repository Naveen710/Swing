"""Tiny persistence layer. Uses DATABASE_URL (Postgres in production) or a local SQLite file."""
from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime

from sqlalchemy import (
    Column, Date, DateTime, Float, Integer, MetaData, String, Table, Text,
    UniqueConstraint, create_engine, select,
)
from sqlalchemy.engine import Engine

from app.config import settings

logger = logging.getLogger(__name__)
metadata = MetaData()

signals_table = Table(
    "signals", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("signal_date", Date, nullable=False),
    Column("symbol", String(40), nullable=False),
    Column("company_name", String(200)),
    Column("sector", String(120)),
    Column("universe", String(40)),
    Column("pattern", String(60), nullable=False),
    Column("regime", String(20)),
    Column("entry", Float, nullable=False),
    Column("stop", Float, nullable=False),
    Column("target", Float, nullable=False),
    Column("probability", Float),
    Column("ranking_score", Float),
    Column("risk_reward", Float),
    Column("status", String(20), nullable=False, default="pending"),
    Column("fill_date", Date),
    Column("fill_price", Float),
    Column("exit_date", Date),
    Column("exit_price", Float),
    Column("sessions_held", Integer),
    Column("return_pct_net", Float),
    Column("r_multiple_net", Float),
    Column("recorded_at", DateTime(timezone=True)),
    Column("evaluated_at", DateTime(timezone=True)),
    UniqueConstraint("symbol", "signal_date", "pattern", name="uq_signal"),
)

artifacts_table = Table(
    "artifacts", metadata,
    Column("key", String(120), primary_key=True),
    Column("payload", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

_engine: Engine | None = None
_lock = threading.Lock()


def database_url() -> str:
    url = settings.database_url.strip()
    if not url:
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(settings.cache_dir / 'swing.db').as_posix()}"
    # Render/Heroku style URLs → SQLAlchemy psycopg3 driver
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_engine() -> Engine:
    global _engine
    with _lock:
        if _engine is None:
            url = database_url()
            kwargs = {"pool_pre_ping": True}
            if url.startswith("sqlite"):
                kwargs["connect_args"] = {"check_same_thread": False}
            _engine = create_engine(url, **kwargs)
            metadata.create_all(_engine)
            logger.info("Database ready (%s)", url.split("@")[-1].split("?")[0])
        return _engine


def is_persistent() -> bool:
    return bool(settings.database_url.strip())


def save_artifact(key: str, payload: dict) -> None:
    engine = get_engine()
    body = json.dumps(payload, default=str)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        exists = conn.execute(select(artifacts_table.c.key).where(artifacts_table.c.key == key)).first()
        if exists:
            conn.execute(artifacts_table.update().where(artifacts_table.c.key == key).values(payload=body, updated_at=now))
        else:
            conn.execute(artifacts_table.insert().values(key=key, payload=body, updated_at=now))


def load_artifact(key: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(artifacts_table.c.payload).where(artifacts_table.c.key == key)).first()
    return json.loads(row[0]) if row else None
