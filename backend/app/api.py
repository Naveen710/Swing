from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.schemas import MarketRegimeSnapshot, ScanRequest, ScanStatusResponse, ScanUniverse
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
