from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.services.universe import NseEquityCsvUniverseProvider


def main() -> None:
    provider = NseEquityCsvUniverseProvider(
        cache_dir=Path(".cache"),
        cache_ttl_minutes=settings.universe_cache_ttl_minutes,
        source_url=settings.nse_equity_csv_url,
        fallback_source_url=settings.nse_equity_fallback_csv_url,
        timeout_seconds=settings.nse_timeout_seconds,
    )
    listings = provider.load()
    print(f"Prefetched {len(listings)} NSE listings into {provider.cache_path}")


if __name__ == "__main__":
    main()
