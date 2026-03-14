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
- `UNIVERSE_PROVIDER=nse`: load the official NSE equity list from the exchange CSV
- `UNIVERSE_PROVIDER=auto`: try the official NSE CSV, then fall back to the built-in basket

The default setup keeps the curated universe for speed, while using live Yahoo
Finance candles whenever network access is available.

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
$env:UNIVERSE_PROVIDER="nse"
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

- `nse-ai-scanner-api`: FastAPI backend
- `nse-ai-scanner-web`: Next.js frontend

Before using it on Render:

1. Push this repo to GitHub or another Git provider supported by Render.
2. In Render, create a new Blueprint service from that repo.
3. If the default service names are already taken on Render, rename them in
   `render.yaml` and update both:
   `CORS_ORIGINS` and `NEXT_PUBLIC_API_BASE_URL`.

## API endpoints

- `GET /api/health`
- `GET /api/stocks`
- `GET /api/signals`
- `POST /api/scan`
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
official exchange CSV, but sector and market-cap values are only fully known for
the built-in curated basket. Unknown live symbols currently default to
`sector=Unknown` and `small_cap`.

## What to build next

1. Persist candles and signals in PostgreSQL instead of in-memory storage.
2. Add Redis workers for batch scans.
3. Integrate the trained model artifact into the probability engine.
4. Swap the SVG chart for TradingView or Lightweight Charts.
5. Add richer company metadata for the full NSE universe so sector and market-cap filters stay accurate.
