from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _parse_cors_origins() -> tuple[str, ...]:
    raw = os.getenv("CORS_ORIGINS")
    if raw:
        origins = tuple(
            origin.strip()
            for origin in raw.split(",")
            if origin.strip()
        )
        if origins:
            return origins

    return (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    )


def _resolve_universe_provider() -> str:
    provider = os.getenv("UNIVERSE_PROVIDER", "nse").strip().lower()
    if (
        provider == "static"
        and os.getenv("RENDER") == "true"
        and os.getenv("FORCE_STATIC_UNIVERSE", "0") != "1"
    ):
        return "nse"
    return provider


@dataclass(frozen=True)
class Settings:
    app_name: str = "NSE AI Swing Scanner"
    app_description: str = (
        "Offline-friendly MVP for scanning NSE swing setups using rule-based "
        "patterns, indicator confirmation, and lightweight backtesting."
    )
    api_prefix: str = "/api"
    scan_workers: int = int(os.getenv("SCAN_WORKERS", "8"))
    default_investment_amount: int = int(
        os.getenv("DEFAULT_INVESTMENT_AMOUNT", "100000")
    )
    default_scan_lookback: int = int(os.getenv("DEFAULT_SCAN_LOOKBACK", "300"))
    market_data_provider: str = os.getenv("MARKET_DATA_PROVIDER", "auto").lower()
    universe_provider: str = _resolve_universe_provider()
    allow_demo_fallback: bool = os.getenv("ALLOW_DEMO_FALLBACK", "1") == "1"
    cache_dir: Path = Path(os.getenv("DATA_CACHE_DIR", ".cache"))
    market_data_cache_ttl_minutes: int = int(
        os.getenv("MARKET_DATA_CACHE_TTL_MINUTES", "240")
    )
    universe_cache_ttl_minutes: int = int(
        os.getenv("UNIVERSE_CACHE_TTL_MINUTES", "1440")
    )
    yahoo_timeout_seconds: int = int(os.getenv("YAHOO_TIMEOUT_SECONDS", "20"))
    nse_timeout_seconds: int = int(os.getenv("NSE_TIMEOUT_SECONDS", "20"))
    nse_equity_csv_url: str = os.getenv(
        "NSE_EQUITY_CSV_URL",
        "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    )
    benchmark_symbol: str = os.getenv("BENCHMARK_SYMBOL", "^NSEI")
    benchmark_name: str = os.getenv("BENCHMARK_NAME", "NIFTY 50")
    cors_origins: tuple[str, ...] = _parse_cors_origins()


settings = Settings()
