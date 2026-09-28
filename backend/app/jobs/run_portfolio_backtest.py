"""CLI: python -m app.jobs.run_portfolio_backtest --universe nifty500 --years 2"""
from __future__ import annotations

import argparse
import json

from app.schemas import ScanUniverse
from app.services.portfolio_backtest import run_portfolio_backtest
from app.services.scanner import scanner_service


def main() -> int:
    parser = argparse.ArgumentParser(description="Walk-forward portfolio backtest of the full system.")
    parser.add_argument("--universe", default="nifty500", choices=[u.value for u in ScanUniverse])
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--max-symbols", type=int, default=None)
    args = parser.parse_args()
    result = run_portfolio_backtest(
        scanner_service, ScanUniverse(args.universe), args.years, args.max_symbols,
        progress=lambda **kw: print(kw.get("stage", ""), end="\r"),
    )
    print(json.dumps(result["metrics"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
