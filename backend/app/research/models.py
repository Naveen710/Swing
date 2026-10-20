"""Research platform schema (SQLAlchemy Core). Shares the app database (SQLite locally,
Postgres on Render). Tables are prefixed `rs_`. Schema changes go through `migrations.py`.

Evidence model: everything traceable has an ID that claims can cite —
  fact:<id>  rs_financial_facts   ann:<id>   rs_announcements
  chunk:<id> rs_chunks            news:<id>  rs_news_items
  raw:<id>   rs_raw_sources       price:<security_id>:<date>  rs_prices_daily
  metric:<key>  a computed metric inside a snapshot (links to its input fact IDs)
"""
from __future__ import annotations

from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, MetaData,
    String, Table, Text, UniqueConstraint,
)

metadata = MetaData()

companies = Table(
    "rs_companies", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(200), nullable=False),
    Column("isin", String(20)),
    Column("sector", String(120)),
    Column("industry", String(160)),
    Column("listing_date", Date),
    Column("status", String(20), nullable=False, default="listed"),     # listed | suspended | delisted | unknown
    Column("is_financial", Boolean, nullable=False, default=False),
    Column("is_sample", Boolean, nullable=False, default=False),        # fictional fixture company
    Column("deep_research", Boolean, nullable=False, default=False),    # in the deep-research set
    Column("source", String(60), nullable=False),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
    UniqueConstraint("isin", name="uq_rs_company_isin"),
)

securities = Table(
    "rs_securities", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("rs_companies.id", ondelete="CASCADE"), nullable=False),
    Column("exchange", String(10), nullable=False),          # NSE | BSE
    Column("symbol", String(40), nullable=False),
    Column("provider_symbol", String(40)),                   # e.g. DIXON.NS for Yahoo
    Column("series", String(10)),
    Column("status", String(20), nullable=False, default="active"),
    Column("source", String(60), nullable=False),
    UniqueConstraint("exchange", "symbol", name="uq_rs_security"),
)

identifier_history = Table(
    "rs_identifier_history", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("security_id", Integer, ForeignKey("rs_securities.id", ondelete="CASCADE"), nullable=False),
    Column("field", String(30), nullable=False),             # symbol | name | isin
    Column("old_value", String(200)),
    Column("new_value", String(200)),
    Column("effective_date", Date),
    Column("source", String(60)),
    Column("recorded_at", DateTime(timezone=True)),
)

raw_sources = Table(
    "rs_raw_sources", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source_key", String(200), nullable=False),       # logical identity, e.g. yahoo:income:DIXON.NS
    Column("version", Integer, nullable=False),
    Column("provider", String(40), nullable=False),
    Column("url", Text),
    Column("company_id", Integer, ForeignKey("rs_companies.id", ondelete="SET NULL")),
    Column("retrieved_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True)),
    Column("published_at", DateTime(timezone=True)),
    Column("sha256", String(64), nullable=False),
    Column("content_type", String(80)),
    Column("size_bytes", Integer),
    Column("content", LargeBinary),                          # zlib-compressed original bytes
    Column("parser_version", String(20)),
    Column("status", String(20), nullable=False),            # ok | error
    Column("error", Text),
    UniqueConstraint("source_key", "version", name="uq_rs_raw_version"),
)
Index("ix_rs_raw_key", raw_sources.c.source_key)

prices_daily = Table(
    "rs_prices_daily", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("security_id", Integer, ForeignKey("rs_securities.id", ondelete="CASCADE"), nullable=False),
    Column("date", Date, nullable=False),
    Column("open", Float), Column("high", Float), Column("low", Float), Column("close", Float),
    Column("adj_close", Float), Column("volume", Float),
    Column("provider", String(40), nullable=False),
    Column("adjustment", String(30)),                        # split_adjusted | unadjusted | unknown
    Column("raw_source_id", Integer, ForeignKey("rs_raw_sources.id", ondelete="SET NULL")),
    UniqueConstraint("security_id", "date", "provider", name="uq_rs_price"),
)

corporate_actions = Table(
    "rs_corporate_actions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("security_id", Integer, ForeignKey("rs_securities.id", ondelete="CASCADE"), nullable=False),
    Column("date", Date, nullable=False),
    Column("action", String(20), nullable=False),            # split | dividend | bonus
    Column("value", Float),                                  # split ratio (new/old) or dividend ₹/share
    Column("provider", String(40), nullable=False),
    Column("raw_source_id", Integer, ForeignKey("rs_raw_sources.id", ondelete="SET NULL")),
    UniqueConstraint("security_id", "date", "action", "provider", name="uq_rs_action"),
)

financial_facts = Table(
    "rs_financial_facts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("rs_companies.id", ondelete="CASCADE"), nullable=False),
    Column("metric", String(60), nullable=False),            # revenue, ebit, net_income, cfo, ...
    Column("value", Float),
    Column("unit", String(20), nullable=False),              # INR | INR_cr | INR_lakh | INR_mn | shares | ratio
    Column("period_end", Date, nullable=False),
    Column("period_type", String(4), nullable=False),        # FY | Q | TTM
    Column("scope", String(20), nullable=False),             # consolidated | standalone | unknown
    Column("published_at", DateTime(timezone=True)),
    Column("provider", String(40), nullable=False),
    Column("raw_source_id", Integer, ForeignKey("rs_raw_sources.id", ondelete="SET NULL")),
    Column("verification", String(20), nullable=False, default="unverified"),  # unverified | verified | mismatch | manual
    Column("version", Integer, nullable=False, default=1),
    Column("is_current", Boolean, nullable=False, default=True),
    Column("superseded_by", Integer),
    Column("recorded_at", DateTime(timezone=True)),
    Column("note", Text),
)
Index("ix_rs_fact_lookup", financial_facts.c.company_id, financial_facts.c.metric, financial_facts.c.period_end)

documents = Table(
    "rs_documents", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("rs_companies.id", ondelete="CASCADE")),
    Column("raw_source_id", Integer, ForeignKey("rs_raw_sources.id", ondelete="SET NULL")),
    Column("title", String(300), nullable=False),
    Column("doc_type", String(30), nullable=False),          # announcement | results | annual_report | presentation | other
    Column("url", Text),
    Column("published_at", DateTime(timezone=True)),
    Column("pages", Integer),
    Column("text_status", String(20), nullable=False),       # ok | no_text | error | pending
    Column("parser_version", String(20)),
    Column("sha256", String(64)),
    Column("created_at", DateTime(timezone=True)),
    UniqueConstraint("sha256", "company_id", name="uq_rs_document_hash"),
)

