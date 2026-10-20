"""Sample-data provider: 20 FICTIONAL companies with deterministic, clearly labelled data.

Why fictional: putting made-up numbers on real tickers would plant fabricated "facts" about real
companies in your research history. Every sample company has a symbol starting with "SMP" and a
name ending in "(sample)", is flagged is_sample=True, and every page shows a sample-data banner.

Each company is built to exercise a specific case: clean compounder, inflection with a lagging
price (recognition gap), bank and insurer (sector-specific metrics), commodity peak, loss-maker,
over-levered, heavy dilution, receivables growing faster than sales, other-income dependence,
auditor resignation, restated figures, statements in ₹ crore (unit normalisation), missing data.
"""
from __future__ import annotations

import io
import json
import math
import zlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from app.research.providers.base import (
    MISSING, OK, AnnouncementRecord, FactRecord, NewsRecord, ProviderResult, RawPayload, SecurityRef,
)

FY_ENDS = [date(2022, 3, 31), date(2023, 3, 31), date(2024, 3, 31), date(2025, 3, 31), date(2026, 3, 31)]
Q_ENDS = [date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]


@dataclass
class Spec:
    symbol: str
    name: str
    sector: str
    industry: str
    rev0: float                       # FY22 revenue, ₹ crore
    growth: list[float]               # YoY growth FY23..FY26
    margin: list[float]               # EBIT margin FY22..FY26
    debt_equity: float = 0.2
    cfo_ratio: float = 1.0            # CFO / PAT
    other_income_share: float = 0.05  # of PBT
    receivable_days: list[float] = field(default_factory=lambda: [60, 60, 60, 60, 60])
    share_growth: float = 0.0         # total over the period
    price_drift: float = 0.0006
    pe_now: float = 28.0
    financial: bool = False
    loss: bool = False
    unit_cr: bool = False             # report in ₹ crore instead of rupees
    missing: tuple[str, ...] = ()
    restate: bool = False
    story: str = ""
    events: list[tuple[str, str, str]] = field(default_factory=list)   # (days_ago, category, text)


