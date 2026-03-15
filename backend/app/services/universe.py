from __future__ import annotations

import csv
import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from app.config import settings
from app.schemas import MarketCapBucket, StockSummary

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StockListing:
    symbol: str
    company_name: str
    sector: str
    market_cap_bucket: MarketCapBucket

    def to_summary(self) -> StockSummary:
        return StockSummary(
            symbol=self.symbol,
            company_name=self.company_name,
            sector=self.sector,
            market_cap_bucket=self.market_cap_bucket,
        )


DEFAULT_UNIVERSE: tuple[StockListing, ...] = (
    StockListing("RELIANCE.NS", "Reliance Industries", "Energy", MarketCapBucket.LARGE),
    StockListing("TCS.NS", "Tata Consultancy Services", "IT", MarketCapBucket.LARGE),
    StockListing("INFY.NS", "Infosys", "IT", MarketCapBucket.LARGE),
    StockListing("HDFCBANK.NS", "HDFC Bank", "Financials", MarketCapBucket.LARGE),
    StockListing("ICICIBANK.NS", "ICICI Bank", "Financials", MarketCapBucket.LARGE),
    StockListing("SBIN.NS", "State Bank of India", "Financials", MarketCapBucket.LARGE),
    StockListing("LT.NS", "Larsen & Toubro", "Industrials", MarketCapBucket.LARGE),
    StockListing("AXISBANK.NS", "Axis Bank", "Financials", MarketCapBucket.LARGE),
    StockListing("BHARTIARTL.NS", "Bharti Airtel", "Telecom", MarketCapBucket.LARGE),
    StockListing("SUNPHARMA.NS", "Sun Pharma", "Healthcare", MarketCapBucket.LARGE),
    StockListing("TITAN.NS", "Titan", "Consumer", MarketCapBucket.LARGE),
    StockListing("ASIANPAINT.NS", "Asian Paints", "Materials", MarketCapBucket.LARGE),
    StockListing("BAJFINANCE.NS", "Bajaj Finance", "Financials", MarketCapBucket.LARGE),
    StockListing("ADANIPORTS.NS", "Adani Ports", "Industrials", MarketCapBucket.LARGE),
    StockListing("HAL.NS", "Hindustan Aeronautics", "Industrials", MarketCapBucket.MID),
    StockListing("POLYCAB.NS", "Polycab India", "Industrials", MarketCapBucket.MID),
    StockListing("LTIM.NS", "LTIMindtree", "IT", MarketCapBucket.MID),
    StockListing("BALKRISIND.NS", "Balkrishna Industries", "Materials", MarketCapBucket.MID),
    StockListing("KPITTECH.NS", "KPIT Technologies", "IT", MarketCapBucket.MID),
    StockListing("CDSL.NS", "Central Depository Services", "Financials", MarketCapBucket.MID),
)

STATIC_METADATA = {listing.symbol.upper(): listing for listing in DEFAULT_UNIVERSE}
BENCHMARK_LISTING = StockListing(
    settings.benchmark_symbol,
    settings.benchmark_name,
    "Benchmark",
    MarketCapBucket.LARGE,
)


class UniverseProviderError(RuntimeError):
    """Raised when the requested stock universe could not be loaded."""