chunks = Table(
    "rs_chunks", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("document_id", Integer, ForeignKey("rs_documents.id", ondelete="CASCADE"), nullable=False),
    Column("page", Integer, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("text", Text, nullable=False),
)

announcements = Table(
    "rs_announcements", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("rs_companies.id", ondelete="CASCADE"), nullable=False),
    Column("exchange", String(10), nullable=False),
    Column("external_id", String(60), nullable=False),       # NSE seq_id
    Column("category", String(200)),                         # exchange's own category (NSE "desc")
    Column("event_type", String(40)),                        # our classification
    Column("text", Text),
    Column("published_at", DateTime(timezone=True)),
    Column("attachment_url", Text),
    Column("document_id", Integer, ForeignKey("rs_documents.id", ondelete="SET NULL")),
    Column("raw_source_id", Integer, ForeignKey("rs_raw_sources.id", ondelete="SET NULL")),
    Column("retrieved_at", DateTime(timezone=True)),
    UniqueConstraint("exchange", "external_id", name="uq_rs_announcement"),
)

news_items = Table(
    "rs_news_items", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("rs_companies.id", ondelete="SET NULL")),
    Column("headline", Text, nullable=False),
    Column("publisher", String(120)),
    Column("url", Text, nullable=False),
    Column("published_at", DateTime(timezone=True)),
    Column("retrieved_at", DateTime(timezone=True)),
    Column("summary", Text),
    Column("feed", String(200)),
    Column("dup_group", String(64), nullable=False),         # hash of normalised headline
    Column("match_method", String(30)),                      # symbol | name | none
    Column("raw_source_id", Integer, ForeignKey("rs_raw_sources.id", ondelete="SET NULL")),
    UniqueConstraint("url", name="uq_rs_news_url"),
)

data_issues = Table(
    "rs_data_issues", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("rs_companies.id", ondelete="CASCADE")),
    Column("kind", String(30), nullable=False),              # missing | conflict | stale | parse_error | fetch_error | mismatch | unsupported
    Column("field", String(80), nullable=False),
    Column("detail", Text),
    Column("severity", String(10), nullable=False),          # info | warn | error
    Column("status", String(12), nullable=False),            # open | resolved | ignored
    Column("first_seen", DateTime(timezone=True)),
    Column("last_seen", DateTime(timezone=True)),
    Column("resolved_at", DateTime(timezone=True)),
    Column("run_id", Integer),
)
Index("ix_rs_issue_key", data_issues.c.company_id, data_issues.c.kind, data_issues.c.field)

