from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.schemas import ScanRequest, ScanStatusResponse, ScanUniverse
from app.services.scanner import scanner_service

router = APIRouter()


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
