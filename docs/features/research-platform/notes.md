# Indian Stock Research & Discovery Platform — implementation notes

Source spec: `Claude_Code_Instructions_Indian_Stock_Research_Platform.docx` (two stretches).

## Stretch One — data foundation and deterministic engine (`backend/app/research/`)
- `models.py`, `db.py`: `rs_*` tables, versioned migrations (`rs_schema_version`), FTS5 on SQLite.
- `archive.py`: raw payload archive (zlib, SHA-256, new version only on change, errors recorded).
- `providers/`: NSE (master, announcements, attachments), Yahoo (prices, statements), RSS news,
  manual CSV/Excel facts, fictional fixtures.
- `ingest.py`: per-company ingestion, fact versioning/restatements, issue log, background batch job.
- `metrics.py`, `checks.py`, `scenarios.py`, `dimensions.py`, `engine.py`: pure, sector-aware computation,
  seven separate dimensions, status decision, reproducible snapshots (`replay`).
- `verify.py` + `verification/`: verification harness; 31 sample cases; a blank template for 40 real checks.
- `cli.py`: seed, ingest, ingest-deep, news, dossier, replay, verify.

## Stretch Two — website, AI and workflow
- `api.py` (`/api/research/*`), `auth.py` (optional password, HMAC tokens), `llm.py` (extraction/narration
  with quote and number validation, cached), `service.py` (queries, watchlists, journal, backup/restore).
- Frontend: `frontend/app/research/*`, `frontend/components/research/*`, `frontend/lib/research.ts`.

## Deviations from the spec (deliberate)
- **Next.js, not Vite**: the repo already ships a Next.js app on Render; the research pages live under `/research`.
- **Shared database**: tables are prefixed `rs_` in the same DB (SQLite locally, Postgres on Render).
- **Postgres search** uses `ILIKE`, not FTS5 (FTS5 is SQLite-only). Ranking is simpler there.
- **Sample companies are fictional** (`SMP…`) so the whole workflow is testable offline; the 31 sample
  verification cases are therefore partly circular. Real-company verification uses
  `verification/real_cases_template.csv`, which must be filled in by hand from annual reports.

## Known limitations
- Live ingestion (NSE, Yahoo, news sites) could not be exercised from the build sandbox — those hosts were
  blocked. Response shapes were checked against live samples, but expect the first real run to surface issues
  on the Data quality page. NSE may also block cloud IPs such as Render's.
- No order placement, no buy/sell recommendations by design.
- Free Render plan: the 20-company refresh takes minutes and runs in a background thread; it stops if the
  instance sleeps.

## Tests
- Backend: `tests/test_research_metrics.py`, `test_research_ingest.py`, `test_research_api.py`
  (pass on SQLite and on Postgres 16 via `TEST_DATABASE_URL`).
- Frontend unit: `frontend/tests/research.test.ts`.
- Browser: `frontend/e2e/research.e2e.mjs` (18 steps, desktop and phone widths).
