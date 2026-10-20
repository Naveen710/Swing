"""Command line for the research platform.

    python -m app.research.cli seed                 # 20 fictional sample companies (no credentials)
    python -m app.research.cli ingest DIXON         # one real company (Yahoo + NSE; needs internet)
    python -m app.research.cli ingest-deep          # the ~20-company deep-research set
    python -m app.research.cli news
    python -m app.research.cli dossier SMPCABLE --md > dossier.md
    python -m app.research.cli replay 12            # re-run snapshot 12 from its stored inputs
    python -m app.research.cli verify app/research/verification/sample_cases.csv
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.research.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed")
    i = sub.add_parser("ingest")
    i.add_argument("symbol")
    sub.add_parser("ingest-deep")
    sub.add_parser("news")
    d = sub.add_parser("dossier")
    d.add_argument("symbol")
    d.add_argument("--md", action="store_true")
    r = sub.add_parser("replay")
    r.add_argument("snapshot_id", type=int)
    v = sub.add_parser("verify")
    v.add_argument("csv", nargs="?")
    a = p.parse_args(argv)

    from app.research.service import ensure_master

    ensure_master()
    if a.cmd == "seed":
        from app.research.service import seed_samples
        print(json.dumps(seed_samples(), indent=2, default=str))
    elif a.cmd == "ingest":
        from app.research.engine import build_snapshot
        from app.research.ingest import ingest_company
        print(json.dumps(ingest_company(a.symbol), indent=2, default=str))
        print("snapshot:", build_snapshot(a.symbol)["snapshot_id"])
    elif a.cmd == "ingest-deep":
        from app.research.engine import build_snapshot
        from app.research.ingest import ingest_company, ingest_news
        from app.research.master import deep_symbols
        for sym in deep_symbols():
            res = ingest_company(sym)
            print(sym, res["status"], "snapshot", build_snapshot(sym)["snapshot_id"])
        print(ingest_news())
    elif a.cmd == "news":
        from app.research.ingest import ingest_news
        print(ingest_news())
    elif a.cmd == "dossier":
        from app.research.engine import build_snapshot, to_markdown
        out = build_snapshot(a.symbol)["output"]
        print(to_markdown(out) if a.md else json.dumps(out, indent=2, default=str))
    elif a.cmd == "replay":
        from app.research.engine import replay_snapshot
        print(json.dumps(replay_snapshot(a.snapshot_id), indent=2))
    elif a.cmd == "verify":
        from app.research.verify import main as vmain
        return vmain([a.csv] if a.csv else [])
    return 0


if __name__ == "__main__":
    sys.exit(main())
