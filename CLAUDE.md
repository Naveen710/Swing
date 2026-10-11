# Swing — NSE swing-trading scanner

Owner: Naveen (swing trader, ~₹18L capital, goal ₹25L; wants steady 2–4%/month, not lottery tickets).
Everything in this repo exists to help one person pick better Indian stocks and manage risk.

## Layout
- `backend/` FastAPI (Python 3.13). Routes in `app/api.py`, prefix `/api`. Logic in `app/services/`.
  - `trade_sim.py` is the single source of truth for entry/stop/target/fills/costs. Every backtest,
    the ledger and the live scanner must use it — never re-implement trade simulation.
  - Market data: Yahoo via `market_data.py`; `MARKET_DATA_PROVIDER=demo` gives deterministic offline data.
  - Persistence: `db.py` `save_artifact/load_artifact` (Postgres when `DATABASE_URL` is set, else temp disk).
  - Pages/services: scanner, signal ledger + calibration, portfolio backtest, quant screen (`quant_screen.py`),
    DCF/reverse DCF (`valuation.py`), curated 5-layer Algo (`curated_algo.py`), VCP (`vcp_scanner.py`),
    stock analysis A–E (`stock_analysis.py`), regime, sector rotation, portfolio risk.
- `frontend/` Next.js 15 app router, React 19, TypeScript strict. API client `lib/api.ts`, types `types.ts`,
  journal/watchlist/settings in browser localStorage (`lib/store.ts`), exit engine `lib/exits.ts`.
- `render.yaml` Render blueprint: `naveen710-swing-api` (Python), `naveen710-swing-web` (Node), daily cron job.
  Free plan = 512 MB RAM and slow CPU: anything scanning a whole universe must be cached, sampled or async.

## Commands
- Backend tests: `cd backend && python -m pytest -q` (offline; `-m "not slow"` for the quick set).
- Frontend: `cd frontend && npm ci && npm run typecheck && npm test && npm run build`.
- Run locally: `cd backend && MARKET_DATA_PROVIDER=demo uvicorn app.main:app --reload` and `cd frontend && npm run dev`.
- CI (`.github/workflows/ci.yml`) runs all of the above on every PR.

## Non-negotiable rules for any change
1. No look-ahead: a signal on day T may only use data up to T's close; fills happen on T+1 or later.
2. Costs always on: 0.25% round trip in `trade_sim` (Algo uses 0.10% + 0.03% + 0.15% per side).
3. Survivorship: the bundled universe is today's constituents — say so in any backtest result.
4. A strategy is not "better" until it beats the current one out of sample, after costs, with enough trades
   (Algo targets: expectancy > 0.3R, profit factor > 1.5, ≥ 200 trades).
5. Never hard-code secrets. Never print, commit or log tokens, `DATABASE_URL`, SMTP passwords or deploy hooks.
6. Every new pure function gets a known-answer test; every new endpoint gets a smoke test in `tests/test_api.py`.
7. Educational tool, not investment advice: no output should tell the user to buy a named stock with certainty.

## Agent team
`.claude/agents/`: planner → developer → tester → reviewer → deployer. `/ship-feature <idea>` runs the whole
pipeline (`.claude/commands/ship-feature.md`). Hand-offs are files in `docs/features/<slug>/`.
