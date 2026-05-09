from __future__ import annotations

import argparse
from datetime import UTC, datetime

from app.config import settings
from app.services.email_report import (
    build_daily_universe_reports,
    send_daily_email_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run all three scan universes and email the daily report."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate the report and print the plain-text body instead of sending email.",
    )
    args = parser.parse_args()

    if not args.dry_run and not settings.email_report_enabled:
        print("Daily email report is disabled. Skipping send.")
        return 0

    started_at = datetime.now(UTC)
    reports = build_daily_universe_reports()
    subject, text_body, _ = send_daily_email_report(reports, dry_run=args.dry_run)

    if args.dry_run:
        print(subject)
        print(f"Generated at {started_at.isoformat()}")
        print(text_body)
    else:
        print(f"Sent daily scan report at {started_at.isoformat()} with subject: {subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
