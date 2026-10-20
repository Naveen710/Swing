# NSE AI Swing Scanner MVP

This repo turns the product idea into a working starter project instead of only
an AI prompt. It includes:

- FastAPI backend with a scanner API
- Live Yahoo Finance daily-price integration with local cache
- Optional NSE official equity-universe loader from the exchange CSV
- NIFTY-relative strength overlay for ranking the best setups versus the benchmark
- Rule-based swing setup detection for:
  - consolidation breakout
  - EMA pullback
  - relative strength breakout
  - support bounce
  - volatility contraction pattern
- Lightweight historical backtest metrics per signal
- Next.js dashboard and stock detail pages
- Docker Compose for local development
- ML training stub for replacing heuristic probability scores later

## Live data behavior

The backend now supports three runtime modes:

- `MARKET_DATA_PROVIDER=auto`: try Yahoo Finance first, then fall back to demo data
- `MARKET_DATA_PROVIDER=yahoo`: use live Yahoo Finance OHLCV data
- `MARKET_DATA_PROVIDER=demo`: force synthetic data for offline development

For the stock universe:

- `UNIVERSE_PROVIDER=static`: use the curated built-in NSE basket
- `UNIVERSE_PROVIDER=bundled_csv`: use the bundled `backend/data/nse_equities.csv` snapshot
- `UNIVERSE_PROVIDER=nse`: load the official NSE equity list from the exchange CSV
- `UNIVERSE_PROVIDER=auto`: try the official NSE CSV, then fall back to the built-in basket

The default setup now uses the official NSE equity list, so `/api/stocks` and
the scanner can work from the full exchange universe instead of only the
curated 20-stock basket.

If you want to control the scan universe manually, comment rows in
`backend/data/nse_equities.csv` and set `UNIVERSE_PROVIDER=bundled_csv`. Commented
rows that start with `#` are ignored by the loader.

If you want to force the old curated basket again, set
`FORCE_STATIC_UNIVERSE=1` together with `UNIVERSE_PROVIDER=static`.

On Render, the app now prefers `bundled_csv` over live `nse` by default so the
committed CSV controls the scan universe. Set `FORCE_LIVE_NSE=1` only if you
want to bypass the bundled file and fetch the full exchange list again.

Large scans are warmed in batched Yahoo Finance downloads before pattern
evaluation starts, which cuts the first-pass scan time materially compared with
fetching each symbol one by one.

Full-universe scans can also switch to a background-refresh flow instead of
holding the HTTP request open. When the universe size crosses
`ASYNC_SCAN_UNIVERSE_THRESHOLD`, `POST /api/scan` returns immediately with the
latest cached results and the frontend polls `GET /api/scan/status` until the
fresh scan is done.

The `Mid & Small 2000+` universe now fans out into `MID_SMALL_PARALLEL_WORKERS`
parallel background workers by default. The Render blueprint sets that to `10`
so the broad discovery scan starts 10 worker partitions as soon as the button
is clicked.

To keep small hosted instances stable, the Yahoo provider also caps its
in-process dataframe cache with `MARKET_DATA_MEMORY_CACHE_SYMBOLS` instead of
retaining the whole exchange universe in RAM.

For serious or commercial deployment, treat `yfinance` as a prototyping source.
It is excellent for development, but you should replace it with a licensed data
provider before shipping a paid scanner.

The scanner benchmark defaults to `^NSEI` (`NIFTY 50`) and is used as a
relative-strength overlay on top of the chart-pattern engine.

## Project structure

```text
backend/
  app/
  training/
frontend/
docs/
docker-compose.yml
```

## Local development

### Backend

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r backend/requirements.txt
uvicorn app.main:app --app-dir backend --reload
```

Backend URL: `http://localhost:8000`

Optional environment variables:

```bash
$env:MARKET_DATA_PROVIDER="yahoo"
$env:UNIVERSE_PROVIDER="bundled_csv"
$env:FORCE_STATIC_UNIVERSE="0"
$env:FORCE_LIVE_NSE="0"
$env:YAHOO_BATCH_SIZE="100"
$env:MARKET_DATA_MEMORY_CACHE_SYMBOLS="64"
$env:ASYNC_SCAN_UNIVERSE_THRESHOLD="500"
$env:ALLOW_DEMO_FALLBACK="1"
$env:CORS_ORIGINS="http://localhost:3000,http://127.0.0.1:3000"
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend URL: `http://localhost:3000`

## Docker

```bash
docker compose up --build
```

## Render

The repo now includes [render.yaml](/C:/Users/prave/OneDrive/Documents/Playground/render.yaml)
with a two-service blueprint:

- `naveen710-swing-api`: FastAPI backend
- `naveen710-swing-web`: Next.js frontend
- `naveen710-swing-daily-email`: daily cron job that scans all three universes
  and emails the ranked results at 9:00 AM IST (`30 3 * * *` in Render's UTC
  cron schedule)

Before using it on Render:

1. Push this repo to GitHub or another Git provider supported by Render.
2. In Render, create a new Blueprint service from that repo.
3. If the default service names are already taken on Render, rename them in
   `render.yaml` and update both:
   `CORS_ORIGINS` and `NEXT_PUBLIC_API_BASE_URL`.
4. For the daily email job, set these environment variables in Render:
   `EMAIL_FROM_ADDRESS`, `EMAIL_SMTP_USERNAME`, and `EMAIL_SMTP_PASSWORD`.
   Gmail works well here with `smtp.gmail.com:587` and an app password.
5. The cron service uses a paid Render plan because Render cron jobs are not
   available on the free tier.

## API endpoints

- `GET /api/health`
- `GET /api/stocks`
- `GET /api/signals`
- `POST /api/scan`
- `GET /api/scan/status`
- `GET /api/stock/{symbol}`
- `GET /api/backtest/{symbol}`

### Example scan payload

```json
{
  "max_results": 12,
  "min_probability": 0.6,
  "min_risk_reward": 2.0,
  "investment_amount": 100000,
  "sectors": ["IT"]
}
```

## Caching

- Market data is cached under `.cache/market-data/yahoo`
- The official NSE universe CSV is cached under `.cache/universe`
- If live refresh fails and cache exists, the backend reuses cached data
- If both live data and cache fail, `ALLOW_DEMO_FALLBACK=1` lets the scanner stay usable

## Universe metadata caveat

When `UNIVERSE_PROVIDER=nse`, symbol and company-name coverage come from the
official exchange CSV. Sector and market-cap values are still most accurate for
the built-in curated basket, while the broader NSE list uses lightweight sector
guessing and defaults unknown names to `small_cap`.

## Signal ledger, portfolio backtest and calibration

Every published pick is written to a database and graded later with the same
entry, stop, target, 20-session time stop and 0.25% round-trip costs that the
backtests use (`app/services/trade_sim.py` is the single source of truth).

- Set `DATABASE_URL` (any Postgres URL) on **both** the API service and the daily
  cron job so they share one ledger. Without it, a local SQLite file is used,
  which is wiped whenever a Render instance restarts.
- The daily cron job now always scans all three universes and grades open
  signals; `EMAIL_REPORT_ENABLED` only controls whether the email is sent.
- Portfolio backtests started from the website test an evenly spread sample of
  `WEB_BACKTEST_MAX_SYMBOLS` (default 120) stocks. For the full universe, run it
  on your own machine against the production database:

```bash
cd backend
pip install -r requirements.txt
export MARKET_DATA_PROVIDER=yahoo UNIVERSE_PROVIDER=bundled_csv
export DATABASE_URL="<your Postgres URL>"   # Windows PowerShell: $env:DATABASE_URL="..."
python -m app.jobs.run_portfolio_backtest --universe nifty500 --years 2
```

  The result and the recalibrated score appear on the Performance page.
- Data-quality guard: stocks whose last bar lags the market by more than
  `STALE_DATA_MAX_LAG_DAYS`, that show a one-day move above
  `STALE_DATA_MAX_GAP_PCT` (usually an unadjusted split), or that have no volume
  for 5 sessions are skipped and listed after each scan.
- Setups whose structural stop is more than `MAX_STOP_PCT` (default 8%) below
  entry are skipped.

## What to build next

1. Persist candles and signals in PostgreSQL instead of in-memory storage.
2. Add Redis workers for batch scans.
3. Integrate the trained model artifact into the probability engine.
4. Swap the SVG chart for TradingView or Lightweight Charts.
5. Add richer company metadata for the full NSE universe so sector and market-cap filters stay accurate.

## Research platform (`/research`)

An evidence-first research and discovery workspace for Indian listed companies, separate from the swing scanner.
Pages: dashboard, company explorer, company dossier, discovery queue, news & events, evidence viewer,
watchlists, decision journal, data quality and settings. It is **read-only**: it never places orders, and
it never tells you to buy anything.

How it works:
- **Data** comes from free sources first (NSE master list and announcements, Yahoo statements/prices,
  RSS news) plus your own CSV/Excel/PDF imports. Every payload is archived with a SHA-256 hash and version;
  failures and gaps are logged on the Data quality page and are never filled with invented numbers.
