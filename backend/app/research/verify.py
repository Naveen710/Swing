"""Verification harness: compare stored facts / prices / corporate actions with values a person
checked against the source (annual report, exchange filing, price history).

CSV columns: case_id, symbol, kind (fact|price|action), field, period (YYYY-MM-DD), period_type
(FY|Q, facts only), expected, unit (INR|INR_cr|INR_lakh|INR_mn|shares|price|ratio), tolerance_pct,
source_note, explanation. A row with an empty `expected` is "not_checked" (template rows).
Matching facts are marked verification="verified"; mismatches become data issues unless an
`explanation` is given (then they are listed as explained).

    python -m app.research.verify app/research/verification/sample_cases.csv
"""
from __future__ import annotations

import csv
import io
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.research import models as m
from app.research.db import now, tx
from app.research.engine import company_row
from app.research.ingest import IssueLog
from app.research.metrics import to_inr

HERE = Path(__file__).resolve().parent / "verification"


def _actual(conn, comp, kind: str, field: str, period: date, period_type: str):
    if kind == "fact":
        f = m.financial_facts.c
        rows = conn.execute(select(f.id, f.value, f.unit, f.provider).where((f.company_id == comp["id"]) & (f.metric == field) &
                            (f.period_end == period) & (f.period_type == (period_type or "FY")) & f.is_current.is_(True))).all()
        if not rows:
            return None, None, None
        r = sorted(rows, key=lambda r: {"manual": 0, "yahoo": 1, "fixture": 2}.get(r.provider, 9))[0]
        return (r.value if r.unit in ("shares", "ratio") else to_inr(r.value, r.unit)), r.id, r.provider
    if kind == "price":
        p = m.prices_daily.c
        r = conn.execute(select(p.close, p.provider).where((p.security_id == comp["security_id"]) & (p.date == period))).first()
        return (r.close, None, r.provider) if r else (None, None, None)
    if kind == "action":
        a = m.corporate_actions.c
        r = conn.execute(select(a.value, a.provider).where((a.security_id == comp["security_id"]) & (a.date == period) & (a.action == field))).first()
        return (r.value, None, r.provider) if r else (None, None, None)
    raise ValueError(f"unknown kind {kind}")


def run(content: bytes, label: str = "upload") -> dict:
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    summary = {"match": 0, "mismatch": 0, "explained": 0, "missing": 0, "not_checked": 0, "unknown_symbol": 0}
    results = []
    with tx() as conn:
        run_id = conn.execute(m.ingestion_runs.insert().values(kind="verification", started_at=now(), status="running", detail={"file": label})).inserted_primary_key[0]
        for r in rows:
            r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
            case = r.get("case_id") or f"row{len(results) + 1}"
            comp = company_row(conn, r["symbol"])
            if comp is None:
                summary["unknown_symbol"] += 1
                results.append({"case_id": case, "status": "unknown_symbol"})
                continue
            period = date.fromisoformat(r["period"])
            tol = float(r.get("tolerance_pct") or 0.5)
            expected_raw = r.get("expected", "")
            unit = r.get("unit") or "INR"
            expected = None
            if expected_raw:
                v = float(expected_raw.replace(",", ""))
                expected = v if unit in ("shares", "ratio", "price") else to_inr(v, unit)
            actual, fact_id, provider = _actual(conn, comp, r["kind"], r["field"], period, r.get("period_type", "FY"))
            if expected is None:
                status = "not_checked"
            elif actual is None:
                status = "missing"
            elif expected == 0:
                status = "match" if actual == 0 else "mismatch"
            else:
                status = "match" if abs(actual / expected - 1) * 100 <= tol else "mismatch"
            explanation = r.get("explanation") or None
            bucket = "explained" if status == "mismatch" and explanation else status
            summary[bucket] += 1
            conn.execute(m.verifications.delete().where(m.verifications.c.case_id == case))
            conn.execute(m.verifications.insert().values(case_id=case, symbol=comp["symbol"], kind=r["kind"], field=r["field"], period=r["period"],
                                                         expected=expected, actual=actual, tolerance_pct=tol, status=bucket,
                                                         source_note=r.get("source_note"), explanation=explanation, checked_at=now()))
            if fact_id and status == "match":
                conn.execute(m.financial_facts.update().where(m.financial_facts.c.id == fact_id).values(verification="verified"))
            if status in ("mismatch", "missing") and not explanation:
                IssueLog(conn, comp["id"], run_id).add("mismatch", f"verify.{case}",
                                                       f"{r['kind']} {r['field']} {r['period']}: expected {expected:,.2f}, stored {'none' if actual is None else f'{actual:,.2f}'} "
                                                       f"({provider or 'no provider'}). Source: {r.get('source_note') or 'n/a'}", "error")
            results.append({"case_id": case, "symbol": comp["symbol"], "kind": r["kind"], "field": r["field"], "period": r["period"],
                            "expected": expected, "actual": actual, "status": bucket, "provider": provider, "explanation": explanation})
        conn.execute(m.ingestion_runs.update().where(m.ingestion_runs.c.id == run_id).values(finished_at=now(),
                     status="ok" if not summary["mismatch"] and not summary["missing"] else "partial", detail={"file": label, **summary}))
    return {"summary": summary, "results": results}


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else HERE / "sample_cases.csv"
    out = run(path.read_bytes(), path.name)
    print(out["summary"])
    for r in out["results"]:
        if r["status"] not in ("match", "not_checked"):
            print(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