class NseEquityCsvUniverseProvider:
    def __init__(
        self,
        cache_dir: Path,
        cache_ttl_minutes: int,
        source_url: str,
        fallback_source_url: str,
        timeout_seconds: int,
    ) -> None:
        self.cache_path = cache_dir / "universe" / "nse_equities.csv"
        self.bundled_snapshot_path = (
            Path(__file__).resolve().parents[2] / "data" / "nse_equities.csv"
        )
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_seconds = cache_ttl_minutes * 60
        self.source_url = source_url
        self.fallback_source_url = fallback_source_url
        self.timeout_seconds = timeout_seconds

    def load(self) -> list[StockListing]:
        cached_text = self._read_cache()
        bundled_text = self._read_bundled_snapshot()
        if cached_text is not None and self._is_cache_fresh():
            return self._parse_csv(cached_text)

        try:
            csv_text = self._download_csv_text()
            self.cache_path.write_text(csv_text, encoding="utf-8")
            return self._parse_csv(csv_text)
        except Exception as exc:
            if cached_text is not None:
                logger.warning(
                    "Unable to refresh NSE universe CSV. Using cached copy instead. %s",
                    exc,
                )
                return self._parse_csv(cached_text)
            if bundled_text is not None:
                logger.warning(
                    "Unable to refresh NSE universe CSV. Using bundled snapshot instead. %s",
                    exc,
                )
                return self._parse_csv(bundled_text)
            raise UniverseProviderError(
                f"Unable to load NSE equity universe from {self.source_url}: {exc}"
            ) from exc

    def load_bundled(self) -> list[StockListing]:
        bundled_text = self._read_bundled_snapshot()
        if bundled_text is None:
            raise UniverseProviderError(
                f"Bundled NSE universe snapshot not found at {self.bundled_snapshot_path}"
            )
        return self._parse_csv(bundled_text)

    def _download_csv_text(self) -> str:
        errors: list[str] = []
        for url in self._candidate_urls():
            try:
                response = requests.get(
                    url,
                    timeout=self.timeout_seconds,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0 Safari/537.36"
                        )
                    },
                )
                response.raise_for_status()
                return response.text
            except Exception as exc:
                errors.append(f"{url}: {exc}")

        raise UniverseProviderError("; ".join(errors))

    def _candidate_urls(self) -> list[str]:
        urls = [self.source_url]
        if self.fallback_source_url and self.fallback_source_url not in urls:
            urls.append(self.fallback_source_url)
        return urls

    def _read_cache(self) -> str | None:
        if not self.cache_path.exists():
            return None
        return self.cache_path.read_text(encoding="utf-8")

    def _read_bundled_snapshot(self) -> str | None:
        if not self.bundled_snapshot_path.exists():
            return None
        return self.bundled_snapshot_path.read_text(encoding="utf-8")

    def _is_cache_fresh(self) -> bool:
        age_seconds = time.time() - self.cache_path.stat().st_mtime
        return age_seconds <= self.cache_ttl_seconds

    def _parse_csv(self, csv_text: str) -> list[StockListing]:
        active_lines = [
            line
            for line in csv_text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        reader = csv.DictReader(io.StringIO("\n".join(active_lines)))
        listings: list[StockListing] = []

        seen_symbols: set[str] = set()

        for row in reader:
            raw_symbol = (row.get("SYMBOL") or "").strip().upper()
            company_name = (row.get("NAME OF COMPANY") or raw_symbol).strip()
            series = (row.get(" SERIES") or row.get("SERIES") or "").strip().upper()

            if not raw_symbol:
                continue

            symbol = f"{raw_symbol}.NS"
            if symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)

            metadata = STATIC_METADATA.get(symbol.upper())
            normalized_sector = metadata.sector if metadata else _guess_sector(company_name)
            listings.append(
                StockListing(
                    symbol=symbol,
                    company_name=company_name,
                    sector=normalized_sector,
                    market_cap_bucket=(
                        metadata.market_cap_bucket if metadata else MarketCapBucket.SMALL
                    ),
                )
            )

        if not listings:
            raise UniverseProviderError("NSE universe CSV was empty after parsing.")

        listings.sort(key=lambda listing: listing.symbol)
        return listings


def load_universe(
    symbols: list[str] | None = None,
    sectors: list[str] | None = None,
    market_caps: list[MarketCapBucket] | None = None,
) -> list[StockListing]:
    listings = _load_configured_universe()

    if symbols:
        symbol_set = {_normalize_symbol(symbol) for symbol in symbols}
        listings = [listing for listing in listings if listing.symbol.upper() in symbol_set]

    if sectors:
        sector_set = {sector.lower() for sector in sectors}
        listings = [listing for listing in listings if listing.sector.lower() in sector_set]

    if market_caps:
        cap_set = set(market_caps)
        listings = [listing for listing in listings if listing.market_cap_bucket in cap_set]

    return listings


def get_benchmark_listing() -> StockListing:
    return BENCHMARK_LISTING


def _load_configured_universe() -> list[StockListing]:
    provider_name = settings.universe_provider

    if provider_name == "static":
        return list(DEFAULT_UNIVERSE)

    live_provider = NseEquityCsvUniverseProvider(
        cache_dir=settings.cache_dir,
        cache_ttl_minutes=settings.universe_cache_ttl_minutes,
        source_url=settings.nse_equity_csv_url,
        fallback_source_url=settings.nse_equity_fallback_csv_url,
        timeout_seconds=settings.nse_timeout_seconds,
    )

    if provider_name == "bundled_csv":
        return live_provider.load_bundled()

    if provider_name == "nse":
        return live_provider.load()

    if provider_name == "auto":
        try:
            return live_provider.load()
        except UniverseProviderError as exc:
            logger.warning(
                "Falling back to the built-in curated universe because live NSE loading failed. %s",
                exc,
            )
            return list(DEFAULT_UNIVERSE)

    raise ValueError(f"Unsupported UNIVERSE_PROVIDER: {provider_name}")


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if normalized.endswith(".NS"):
        return normalized
    return f"{normalized}.NS"


def _guess_sector(company_name: str) -> str:
    normalized = company_name.lower()
    if any(token in normalized for token in ("bank", "finance", "capital", "investment")):
        return "Financials"
    if any(token in normalized for token in ("pharma", "health", "hospital", "life science")):
        return "Healthcare"
    if any(token in normalized for token in ("software", "tech", "systems", "infotech")):
        return "IT"
    if any(token in normalized for token in ("power", "energy", "gas", "oil")):
        return "Energy"
    if any(token in normalized for token in ("telecom", "communications", "airtel")):
        return "Telecom"
    if any(token in normalized for token in ("cement", "steel", "paint", "chemical")):
        return "Materials"
    if any(token in normalized for token in ("motors", "auto", "tyre")):
        return "Automotive"
    if any(token in normalized for token in ("retail", "consumer", "foods", "jewellers")):
        return "Consumer"
    if any(token in normalized for token in ("infra", "construction", "engineer", "shipping", "ports")):
        return "Industrials"
    return "Unknown"