SPECS: list[Spec] = [
    Spec("SMPCABLE", "Aravali Cables (sample)", "Capital Goods", "Cables & Wires", 900, [0.14, 0.18, 0.24, 0.31], [0.10, 0.105, 0.115, 0.13, 0.145],
         cfo_ratio=0.95, price_drift=-0.0002, pe_now=13, story="inflection with lagging price",
         events=[("12", "Bagging/Receiving of orders/contracts", "Received order worth Rs 410 crore from a state transmission utility"),
                 ("40", "Financial Result Updates", "Quarterly results: revenue up 33% YoY"),
                 ("75", "Analysts/Institutional Investor Meet/Con. Call Updates", "Outcome of investor call: capacity expansion on track for FY27")]),
    Spec("SMPPHARMA", "Konkan Lifesciences (sample)", "Healthcare", "Pharmaceuticals", 2400, [0.11, 0.12, 0.12, 0.13], [0.18, 0.18, 0.19, 0.19, 0.2],
         debt_equity=0.05, cfo_ratio=1.05, pe_now=34, story="steady compounder",
         events=[("20", "Financial Result Updates", "Quarterly results in line"), ("90", "Updates", "USFDA inspection closed with no observations")]),
    Spec("SMPBANK", "Narmada Small Finance Bank (sample)", "Financial Services", "Banks", 3100, [0.18, 0.2, 0.17, 0.15], [0.22, 0.23, 0.24, 0.24, 0.23],
         debt_equity=7.5, cfo_ratio=0.6, pe_now=14, financial=True, story="bank — P/E and FCF logic not applied",
         events=[("15", "Financial Result Updates", "Quarterly results: gross NPA 2.1%")]),
    Spec("SMPINSURE", "Sahyadri General Insurance (sample)", "Financial Services", "Insurance", 5200, [0.12, 0.1, 0.13, 0.11], [0.08, 0.07, 0.09, 0.1, 0.1],
         debt_equity=0.1, cfo_ratio=1.4, pe_now=40, financial=True, story="insurer"),
    Spec("SMPSTEEL", "Deccan Steel Works (sample)", "Metals & Mining", "Iron & Steel", 8000, [0.35, 0.28, -0.08, 0.04], [0.22, 0.26, 0.17, 0.12, 0.13],
         debt_equity=0.7, cfo_ratio=1.1, pe_now=7, story="commodity cycle past peak"),
    Spec("SMPEV", "Vindhya Electric Mobility (sample)", "Automobile and Auto Components", "2/3 Wheelers", 600, [0.6, 0.45, 0.3, 0.2], [-0.25, -0.2, -0.15, -0.12, -0.09],
         debt_equity=0.4, cfo_ratio=1.0, share_growth=0.34, pe_now=0, loss=True, story="loss-making with dilution",
         events=[("30", "Preferential Issue", "Allotment of equity shares on preferential basis to promoter group")]),
    Spec("SMPINFRA", "Thar Infra Projects (sample)", "Construction", "Civil Construction", 4200, [0.09, 0.07, 0.11, 0.06], [0.11, 0.1, 0.1, 0.09, 0.09],
         debt_equity=2.1, cfo_ratio=0.45, pe_now=11, story="over-levered, weak cash conversion"),
    Spec("SMPAGRO", "Godavari Agro Foods (sample)", "Fast Moving Consumer Goods", "Packaged Foods", 1500, [0.12, 0.15, 0.2, 0.22], [0.08, 0.08, 0.09, 0.09, 0.1],
         cfo_ratio=0.4, receivable_days=[45, 58, 75, 96, 120], pe_now=45, story="receivables outrunning sales"),
    Spec("SMPHOLD", "Malabar Holdings & Trading (sample)", "Diversified", "Diversified", 700, [0.03, 0.02, 0.04, 0.01], [0.04, 0.04, 0.03, 0.03, 0.03],
         other_income_share=0.62, pe_now=19, story="profits from other income"),
    Spec("SMPCHEM", "Satpura Speciality Chemicals (sample)", "Chemicals", "Speciality Chemicals", 1900, [0.16, 0.13, 0.14, 0.15], [0.15, 0.15, 0.16, 0.16, 0.17],
         pe_now=31, story="auditor resignation",
         events=[("9", "Resignation of Statutory Auditors", "Statutory auditor has resigned citing non-receipt of information"),
                 ("60", "Financial Result Updates", "Quarterly results")]),
    Spec("SMPIT", "Nilgiri Software Services (sample)", "Information Technology", "IT Services", 3300, [0.19, 0.14, 0.09, 0.08], [0.21, 0.2, 0.19, 0.18, 0.17],
         debt_equity=0.0, cfo_ratio=1.1, pe_now=26, story="growth decelerating"),
    Spec("SMPRETAIL", "Kaveri Lifestyle Retail (sample)", "Consumer Services", "Speciality Retail", 2100, [0.22, 0.25, 0.27, 0.29], [0.07, 0.075, 0.08, 0.085, 0.09],
         cfo_ratio=1.2, pe_now=95, price_drift=0.0012, story="great growth, priced for perfection"),
    Spec("SMPPOWER", "Chambal Renewable Power (sample)", "Power", "Renewable Energy", 1300, [0.25, 0.3, 0.28, 0.26], [0.32, 0.33, 0.34, 0.34, 0.35],
         debt_equity=1.4, cfo_ratio=1.3, pe_now=38, story="high growth, high leverage"),
    Spec("SMPAUTO", "Ganga Auto Components (sample)", "Automobile and Auto Components", "Auto Components", 2600, [0.1, 0.12, 0.15, 0.19], [0.09, 0.095, 0.105, 0.115, 0.125],
         cfo_ratio=0.9, pe_now=24, unit_cr=True, story="reports in ₹ crore (unit normalisation)"),
    Spec("SMPTEXT", "Sabarmati Textiles (sample)", "Textiles", "Textiles", 1700, [0.05, -0.02, 0.03, 0.06], [0.07, 0.06, 0.05, 0.06, 0.06],
         debt_equity=0.9, cfo_ratio=0.8, pe_now=13, restate=True, story="restated FY25 figures"),
    Spec("SMPLOGI", "Brahmaputra Logistics (sample)", "Services", "Logistics", 1100, [0.17, 0.19, 0.21, 0.23], [0.12, 0.125, 0.13, 0.14, 0.15],
         cfo_ratio=1.0, pe_now=27, missing=("cfo", "receivables", "quarterly"), story="missing cash flow and quarterly data"),
    Spec("SMPCEM", "Aravalli Cement (sample)", "Construction Materials", "Cement", 5600, [0.08, 0.1, 0.06, 0.07], [0.14, 0.12, 0.15, 0.16, 0.16],
         debt_equity=0.5, cfo_ratio=1.2, pe_now=29, story="cyclical, average"),
    Spec("SMPDEF", "Shivalik Defence Systems (sample)", "Capital Goods", "Aerospace & Defense", 800, [0.12, 0.2, 0.35, 0.42], [0.13, 0.15, 0.17, 0.19, 0.21],
         cfo_ratio=0.7, pe_now=58, price_drift=0.0016, story="strong inflection already recognised by price",
         events=[("5", "Bagging/Receiving of orders/contracts", "Order from Ministry of Defence worth Rs 1,250 crore")]),
    Spec("SMPFMCG", "Yamuna Consumer Products (sample)", "Fast Moving Consumer Goods", "Personal Products", 4100, [0.07, 0.08, 0.06, 0.07], [0.19, 0.2, 0.2, 0.21, 0.21],
         cfo_ratio=1.15, pe_now=52, story="quality, slow growth, expensive"),
    Spec("SMPNBFC", "Tapti Finance (sample)", "Financial Services", "Finance", 1250, [0.24, 0.27, 0.22, 0.21], [0.35, 0.36, 0.34, 0.33, 0.33],
         debt_equity=4.2, cfo_ratio=-0.5, pe_now=18, financial=True, story="NBFC — negative CFO is normal for a growing lender"),
]
BY_SYMBOL = {s.symbol: s for s in SPECS}
CR = 1e7


