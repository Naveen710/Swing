"""Manual import for data free endpoints don't provide: facts from CSV/Excel, documents from PDF.

CSV/Excel columns (header row required):
  symbol, metric, period_end (YYYY-MM-DD), period_type (FY|Q|TTM), value, unit, scope, source_note
Units: INR, INR_cr, INR_lakh, INR_mn, shares, ratio. Imported facts are marked verification="manual".
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date

from app.research.providers.base import FactRecord

REQUIRED = ("symbol", "metric", "period_end", "period_type", "value", "unit")
UNITS = {"INR", "INR_cr", "INR_lakh", "INR_mn", "shares", "ratio"}
PERIOD_TYPES = {"FY", "Q", "TTM"}
SCOPES = {"consolidated", "standalone", "unknown"}


@dataclass
class ImportRow:
    symbol: str
    fact: FactRecord
    line: int


def _rows_from_csv(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def _rows_from_excel(content: bytes) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    return [dict(zip(header, ["" if v is None else v for v in r])) for r in rows[1:]]


def parse_fact_file(content: bytes, filename: str) -> tuple[list[ImportRow], list[str]]:
    """Returns (valid rows, errors). Invalid rows are reported with their line number, never guessed."""
    raw = _rows_from_excel(content) if filename.lower().endswith((".xlsx", ".xlsm")) else _rows_from_csv(content)
    out: list[ImportRow] = []
    errors: list[str] = []
    if raw and any(k not in {str(c).strip().lower() for c in raw[0]} for k in REQUIRED):
        return [], [f"Missing columns. Required: {', '.join(REQUIRED)}"]
    for i, r in enumerate(raw, start=2):
        r = {str(k).strip().lower(): ("" if v is None else str(v).strip()) for k, v in r.items()}
        if not any(r.values()):
            continue
        try:
            unit = r["unit"]
            if unit not in UNITS:
                raise ValueError(f"unit '{unit}' not one of {sorted(UNITS)}")
            ptype = r["period_type"].upper()
            if ptype not in PERIOD_TYPES:
                raise ValueError(f"period_type '{r['period_type']}' not one of FY/Q/TTM")
            scope = (r.get("scope") or "unknown").lower()
            if scope not in SCOPES:
                raise ValueError(f"scope '{scope}' not consolidated/standalone/unknown")
            value = float(r["value"].replace(",", "")) if r["value"] != "" else None
            pe = date.fromisoformat(r["period_end"][:10])
            out.append(ImportRow(symbol=r["symbol"].upper().removesuffix(".NS"), line=i, fact=FactRecord(
                metric=r["metric"], value=value, unit=unit, period_end=pe, period_type=ptype, scope=scope,
                note=r.get("source_note") or "manual import")))
        except (KeyError, ValueError) as exc:
            errors.append(f"line {i}: {exc}")
    return out, errors
