from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.schemas import (
    MarketRegimeSnapshot,
    PortfolioRiskRequest,
    PortfolioRiskResponse,
    ScanRequest,
    ScanStatusResponse,
    ScanUniverse,
    SectorRotationResponse,
    StockAnalysisResponse,
    StockSearchResult,
)
from app.services.portfolio_risk import compute_portfolio_risk
from app.services.quant_screen import QuantScreenService
from app.services.valuation import FinancialsProvider, build_valuation
from app.services.stock_analysis import analyze_stock, search_stocks
from app.services.db import is_persistent
from app.services.portfolio_backtest import PortfolioBacktestRunner, latest_result
from app.services.scanner import scanner_service

router = APIRouter()
backtest_runner = PortfolioBacktestRunner(scanner_service)


@router.get("/health")
def healthcheck() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "app_release": settings.app_release,
        "market_data_provider": settings.market_data_provider,
        "universe_provider": settings.universe_provider,
        "allow_demo_fallback": settings.allow_demo_fallback,
        "benchmark_symbol": settings.benchmark_symbol,
        "benchmark_symbol_fallbacks": ",".join(settings.benchmark_symbol_fallbacks),
    }


@router.get("/stocks")
def list_stocks(universe: ScanUniverse = ScanUniverse.NIFTY500) -> list[dict[str, str]]:
    return scanner_service.list_stocks(universe=universe)


@router.get("/signals")
def latest_signals(universe: ScanUniverse | None = None):
    return scanner_service.latest_signals(universe=universe)


@router.post("/scan")
def run_scan(request: ScanRequest):
    return scanner_service.run_scan(request)


@router.get("/scan/status")
def get_scan_status() -> ScanStatusResponse:
    return ScanStatusResponse(**scanner_service.scan_status())


@router.get("/regime")
def get_market_regime(refresh: bool = False) -> MarketRegimeSnapshot:
    """Market regime: trend, breadth and volatility context plus recommended thresholds."""
    return scanner_service.market_regime(force=refresh)


@router.get("/sectors")
def get_sector_rotation(universe: ScanUniverse = ScanUniverse.NIFTY500) -> SectorRotationResponse:
    """Top-down sector ranking from the most recent scan of this universe."""
    generated_at, sectors = scanner_service.sector_rotation(universe)
    return SectorRotationResponse(universe=universe, generated_at=generated_at, sectors=sectors)


@router.post("/portfolio/risk")
def get_portfolio_risk(request: PortfolioRiskRequest) -> PortfolioRiskResponse:
    """Beta of each holding vs Nifty, portfolio beta, and correlation of candidates with holdings."""
    context = scanner_service._load_benchmark_context(request.lookback_sessions + 40)
    frame = context.benchmark_frame if context is not None else None
    return compute_portfolio_risk(scanner_service.market_data, frame, request)


@router.get("/performance")
def get_performance():
    """Live signal ledger: how published picks actually played out."""
    summary = scanner_service.ledger.summary()
    summary["persistent_storage"] = is_persistent()
    summary["calibration"] = scanner_service.calibration.info()
    return summary


@router.post("/performance/evaluate")
def evaluate_ledger():
    return scanner_service.ledger.evaluate()


@router.post("/backtest/portfolio")
def start_portfolio_backtest(
    universe: ScanUniverse = ScanUniverse.NIFTY500,
    years: int = 2,
    max_symbols: int | None = None,
):
    # Full-universe runs are CPU-heavy; from the web we test an evenly spread sample.
    # Run `python -m app.jobs.run_portfolio_backtest` locally for the full universe.
    cap = settings.web_backtest_max_symbols
    max_symbols = min(max_symbols or cap, cap)
    years = max(1, min(years, 3))
    started = backtest_runner.start(universe, years, max_symbols)
    return {"started": started, "state": backtest_runner.state}


@router.get("/backtest/portfolio/status")
def portfolio_backtest_status():
    return backtest_runner.state


@router.get("/backtest/portfolio")
def get_portfolio_backtest(universe: ScanUniverse = ScanUniverse.NIFTY500):
    result = latest_result(universe)
    if result is None:
        raise HTTPException(status_code=404, detail="No portfolio backtest has been run for this universe yet.")
    return result


quant_service = QuantScreenService(scanner_service)


@router.get("/quant")
def quant_screen(universe: ScanUniverse = ScanUniverse.NIFTY500, top: int = 20, refresh: bool = False):
    """Multi-factor quant ranking of the whole universe, with its own historical validation."""
    return quant_service.run(universe, max(5, min(top, 50)), refresh=refresh)


financials_provider = FinancialsProvider(scanner_service.fundamentals)


@router.get("/valuation/{symbol}")
def valuation(
    symbol: str,
    method: str | None = None,
    base_cash_flow: float | None = None,
    growth: float | None = None,
    terminal: float | None = None,
    discount: float | None = None,
    mos: float | None = None,
    shares: float | None = None,
):
    """DCF + reverse DCF. Assumptions are optional overrides (percent values)."""
    if method not in (None, "fcf", "earnings"):
        raise HTTPException(status_code=400, detail="method must be 'fcf' or 'earnings'")
    overrides = {"method": method, "base_cash_flow": base_cash_flow, "growth_pct": growth,
                 "terminal_growth_pct": terminal, "discount_rate_pct": discount, "margin_of_safety_pct": mos,
                 "shares": shares if shares and shares > 0 else None}
    result = build_valuation(scanner_service, financials_provider, symbol, overrides)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No price data for {symbol.upper()}.")
    return result


@router.get("/search")
def search(q: str, limit: int = 10) -> list[StockSearchResult]:
    """Find stocks by symbol or company name across all scan universes."""
    return search_stocks(q, max(1, min(limit, 25)))


@router.get("/analyze/{symbol}")
def analyze(symbol: str) -> StockAnalysisResponse:
    """Technical + fundamental analysis with an overall A–E rating."""
    result = analyze_stock(scanner_service, symbol)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No price data found for {symbol.upper()}. Check the NSE symbol.")
    return result


@router.get("/stock/{symbol}")
def get_stock_detail(symbol: str):
    detail = scanner_service.get_stock_detail(symbol)
    if detail is None:
        raise HTTPException(status_code=404, detail="Stock not found in the current universe.")
    return detail


@router.get("/backtest/{symbol}")
def get_backtest(symbol: str):
    stats = scanner_service.get_backtest(symbol)
    if stats is None:
        raise HTTPException(
            status_code=404,
            detail="Backtest unavailable because no active pattern was found for this symbol.",
        )
    return stats