def _seed(symbol: str) -> int:
    return zlib.crc32(symbol.encode())


def latest_business_day(today: date | None = None) -> date:
    d = today or datetime.now(UTC).date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def annual_series(s: Spec) -> dict[str, list[float]]:
    rev = [s.rev0]
    for g in s.growth:
        rev.append(rev[-1] * (1 + g))
    ebit = [r * m for r, m in zip(rev, s.margin)]
    interest = [max(0.0, r * 0.45 * s.debt_equity * 0.09 * 0.25) for r in rev]
    pbt = [(e - i) / max(1 - s.other_income_share, 0.2) if not s.loss else e - i for e, i in zip(ebit, interest)]
    other = [p * s.other_income_share if not s.loss else 0.0 for p in pbt]
    ni = [p * 0.75 if p > 0 else p for p in pbt]
    equity = []
    eq = rev[0] * 0.5
    for n in ni:
        eq = eq + max(n, -eq * 0.2) * 0.8
        equity.append(eq)
    debt = [e * s.debt_equity for e in equity]
    assets = [e + d + r * 0.22 for e, d, r in zip(equity, debt, rev)]
    cl = [r * 0.18 for r in rev]
    cfo = [n * s.cfo_ratio if n > 0 else n * 0.8 for n in ni]
    capex = [-r * 0.06 for r in rev]
    recv = [r * d / 365 for r, d in zip(rev, s.receivable_days)]
    inv = [r * 0.12 for r in rev]
    shares0 = 5e7 + (_seed(s.symbol) % 40) * 1e6
    shares = [shares0 * (1 + s.share_growth * i / 4) for i in range(5)]
    return {"revenue": rev, "ebit": ebit, "ebitda": [e + r * 0.03 for e, r in zip(ebit, rev)], "pbt": pbt, "net_income": ni,
            "interest_expense": interest, "other_income": other, "equity": equity, "total_debt": debt, "total_assets": assets,
            "current_liabilities": cl, "cfo": cfo, "capex": capex, "fcf": [c + x for c, x in zip(cfo, capex)],
            "receivables": recv, "inventory": inv, "cash": [r * 0.06 for r in rev], "shares": shares, "diluted_shares": shares}


PRICE_START = date(2023, 6, 1)
PRICE_ANCHOR = date(2026, 3, 31)      # price level is set here, so any date's close is stable as days pass
DIVIDEND_DATE = date(2026, 4, 15)


def price_series(s: Spec, end: date) -> pd.DataFrame:
    rng = np.random.default_rng(_seed(s.symbol))
    dates = pd.bdate_range(start=PRICE_START, end=end)
    days = len(dates)
    a = annual_series(s)
    eps = a["net_income"][-1] * CR / a["shares"][-1] if not s.loss else None
    target = eps * s.pe_now if eps and s.pe_now else 140.0
    rets = rng.normal(s.price_drift, 0.016, size=days)
    path = np.exp(np.cumsum(rets))
    anchor = int(np.searchsorted(dates.values, np.datetime64(PRICE_ANCHOR)))
    anchor = min(anchor, days - 1)
    close = path / path[anchor] * target
    o_noise, h_noise, l_noise = rng.normal(0, 0.003, days), np.abs(rng.normal(0.006, 0.003, days)), np.abs(rng.normal(0.006, 0.003, days))
    open_ = np.r_[close[0], close[:-1]] * (1 + o_noise)
    high = np.maximum(open_, close) * (1 + h_noise)
    low = np.minimum(open_, close) * (1 - l_noise)
    vol = rng.integers(200_000, 3_000_000, size=days).astype(float)
    div = np.zeros(days)
    if not s.loss and end >= DIVIDEND_DATE:
        i = int(np.searchsorted(dates.values, np.datetime64(DIVIDEND_DATE)))
        div[i] = round(target * 0.006, 2)
    return pd.DataFrame({"date": [d.date() for d in dates], "open": open_.round(2), "high": high.round(2), "low": low.round(2),
                         "close": close.round(2), "adj_close": close.round(2), "volume": vol, "dividend": div, "split": 0.0})


