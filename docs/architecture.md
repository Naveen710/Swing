# NSE AI Swing Scanner Architecture

## Current MVP

The repo is intentionally split so you can iterate toward a full SaaS product:

- `backend/app/main.py`: FastAPI app and API mounting
- `backend/app/api.py`: scanner, stock, and signal endpoints
- `backend/app/services/market_data.py`: live Yahoo Finance provider, cache, and fallback factory
- `backend/app/services/demo_market_data.py`: deterministic offline fallback provider
- `backend/app/services/universe.py`: curated universe plus official NSE CSV loader
- `backend/app/services/relative_strength.py`: benchmark-relative strength versus NIFTY
- `backend/app/services/indicators.py`: EMA, RSI, ATR, and volume calculations
- `backend/app/services/patterns.py`: codified swing setups including relative-strength breakout
- `backend/app/services/backtest.py`: lightweight setup outcome evaluation
- `frontend/app`: Next.js app router views
- `frontend/components`: dashboard, detail page, and chart primitives
- `render.yaml`: Render blueprint for the FastAPI API and Next.js frontend

## Live data design

The scanner now has a provider boundary instead of a hard-coded demo feed:

- Yahoo Finance supplies daily OHLCV history for the current MVP
- The official NSE equity CSV can supply the broader symbol universe
- Disk cache keeps repeated scans faster and provides resilience when refreshes fail
- Demo fallback keeps the app usable in offline or dependency-light environments

## Recommended next upgrades

1. Persist live candles and signals in PostgreSQL so scans stop depending on just-in-time downloads.
2. Move scans into a Redis-backed worker queue for real parallel execution.
3. Load the trained probability model instead of the heuristic score.
4. Add TradingView or Lightweight Charts on the stock detail page.
5. Introduce auth, watchlists, alerts, and portfolio journal tables.
