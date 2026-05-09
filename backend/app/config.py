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


def _parse_csv_env(name: str) -> tuple[str, ...]:
    raw = os.getenv(name)
    if not raw:
        return ()

    values = tuple(
        value.strip()
        for value in raw.split(",")
        if value.strip()
    )
    return values


def _parse_benchmark_symbol_fallbacks() -> tuple[str, ...]:
    fallbacks = _parse_csv_env("BENCHMARK_SYMBOL_FALLBACKS")
    if fallbacks:
        return fallbacks

    return ("NIFTYBEES.NS",)


def _resolve_universe_provider() -> str:
    provider = os.getenv("UNIVERSE_PROVIDER", "bundled_csv").strip().lower()
    if (
        provider == "static"
        and os.getenv("RENDER") == "true"
        and os.getenv("FORCE_STATIC_UNIVERSE", "0") != "1"
    ):
        return "bundled_csv"
    if (
        provider == "nse"
        and os.getenv("RENDER") == "true"
        and os.getenv("FORCE_LIVE_NSE", "0") != "1"
    ):
        return "bundled_csv"
    return provider


@dataclass(frozen=True)
class Settings:
    app_name: str = "NSE AI Swing Scanner"
    app_release: str = os.getenv("APP_RELEASE", "dev")
    app_description: str = (
        "Offline-friendly MVP for scanning NSE swing setups using rule-based "
        "patterns, indicator confirmation, and lightweight backtesting."
    )
    api_prefix: str = "/api"
    scan_workers: int = int(os.getenv("SCAN_WORKERS", "8"))
    async_scan_universe_threshold: int = int(
        os.getenv("ASYNC_SCAN_UNIVERSE_THRESHOLD", "500")
    )
    default_investment_amount: int = int(
        os.getenv("DEFAULT_INVESTMENT_AMOUNT", "100000")
    )
    default_scan_lookback: int = int(os.getenv("DEFAULT_SCAN_LOOKBACK", "300"))
    liquidity_filter_min_avg_traded_value_inr: float = float(
        os.getenv("LIQUIDITY_FILTER_MIN_AVG_TRADED_VALUE_INR", "50000000")
    )
    event_risk_review_limit: int = int(os.getenv("EVENT_RISK_REVIEW_LIMIT", "80"))
    event_risk_window_days: int = int(os.getenv("EVENT_RISK_WINDOW_DAYS", "7"))
    event_risk_high_penalty_days: int = int(
        os.getenv("EVENT_RISK_HIGH_PENALTY_DAYS", "3")
    )
    event_risk_post_result_cooloff_days: int = int(
        os.getenv("EVENT_RISK_POST_RESULT_COOLOFF_DAYS", "2")
    )
    event_data_cache_ttl_minutes: int = int(
        os.getenv("EVENT_DATA_CACHE_TTL_MINUTES", "360")
    )
    delivery_data_cache_ttl_minutes: int = int(
        os.getenv("DELIVERY_DATA_CACHE_TTL_MINUTES", "1440")
    )
    delivery_data_sessions: int = int(os.getenv("DELIVERY_DATA_SESSIONS", "10"))
    nse_delivery_archive_base_url: str = os.getenv(
        "NSE_DELIVERY_ARCHIVE_BASE_URL",
        "https://archives.nseindia.com/archives/equities/mto",
    )
    market_data_provider: str = os.getenv("MARKET_DATA_PROVIDER", "auto").lower()
    universe_provider: str = _resolve_universe_provider()
    allow_demo_fallback: bool = os.getenv("ALLOW_DEMO_FALLBACK", "1") == "1"
    cache_dir: Path = Path(os.getenv("DATA_CACHE_DIR", ".cache"))
    market_data_cache_ttl_minutes: int = int(
        os.getenv("MARKET_DATA_CACHE_TTL_MINUTES", "240")
    )
    yahoo_batch_size: int = int(os.getenv("YAHOO_BATCH_SIZE", "100"))
    market_data_memory_cache_symbols: int = int(
        os.getenv("MARKET_DATA_MEMORY_CACHE_SYMBOLS", "64")
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
    nse_equity_fallback_csv_url: str = os.getenv(
        "NSE_EQUITY_FALLBACK_CSV_URL",
        "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
    )
    benchmark_symbol: str = os.getenv("BENCHMARK_SYMBOL", "^NSEI")
    benchmark_name: str = os.getenv("BENCHMARK_NAME", "NIFTY 50")
    benchmark_symbol_fallbacks: tuple[str, ...] = _parse_benchmark_symbol_fallbacks()
    email_smtp_host: str = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
    email_smtp_port: int = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    email_smtp_starttls: bool = os.getenv("EMAIL_SMTP_STARTTLS", "1") == "1"
    email_smtp_username: str = os.getenv("EMAIL_SMTP_USERNAME", "")
    email_smtp_password: str = os.getenv("EMAIL_SMTP_PASSWORD", "")
    email_from_address: str = os.getenv("EMAIL_FROM_ADDRESS", "")
    email_report_recipients: tuple[str, ...] = _parse_csv_env("EMAIL_REPORT_RECIPIENTS")
    email_report_timezone: str = os.getenv("EMAIL_REPORT_TIMEZONE", "Asia/Kolkata")
    email_report_max_results: int = int(os.getenv("EMAIL_REPORT_MAX_RESULTS", "12"))
    email_report_min_probability: float = float(
        os.getenv("EMAIL_REPORT_MIN_PROBABILITY", "0.55")
    )
    email_report_min_risk_reward: float = float(
        os.getenv("EMAIL_REPORT_MIN_RISK_REWARD", "1.8")
    )
    email_report_enabled: bool = os.getenv("EMAIL_REPORT_ENABLED", "1") == "1"
    cors_origins: tuple[str, ...] = _parse_cors_origins()


settings = Settings()