def _pdf(lines: list[str]) -> bytes:
    """Minimal single-page PDF with Helvetica text (no external library)."""
    def esc(t: str) -> str:
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1", "replace").decode("latin-1")
    body = "BT /F1 11 Tf 50 790 Td 14 TL " + " ".join(f"({esc(l)}) Tj T*" for l in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(body)} >>\nstream\n{body}\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def sample_document(s: Spec) -> bytes:
    a = annual_series(s)
    g = s.growth[-1] * 100
    lines = [
        f"{s.name} - Investor presentation Q1 FY27 (SAMPLE DOCUMENT, SYNTHETIC DATA)",
        "This document is generated for software testing. It describes a fictional company.",
        "",
        f"Revenue for FY26 grew {g:.0f}% year on year to Rs {a['revenue'][-1]:,.0f} crore.",
        f"EBIT margin improved to {s.margin[-1] * 100:.1f}% from {s.margin[-2] * 100:.1f}% a year earlier.",
        "Management guidance: we expect revenue growth of 20-25% in FY27, led by new capacity.",
        "Order book stands at 2.3x trailing revenue, providing visibility for the next 24 months.",
        "Key risks: raw material price volatility and delays in customer approvals.",
    ]
    return _pdf(lines)


class FixtureProvider:
    name = "fixture"

    def __init__(self, today: date | None = None) -> None:
        self.end = latest_business_day(today)

    @staticmethod
    def universe() -> list[dict]:
        return [{"symbol": s.symbol, "name": s.name, "sector": s.sector, "industry": s.industry,
                 "isin": f"INSAMPLE{i:04d}", "is_financial": s.financial} for i, s in enumerate(SPECS, start=1)]

    def _spec(self, sec: SecurityRef) -> Spec | None:
        return BY_SYMBOL.get(sec.symbol)

    def get_prices(self, sec: SecurityRef, years: int = 3) -> ProviderResult:
        s = self._spec(sec)
        if not s:
            return ProviderResult(status=MISSING, error=f"No sample data for {sec.symbol}")
        frame = price_series(s, self.end)
        raw = RawPayload(f"fixture:prices:{s.symbol}", frame.to_csv(index=False).encode(), "text/csv", url=None)
        return ProviderResult(status=OK, data=frame, raw=[raw], notes=["Sample data — synthetic prices"])

    def get_metadata(self, sec: SecurityRef) -> ProviderResult:
        s = self._spec(sec)
        if not s:
            return ProviderResult(status=MISSING, error=f"No sample data for {sec.symbol}")
        a = annual_series(s)
        data = {"longName": s.name, "sector": s.sector, "industry": s.industry, "financialCurrency": "INR",
                "sharesOutstanding": a["shares"][-1], "longBusinessSummary": f"Fictional sample company used to test the research engine ({s.story})."}
        raw = RawPayload(f"fixture:info:{s.symbol}", json.dumps(data, sort_keys=True).encode(), "application/json")
        return ProviderResult(status=OK, data=data, raw=[raw])

    def get_financials(self, sec: SecurityRef) -> ProviderResult:
        s = self._spec(sec)
        if not s:
            return ProviderResult(status=MISSING, error=f"No sample data for {sec.symbol}")
        a = annual_series(s)
        unit = "INR_cr" if s.unit_cr else "INR"
        mult = 1.0 if s.unit_cr else CR
        key = f"fixture:financials:{s.symbol}"
        facts: list[FactRecord] = []
        for metric, vals in a.items():
            if metric in s.missing:
                continue
            for pe, v in zip(FY_ENDS, vals):
                is_share = metric in ("shares", "diluted_shares")
                value = v if is_share else v * mult
                if s.restate and metric in ("revenue", "net_income") and pe == FY_ENDS[3]:
                    value *= 0.97          # restated figures arrive in a later ingest (see restated())
                facts.append(FactRecord(metric, round(value, 2), "shares" if is_share else unit, pe, "FY", "consolidated", raw_key=key))
        if "quarterly" not in s.missing:
            q_rev = a["revenue"][-2] / 4
            g = (1 + s.growth[-1]) ** 0.25 - 1
            for i, pe in enumerate(Q_ENDS):
                r = q_rev * (1 + g) ** i
                facts.append(FactRecord("revenue", round(r * mult, 2), unit, pe, "Q", "consolidated", raw_key=key))
                facts.append(FactRecord("net_income", round(r * s.margin[-1] * 0.72 * mult, 2), unit, pe, "Q", "consolidated", raw_key=key))
        payload = json.dumps([f.__dict__ for f in facts], default=str, sort_keys=True).encode()
        missing = list(s.missing)
        return ProviderResult(status=OK, data=facts, raw=[RawPayload(key, payload, "application/json")], missing=missing,
                              notes=["Sample data — synthetic statements"])

    def restated(self, symbol: str) -> list[FactRecord]:
        """The corrected FY25 numbers for the restatement case (used by tests and the demo seed)."""
        s = BY_SYMBOL[symbol]
        a = annual_series(s)
        return [FactRecord(m, round(a[m][3] * CR, 2), "INR", FY_ENDS[3], "FY", "consolidated", raw_key=f"fixture:restated:{symbol}",
                           note="restated in FY26 annual report") for m in ("revenue", "net_income")]

    def get_announcements(self, sec: SecurityRef, limit: int = 40) -> ProviderResult:
        s = self._spec(sec)
        if not s:
            return ProviderResult(status=MISSING, error=f"No sample data for {sec.symbol}")
        base = datetime.combine(self.end, datetime.min.time(), tzinfo=UTC).replace(hour=10)
        recs = []
        events = s.events or [("45", "Financial Result Updates", "Quarterly results")]
        for i, (days, cat, txt) in enumerate(events):
            recs.append(AnnouncementRecord(external_id=f"SMP-{s.symbol}-{i}", category=cat, text=f"{s.name}: {txt}",
                                           published_at=base - timedelta(days=int(days)),
                                           attachment_url=f"fixture://{s.symbol}/presentation.pdf" if i == 0 else None,
                                           raw_key=f"fixture:announcements:{s.symbol}"))
        payload = json.dumps([r.__dict__ for r in recs], default=str, sort_keys=True).encode()
        return ProviderResult(status=OK, data=recs[:limit], raw=[RawPayload(f"fixture:announcements:{s.symbol}", payload, "application/json")])

    def fetch(self, url: str) -> ProviderResult:
        if not url.startswith("fixture://"):
            return ProviderResult(status=MISSING, error="Fixture provider only serves fixture:// documents")
        sym = url.split("/")[2]
        data = sample_document(BY_SYMBOL[sym])
        return ProviderResult(status=OK, data=data, raw=[RawPayload(f"fixture:file:{url}", data, "application/pdf", url=None)])

    def get_news(self) -> ProviderResult:
        base = datetime.combine(self.end, datetime.min.time(), tzinfo=UTC).replace(hour=9)
        items = []
        for i, s in enumerate(SPECS[:12]):
            head = {"inflection with lagging price": f"{s.name.replace(' (sample)', '')} wins large transmission order",
                    "auditor resignation": f"{s.name.replace(' (sample)', '')} auditor quits; shares slide"}.get(s.story, f"{s.name.replace(' (sample)', '')} posts quarterly numbers")
            items.append(NewsRecord(headline=f"[Sample] {head}", url=f"https://example.invalid/sample-news/{s.symbol.lower()}-{i}",
                                    publisher="Sample Wire", published_at=base - timedelta(hours=6 * i), summary="Synthetic headline for testing.",
                                    feed="fixture://news", raw_key="fixture:news"))
        # a duplicate story from a second "publisher" to exercise de-duplication
        items.append(NewsRecord(headline=items[0].headline, url="https://example.invalid/sample-news/dup", publisher="Sample Daily",
                                published_at=base - timedelta(hours=2), summary="Same story, different outlet.", feed="fixture://news", raw_key="fixture:news"))
        payload = json.dumps([n.__dict__ for n in items], default=str, sort_keys=True).encode()
        return ProviderResult(status=OK, data=items, raw=[RawPayload("fixture:news", payload, "application/json")])
