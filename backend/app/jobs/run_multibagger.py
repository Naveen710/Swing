"""Run the multibagger screen, record this month's snapshot and grade older ones.

    python -m app.jobs.run_multibagger --universe nifty_smallcap_250
    python -m app.jobs.run_multibagger --if-new-month      # used by the daily job
"""
from __future__ import annotations

import argparse
from datetime import date

from app.schemas import ScanUniverse


def run_monthly(universe: ScanUniverse = ScanUniverse.NIFTY_SMALLCAP_250, only_if_new_month: bool = True) -> str:
    from app.api import multibagger_service
    from app.services.curated_algo import _index_frame
    from app.services.scanner import scanner_service

    try:   # snapshot month = month of the latest trading day, so the 1st of a month doesn't re-run daily
        month = str(_index_frame(scanner_service, 30)[0].index[-1].date())[:7]
    except Exception:  # noqa: BLE001
        month = date.today().isoformat()[:7]
    if only_if_new_month and multibagger_service.has_snapshot(universe, month):
        graded = multibagger_service.evaluate_ledger()
        return f"Multibagger snapshot for {month} already recorded; ledger graded: {graded}"
    result = multibagger_service.run(universe, set(), False)
    graded = multibagger_service.evaluate_ledger()
    return f"Multibagger run {universe.value}: {result['funnel']['tiered']} tiered, snapshot {result.get('snapshot')}; ledger graded: {graded}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", default=ScanUniverse.NIFTY_SMALLCAP_250.value, choices=[u.value for u in ScanUniverse])
    parser.add_argument("--if-new-month", action="store_true", help="Skip the run if this month's snapshot exists.")
    args = parser.parse_args()
    print(run_monthly(ScanUniverse(args.universe), only_if_new_month=args.if_new_month))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