ingestion_runs = Table(
    "rs_ingestion_runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(30), nullable=False),              # company | sample_batch | news | manual_import | verification
    Column("company_id", Integer, ForeignKey("rs_companies.id", ondelete="SET NULL")),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", String(12), nullable=False),            # running | ok | partial | error
    Column("detail", JSON),
)

research_snapshots = Table(
    "rs_research_snapshots", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("rs_companies.id", ondelete="CASCADE"), nullable=False),
    Column("as_of", Date, nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("output_hash", String(64), nullable=False),
    Column("code_version", String(20), nullable=False),
    Column("model_version", String(80)),
    Column("status", String(30), nullable=False),            # BLOCKED | REVIEW_REQUIRED | ELIGIBLE_FOR_RESEARCH
    Column("dimensions", JSON),
    Column("flags", JSON),
    Column("inputs", JSON),                                  # the exact input record used (for replay)
    Column("output", JSON),
    Column("created_at", DateTime(timezone=True)),
    UniqueConstraint("company_id", "input_hash", "code_version", "model_version", name="uq_rs_snapshot"),
)

watchlists = Table(
    "rs_watchlists", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(120), nullable=False),
    Column("description", Text),
    Column("created_at", DateTime(timezone=True)),
)

watchlist_items = Table(
    "rs_watchlist_items", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("watchlist_id", Integer, ForeignKey("rs_watchlists.id", ondelete="CASCADE"), nullable=False),
    Column("company_id", Integer, ForeignKey("rs_companies.id", ondelete="CASCADE"), nullable=False),
    Column("reason", Text),
    Column("added_at", DateTime(timezone=True)),
    Column("added_snapshot_id", Integer, ForeignKey("rs_research_snapshots.id", ondelete="SET NULL")),
    Column("added_price", Float),
    Column("removed_at", DateTime(timezone=True)),
)

decisions = Table(
    "rs_decisions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("rs_companies.id", ondelete="CASCADE"), nullable=False),
    Column("title", String(200)),
    Column("thesis", Text),
    Column("drivers", Text),
    Column("valuation_assumptions", Text),
    Column("downside_risks", Text),
    Column("invalidation", Text),
    Column("status", String(20), nullable=False, default="open"),   # open | closed | invalidated
    Column("snapshot_id", Integer, ForeignKey("rs_research_snapshots.id", ondelete="SET NULL")),
    Column("price_at_entry", Float),
    Column("follow_ups", JSON),                              # [{date, note, price, outcome}]
    Column("outcome", Text),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)

llm_cache = Table(
    "rs_llm_cache", metadata,
    Column("key", String(64), primary_key=True),             # sha256(task, input hash, model, prompt version)
    Column("task", String(40), nullable=False),
    Column("model", String(80), nullable=False),
    Column("prompt_version", String(20), nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("output", JSON),
    Column("usage", JSON),
    Column("created_at", DateTime(timezone=True)),
)

verifications = Table(
    "rs_verifications", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", String(60), nullable=False),
    Column("symbol", String(40), nullable=False),
    Column("kind", String(20), nullable=False),              # fact | price | action
    Column("field", String(60), nullable=False),
    Column("period", String(20)),
    Column("expected", Float),
    Column("actual", Float),
    Column("tolerance_pct", Float),
    Column("status", String(20), nullable=False),            # match | mismatch | missing | not_checked
    Column("source_note", Text),
    Column("explanation", Text),
    Column("checked_at", DateTime(timezone=True)),
)

settings_kv = Table(
    "rs_settings", metadata,
    Column("key", String(80), primary_key=True),
    Column("value", JSON),
    Column("updated_at", DateTime(timezone=True)),
)

index_members = Table(
    "rs_index_members", metadata,                            # which companies are in each market-cap segment
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("segment", String(10), nullable=False),           # large | mid | small
    Column("index_name", String(60), nullable=False),        # e.g. NIFTY 100
    Column("company_id", Integer, ForeignKey("rs_companies.id", ondelete="CASCADE"), nullable=False),
    Column("symbol", String(40), nullable=False),
    Column("source", String(30), nullable=False),            # niftyindices | bundled
    Column("raw_source_id", Integer, ForeignKey("rs_raw_sources.id", ondelete="SET NULL")),
    Column("as_of", DateTime(timezone=True)),
    UniqueConstraint("segment", "company_id", name="uq_rs_index_member"),
)

schema_version = Table(
    "rs_schema_version", metadata,
    Column("version", Integer, primary_key=True),
    Column("name", String(120), nullable=False),
    Column("applied_at", DateTime(timezone=True)),
)

# Tables whose content belongs to the user (exported/restored by backup)
USER_TABLES = (watchlists, watchlist_items, decisions, settings_kv)