- **Facts** are versioned: restatements keep the original and mark the newer one current.
- **Research snapshots** are deterministic: Python computes every metric, accounting check, bear/base/bull
  scenario and the seven separate dimensions (quality, growth, strength, valuation, governance, data
  confidence, recognition gap) from a recorded input bundle, and `replay` reproduces the output hash exactly.
  Status is `BLOCKED`, `REVIEW_REQUIRED` or `ELIGIBLE_FOR_RESEARCH`.
- **AI is optional** and limited to extracting quoted statements from filings and narrating computed claims.
  Every claim is typed Sourced / Computed / Inference and links to its evidence; unverifiable output is rejected.
- **Sample companies** (symbols `SMP…`, marked "(sample)") are fictional fixtures for trying the workflow offline.

Run locally:
```bash
cd backend && python -m app.research.cli seed            # fictional sample companies, offline
python -m app.research.cli ingest RELIANCE               # one real company (needs network to NSE/Yahoo)
python -m app.research.cli ingest-deep                   # the 20-company deep-research set
python -m app.research.cli news                          # refresh RSS news
python -m app.research.cli dossier SMPCABLE --md         # print a dossier
python -m app.research.cli replay <snapshot_id>          # prove a snapshot is reproducible
python -m app.research.cli verify app/research/verification/sample_cases.csv
```
Or open `/research` and press **Load sample companies**.

**Large / Mid / Small cap buttons.** The research dashboard has three buttons — Large cap (NIFTY 100), Mid cap
(NIFTY Midcap 150) and Small cap (NIFTY Smallcap 250); together they are the NIFTY 500 (SEBI's size classes:
top 100, 101–250, 251–500 by market cap). Pressing one downloads the current constituent list from NSE Indices
(niftyindices.com, archived with its hash; falls back to the lists bundled in `backend/data/` if the download
fails), then fetches every company in that list in the background and opens a sortable table of status, price,
valuation, returns, growth and dimension scores. Companies refreshed in the last 24 hours are skipped
("Fetch latest data" again, or `--force` on the CLI, to redo them). Filing PDFs are not downloaded for whole
segments — open a company's dossier and press Refresh for that.
```bash
python -m app.research.cli lists             # just refresh the three constituent lists
python -m app.research.cli segment mid       # fetch all Mid cap companies in the foreground
python -m app.research.cli templates         # regenerate the per-segment verification templates
```
Verification templates are per segment: `app/research/verification/real_cases_{large,mid,small}_cap.csv`
(FY revenue and net profit for every constituent, blank for you to fill from annual reports), also downloadable
from each segment page. Upload a filled file on the Data quality page.

Environment (API service, all optional):
| Variable | Purpose |
|---|---|
| `RESEARCH_PASSWORD`, `RESEARCH_SECRET` | Require sign-in for all `/api/research` endpoints. Set these on any public deployment. |
| `RESEARCH_LLM_API_KEY` (or `ANTHROPIC_API_KEY`), `RESEARCH_LLM_MODEL` | Enable AI extraction/narration (default model `claude-haiku-5-5`). |
| `RESEARCH_SAMPLE_SYMBOLS` | Comma-separated deep-research set (default: 20 large/mid caps). |
| `RESEARCH_NEWS_FEEDS` | Comma-separated RSS URLs. |

Search uses SQLite FTS5 locally and falls back to `ILIKE` on Postgres. Backup/restore of watchlists, journal
and preferences is on the Settings page. To refresh daily, add `python -m app.research.cli ingest-deep` to a cron job.

Tests: `python -m pytest -q tests/test_research_*.py` (add `TEST_DATABASE_URL=postgresql://…` to run against a
throwaway Postgres), `npm test` in `frontend/`, and the browser walkthrough
`node frontend/e2e/research.e2e.mjs` against running servers (`E2E_BASE_URL`, `CHROMIUM_PATH` optional; needs
`npm i -D playwright`).

## AI agent team (Claude Code)

Open this repo in Claude Code and the agents in `.claude/agents/` are available:

| Agent | Role |
|---|---|
| `planner` | 30-year Indian-equities strategist + product planner. Writes a testable spec with a validation plan. |
| `developer` | Implements the spec with tests. |
| `tester` | Runs the suites, checks every acceptance criterion and edge case, runs the validation backtest. |
| `reviewer` | Code + quant-methodology review (look-ahead, survivorship, costs, overfitting, security, Render limits). |
| `deployer` | Branch, PR, CI, then merge and Render deploy only after you confirm. |

- `/ship-feature <idea>` runs plan → develop → test → review → deploy, pausing for your approval after the plan and before deploying.
- `/find-multibaggers [focus]` asks the planner to turn its multibagger instincts into a testable screen.
- Tests: `cd backend && python -m pytest -q` and `cd frontend && npm test`. CI runs both on every PR (`.github/workflows/ci.yml`).
