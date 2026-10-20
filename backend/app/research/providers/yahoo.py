"""Yahoo Finance via yfinance: prices, corporate actions, metadata, annual + quarterly statements.

Known limits (handled, not hidden):
  * NSE symbols need the `.NS` suffix (BSE `.BO`); some small caps exist only on one.
  * Statement rows vary by company and change names between yfinance versions; any metric we
    can't find is returned in `missing`.
  * Yahoo does not say whether Indian statements are consolidated or standalone → scope "unknown".
  * No publication dates for statements → published_at is None.
  * Prices come with auto_adjust=False: Close is split-adjusted, not dividend-adjusted;
    Adj Close is dividend-adjusted.
"""
from __future__ import annotations

import importlib
import io
import json
import math
from datetime import date

import pandas as pd

from app.config import settings
from app.research.providers.base import (
    ERROR, MISSING, OK, FactRecord, ProviderResult, RateLimiter, RawPayload, SecurityRef, with_retries,
)

STATEMENT_ROWS: dict[str, tuple[str, tuple[str, ...]]] = {
    # metric: (statement, candidate row names in priority order)
    "revenue": ("income", ("Total Revenue", "Operating Revenue")),
    "ebitda": ("income", ("EBITDA", "Normalized EBITDA")),
    "ebit": ("income", ("EBIT", "Operating Income")),
    "pbt": ("income", ("Pretax Income",)),
    "net_income": ("income", ("Net Income", "Net Income Common Stockholders")),
    "interest_expense": ("income", ("Interest Expense", "Interest Expense Non Operating")),
    "other_income": ("income", ("Other Non Operating Income Expenses", "Other Income Expense")),
    "diluted_shares": ("income", ("Diluted Average Shares", "Basic Average Shares")),
    "total_assets": ("balance", ("Total Assets",)),
    "current_liabilities": ("balance", ("Current Liabilities", "Total Current Liabilities")),
    "total_debt": ("balance", ("Total Debt",)),
    "equity": ("balance", ("Stockholders Equity", "Common Stock Equity")),
    "shares": ("balance", ("Ordinary Shares Number", "Share Issued")),
    "receivables": ("balance", ("Accounts Receivable", "Receivables")),
    "inventory": ("balance", ("Inventory",)),
    "cash": ("balance", ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")),
    "cfo": ("cash", ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities")),
    "capex": ("cash", ("Capital Expenditure",)),
    "fcf": ("cash", ("Free Cash Flow",)),
}
QUARTERLY_ROWS = {k: STATEMENT_ROWS[k][1] for k in ("revenue", "ebit", "net_income")}
SHARE_METRICS = {"shares", "diluted_shares"}
INFO_KEYS = ("longName", "shortName", "sector", "industry", "country", "currency", "financialCurrency", "exchange",
             "marketCap", "sharesOutstanding", "trailingPE", "priceToBook", "beta", "heldPercentInsiders",
             "heldPercentInstitutions", "website", "longBusinessSummary", "fullTimeEmployees", "isin")

_limiter = RateLimiter(0.6)


def _yf():
    return importlib.import_module("yfinance")


def _csv(frame: pd.DataFrame | None) -> bytes:
    return b"" if frame is None else frame.to_csv().encode()


def _clean(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def statement_facts(frame: pd.DataFrame | None, rows: dict[str, tuple[str, ...]], period_type: str, unit: str,
                    raw_key: str) -> tuple[list[FactRecord], list[str]]:
    """Turn a yfinance statement frame (rows = line items, columns = period ends) into facts."""
    facts: list[FactRecord] = []
    missing: list[str] = []
    if frame is None or getattr(frame, "empty", True):
        return facts, list(rows)
    for metric, names in rows.items():
        name = next((n for n in names if n in frame.index), None)
        if name is None:
            missing.append(metric)
            continue
        for col, value in frame.loc[name].items():
            v = _clean(value)
            if v is None:
                continue
            facts.append(FactRecord(metric=metric, value=v, unit="shares" if metric in SHARE_METRICS else unit,
                                    period_end=pd.Timestamp(col).date(), period_type=period_type, raw_key=raw_key,
                                    note=f"yahoo row '{name}'"))
    return facts, missing


class YahooProvider:
    name = "yahoo"

    def _ticker(self, sec: SecurityRef):
        return _yf().Ticker(sec.provider_symbol or f"{sec.symbol}.NS")

    def get_prices(self, sec: SecurityRef, years: int = 3) -> ProviderResult:
        sym = sec.provider_symbol or f"{sec.symbol}.NS"
        try:
            _limiter.wait()
            hist = with_retries(lambda: self._ticker(sec).history(period=f"{years}y", interval="1d", auto_adjust=False,
                                                                  actions=True, timeout=settings.yahoo_timeout_seconds))
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(status=ERROR, error=f"Yahoo prices for {sym}: {exc}")
        if hist is None or hist.empty:
            return ProviderResult(status=MISSING, error=f"Yahoo returned no price history for {sym}")
        frame = pd.DataFrame({
            "date": [pd.Timestamp(i).date() for i in hist.index],
            "open": hist.get("Open"), "high": hist.get("High"), "low": hist.get("Low"), "close": hist.get("Close"),
            "adj_close": hist.get("Adj Close"), "volume": hist.get("Volume"),
            "dividend": hist.get("Dividends", 0.0), "split": hist.get("Stock Splits", 0.0),
        }).reset_index(drop=True)
        raw = RawPayload(f"yahoo:prices:{sym}", _csv(hist), "text/csv",
                         url=f"https://finance.yahoo.com/quote/{sym}/history")
        return ProviderResult(status=OK, data=frame, raw=[raw], notes=["Close is split-adjusted; Adj Close also dividend-adjusted"])

    def get_metadata(self, sec: SecurityRef) -> ProviderResult:
        sym = sec.provider_symbol or f"{sec.symbol}.NS"
        try:
            _limiter.wait()
            info = with_retries(lambda: self._ticker(sec).info) or {}
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(status=ERROR, error=f"Yahoo info for {sym}: {exc}")
        subset = {k: info.get(k) for k in INFO_KEYS if info.get(k) is not None}
        missing = [k for k in ("sector", "industry", "marketCap", "sharesOutstanding", "financialCurrency") if k not in subset]
        raw = RawPayload(f"yahoo:info:{sym}", json.dumps(subset, sort_keys=True, default=str).encode(), "application/json",
                         url=f"https://finance.yahoo.com/quote/{sym}/profile")
        return ProviderResult(status=OK if subset else MISSING, data=subset, raw=[raw], missing=missing)

    def get_financials(self, sec: SecurityRef) -> ProviderResult:
        sym = sec.provider_symbol or f"{sec.symbol}.NS"
        try:
            _limiter.wait()
            t = self._ticker(sec)
            income = with_retries(lambda: t.income_stmt)
            balance = with_retries(lambda: t.balance_sheet)
            cash = with_retries(lambda: t.cashflow)
            try:
                quarterly = with_retries(lambda: t.quarterly_income_stmt, attempts=2)
            except Exception:  # noqa: BLE001
                quarterly = None
            try:
                currency = (t.info or {}).get("financialCurrency") or "INR"
            except Exception:  # noqa: BLE001
                currency = "INR"
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(status=ERROR, error=f"Yahoo statements for {sym}: {exc}")
        unit = "INR" if currency == "INR" else currency
        frames = {"income": income, "balance": balance, "cash": cash}
        facts: list[FactRecord] = []
        missing: list[str] = []
        raws: list[RawPayload] = []
        for stmt in ("income", "balance", "cash"):
            key = f"yahoo:{stmt}:{sym}"
            raws.append(RawPayload(key, _csv(frames[stmt]), "text/csv", url=f"https://finance.yahoo.com/quote/{sym}/financials"))
            rows = {mtr: names for mtr, (s, names) in STATEMENT_ROWS.items() if s == stmt}
            f, miss = statement_facts(frames[stmt], rows, "FY", unit, key)
            facts += f
            missing += miss
        qkey = f"yahoo:quarterly_income:{sym}"
        raws.append(RawPayload(qkey, _csv(quarterly), "text/csv", url=f"https://finance.yahoo.com/quote/{sym}/financials"))
        qf, qmiss = statement_facts(quarterly, QUARTERLY_ROWS, "Q", unit, qkey)
        facts += qf
        missing += [f"quarterly_{x}" for x in qmiss]
        notes = []
        if unit != "INR":
            notes.append(f"Statements reported in {currency}, not INR")
        status = OK if facts else MISSING
        return ProviderResult(status=status, data=facts, raw=raws, missing=missing, notes=notes,
                              error=None if facts else f"Yahoo returned no statements for {sym}")
