"""Research engine: gather inputs → compute (pure) → dossier → snapshot.

`compute(inputs)` is a pure function of the recorded input bundle (facts, prices, filings, data
state, cached AI extractions). The bundle is stored with each snapshot, so any snapshot can be
replayed and must reproduce the same output hash (`replay_snapshot`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import date, timedelta

from sqlalchemy import func, select

from app.research import models as m
from app.research.checks import COMMODITY_SECTORS, run_checks
from app.research.db import now, tx
from app.research.dimensions import DIMENSIONS, decide_status, score_dimensions
from app.research.metrics import Fact, FactSet, compute_all
from app.research.scenarios import build_scenarios

logger = logging.getLogger(__name__)
CODE_VERSION = "research-1.0"
CR = 1e7


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def digest(obj) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


# ── inputs ───────────────────────────────────────────────────────────────────

_index_cache: dict = {"ts": 0.0, "value": None}
_index_lock = threading.Lock()


def index_returns() -> dict | None:
    """Nifty 500 returns from the app's market-data provider (cached for an hour)."""
    with _index_lock:
        if time.time() - _index_cache["ts"] < 3600:
            return _index_cache["value"]
    value = None
    try:
        from app.services.curated_algo import _index_frame
        from app.services.scanner import scanner_service

        close, name = _index_frame(scanner_service, 300)
        c = close.dropna()
        if len(c) > 253:
            value = {"name": name, "as_of": str(c.index[-1].date()), "ret_1y": round((float(c.iloc[-1]) / float(c.iloc[-253]) - 1) * 100, 4),
                     "ret_3m": round((float(c.iloc[-1]) / float(c.iloc[-64]) - 1) * 100, 4)}
    except Exception as exc:  # noqa: BLE001
        logger.info("Index returns unavailable for research engine: %s", exc)
    with _index_lock:
        _index_cache.update(ts=time.time(), value=value)
    return value


def company_row(conn, symbol: str):
    return conn.execute(
        select(m.companies, m.securities.c.id.label("security_id"), m.securities.c.symbol)
        .join(m.securities, m.securities.c.company_id == m.companies.c.id)
        .where((m.securities.c.symbol == symbol.upper().removesuffix(".NS")) & (m.securities.c.exchange == "NSE"))
    ).mappings().first()


def gather_inputs(conn, comp) -> dict:
    cid = comp["id"]
    f = m.financial_facts.c
    facts = conn.execute(select(f.id, f.metric, f.period_type, f.period_end, f.value, f.unit, f.scope, f.provider, f.version, f.verification)
                         .where((f.company_id == cid) & f.is_current.is_(True)).order_by(f.id)).all()
    restated = conn.execute(select(func.count()).select_from(m.financial_facts).where((f.company_id == cid) & (f.version > 1) & f.is_current.is_(True))).scalar()
    p = m.prices_daily.c
    prices = conn.execute(select(p.date, p.close, p.provider).where(p.security_id == comp["security_id"]).order_by(p.date.desc()).limit(300)).all()
    prices = sorted(prices, key=lambda r: r.date)
    a = m.announcements.c
    anns = conn.execute(select(a.id, a.published_at, a.category, a.event_type, a.text, a.document_id)
                        .where(a.company_id == cid).order_by(a.published_at.desc()).limit(40)).all()
    d = m.documents.c
    docs = conn.execute(select(d.id, d.title, d.doc_type, d.published_at, d.pages, d.text_status, d.sha256).where(d.company_id == cid).order_by(d.id)).all()
    i = m.data_issues.c
    issues = conn.execute(select(i.kind, i.field, i.severity, i.detail).where((i.company_id == cid) & (i.status == "open"))).all()
    profile = conn.execute(select(m.settings_kv.c.value).where(m.settings_kv.c.key == f"profile:{cid}")).scalar() or {}
    extractions = []
    from app.research.llm import cached_extractions
    extractions = cached_extractions(conn, [doc.sha256 for doc in docs if doc.sha256])
    return {
        "code_version": CODE_VERSION,
        "company": {"id": cid, "name": comp["name"], "symbol": comp["symbol"], "isin": comp["isin"], "sector": comp["sector"], "industry": comp["industry"],
                    "is_financial": bool(comp["is_financial"]), "is_sample": bool(comp["is_sample"]), "status": comp["status"]},
        "facts": [[r.id, r.metric, r.period_type, str(r.period_end), r.value, r.unit, r.scope, r.provider, r.version, r.verification] for r in facts],
        "restated_count": int(restated or 0),
        "prices": [[str(r.date), r.close] for r in prices],
        "price_provider": prices[-1].provider if prices else None,
        "index": index_returns(),
        "announcements": [[r.id, str(r.published_at)[:10] if r.published_at else None, r.category, r.event_type, (r.text or "")[:300], r.document_id] for r in anns],
        "documents": [[r.id, r.title, r.doc_type, str(r.published_at)[:10] if r.published_at else None, r.pages, r.text_status] for r in docs],
        "issues": sorted([[r.kind, r.field, r.severity, (r.detail or "")[:200]] for r in issues]),
        "profile": {k: profile.get(k) for k in ("sharesOutstanding", "longBusinessSummary", "website", "financialCurrency") if profile.get(k) is not None},
        "extractions": extractions,
    }


# ── pure computation ─────────────────────────────────────────────────────────

def _fmt_cr(v: float | None) -> str:
    return "—" if v is None else f"₹{v / CR:,.0f} cr"


def _num(v: float | None, unit: str) -> str:
    if v is None:
        return "—"
    return {"pct": f"{v:.1f}%", "pp": f"{v:+.1f} pp", "x": f"{v:.2f}×", "days": f"{v:.0f} days", "INR": _fmt_cr(v)}.get(unit, f"{v:.2f}")


def compute(inputs: dict) -> dict:
    comp = inputs["company"]
    financial = comp["is_financial"]
    facts = [Fact(id=r[0], metric=r[1], period_type=r[2], period_end=date.fromisoformat(r[3]), value=r[4], unit=r[5], scope=r[6], provider=r[7], version=r[8])
             for r in inputs["facts"]]
    fs = FactSet(facts)
    closes = [(d, c) for d, c in inputs["prices"]]
    price = closes[-1][1] if closes else None
    shares_fb = inputs["profile"].get("sharesOutstanding")
    mt = compute_all(fs, financial, price, shares_fb, closes, inputs["index"])
    sector = f"{comp['sector'] or ''} {comp['industry'] or ''}"
    cyclical = any(w in sector.lower() for w in COMMODITY_SECTORS)
    margins = [e.inr / r.inr * 100 for e in fs.series("ebit") if (r := fs.at("revenue", e.period_end)) and r.inr > 0]
    gov = [{"id": a[0], "date": a[1], "category": a[2]} for a in inputs["announcements"] if a[3] == "governance"]
    checks = run_checks(mt, financial, sector, inputs["restated_count"], gov, margins)
    sh = fs.latest("shares") or fs.latest("diluted_shares")
    shares = sh.inr if sh else shares_fb
    scen = build_scenarios(fs, mt, financial, cyclical, price, shares)
    fy = fs.series("revenue")
    open_errors = sum(1 for i in inputs["issues"] if i[2] == "error")
    open_warn = sum(1 for i in inputs["issues"] if i[2] == "warn")
    stale = [i[3] for i in inputs["issues"] if i[0] == "stale"]
    data_state = {"open_errors": open_errors, "open_warnings": open_warn, "fy_count": len(fy), "stale": stale,
                  "verified_facts": sum(1 for r in inputs["facts"] if r[9] == "verified")}
    dims = score_dimensions(mt, checks, scen, financial, data_state)
    status, flags = decide_status(mt, checks, dims, data_state, financial, comp["status"])
    claims = build_claims(mt, checks, scen, dims, inputs)
    as_of = closes[-1][0] if closes else max((str(f.period_end) for f in facts), default=None)
    table = financial_table(fs)
    providers = sorted({r[7] for r in inputs["facts"]} | ({inputs["price_provider"]} if inputs["price_provider"] else set()))
    out = {
        "code_version": CODE_VERSION,
        "company": comp,
        "as_of": as_of,
        "status": status,
        "status_note": {"BLOCKED": "Clear disqualifier or essential data missing — not researchable as is.",
                        "REVIEW_REQUIRED": "Concerns that need your judgement before going further.",
                        "ELIGIBLE_FOR_RESEARCH": "Can be investigated further. This is not a buy signal."}[status],
        "flags": flags,
        "dimensions": [dims[k] for k, _ in DIMENSIONS],
        "metrics": {k: v.to_dict() for k, v in mt.items()},
        "checks": checks,
        "valuation": scen,
        "claims": claims,
        "financial_table": table,
        "price": {"last": price, "date": closes[-1][0] if closes else None, "points": len(closes)},
        "provenance": {"providers": providers, "facts": len(facts), "fact_issues": fs.issues,
                       "sample_data": comp["is_sample"], "index": inputs["index"], "documents": len(inputs["documents"]),
                       "announcements": len(inputs["announcements"]), "ai_extractions": len(inputs["extractions"])},
    }
    return out


def financial_table(fs: FactSet) -> list[dict]:
    rows = []
    for rev in fs.series("revenue")[-5:]:
        pe = rev.period_end

        def v(metric):
            f = fs.at(metric, pe)
            return None if f is None else round(f.inr / CR, 2) if f.unit not in ("shares", "ratio") else f.inr

        ebit, ni = fs.at("ebit", pe), fs.at("net_income", pe)
        rows.append({"period_end": str(pe), "revenue_cr": v("revenue"), "ebit_cr": v("ebit"), "net_income_cr": v("net_income"), "cfo_cr": v("cfo"),
                     "fcf_cr": v("fcf"), "debt_cr": v("total_debt"), "equity_cr": v("equity"),
                     "ebit_margin_pct": round(ebit.inr / rev.inr * 100, 2) if ebit and rev.inr > 0 else None,
                     "net_margin_pct": round(ni.inr / rev.inr * 100, 2) if ni and rev.inr > 0 else None,
                     "fact_ids": [f.id for f in (rev, ebit, ni) if f]})
    return rows


def build_claims(mt, checks, scen, dims, inputs) -> list[dict]:
    claims: list[dict] = []

    def add(section, text, ctype, evidence):
        claims.append({"id": f"c{len(claims) + 1}", "section": section, "text": text, "type": ctype, "evidence": evidence,
                       "status": "verified" if ctype in ("computed", "sourced") else "inference"})

    def mev(key):
        return [f"metric:{key}"] + [f"fact:{i}" for i in mt[key].inputs]

    for key, tmpl, section in (
        ("revenue_growth", "Revenue grew {v} in {p}.", "why"),
        ("revenue_cagr", "Revenue compounded {v} a year ({p}).", "why"),
        ("profit_cagr", "Net profit compounded {v} a year ({p}).", "why"),
        ("revenue_acceleration", "Revenue growth changed by {v} versus the prior year.", "why"),
        ("q_revenue_yoy", "The latest quarter's revenue was {v} year on year.", "why"),
        ("ebit_margin_change", "EBIT margin moved {v} over two years.", "why"),
        ("roce", "ROCE was {v} in {p}.", "why"),
        ("roe", "ROE was {v} in {p}.", "why"),
        ("cfo_pat", "Operating cash flow was {v} reported profit over {p}.", "check:cash_conversion"),
        ("debt_equity", "Debt to equity was {v} in {p}.", "check:leverage"),
        ("pe", "The stock trades at {v} earnings.", "valuation"),
        ("pb", "The stock trades at {v} book value.", "valuation"),
    ):
        x = mt[key]
        if section.startswith("check:"):        # a strength unless its check warns/fails
            chk = next((c for c in checks if c["key"] == section[6:]), None)
            section = "risk" if chk and chk["status"] in ("warn", "fail") else "why"
        if x.value is not None:
            add(section, tmpl.format(v=_num(x.value, x.unit), p=x.period or "the latest year"), "computed", mev(key))
    rd = scen.get("reverse_dcf") or {}
    if rd.get("implied_growth_pct") is not None:
        add("valuation", f"At today's price, a reverse DCF implies about {rd['implied_growth_pct']:.0f}% annual cash-flow growth for five years "
                         f"(delivered: {rd['delivered_growth_pct']:.0f}%)." if rd.get("delivered_growth_pct") is not None
            else f"At today's price, a reverse DCF implies about {rd['implied_growth_pct']:.0f}% annual growth.",
            "computed", ["valuation:reverse_dcf"] + [f"fact:{i}" for i in scen.get("inputs", [])])
    for name in ("bear", "base", "bull"):
        s = (scen.get("scenarios") or {}).get(name)
        if s and s.get("value_per_share") is not None:
            add("valuation", f"{name.title()} scenario estimate: ₹{s['value_per_share']:,.0f} per share ({s['upside_pct']:+.0f}% vs price).", "computed",
                [f"valuation:{name}"] + [f"fact:{i}" for i in scen.get("inputs", [])])
    for c in checks:
        if c["status"] in ("fail", "warn"):
            add("risk", f"{c['label']}: {c['detail']}.", "computed", [f"check:{c['key']}"] + [e for k in c["metrics"] if k in mt for e in mev(k)])
    for a in inputs["announcements"][:15]:
        if a[3] in ("order", "corporate", "results", "capital", "governance", "meeting"):
            section = "risk" if a[3] in ("governance",) else "catalyst"
            add(section, f"NSE filing {a[1]} ({a[2]}): {a[4][:200]}", "sourced", [f"ann:{a[0]}"])
    for ex in inputs["extractions"]:
        for c in ex.get("claims", []):
            add(c.get("section", "catalyst"), c["text"], "sourced" if c.get("status") == "verified" else "unverified",
                [f"chunk:{c['chunk_id']}"] if c.get("chunk_id") else [])
    g = next(d for d in dims.values() if d["key"] == "growth_inflection")["score"]
    acc = mt["revenue_acceleration"].value
    if acc is not None and acc > 3 and g is not None and g >= 60:
        add("why", "Growth appears to be accelerating, not just high — a possible fundamental inflection worth checking in the filings.", "inference",
            mev("revenue_acceleration") + mev("revenue_growth"))
    gap = dims["recognition_gap"]["score"]
    if gap is not None and gap >= 65:
        add("why", "Fundamentals have improved faster than the share price has moved — a possible recognition gap (or the market knows something the numbers don't show).",
            "inference", ["dimension:recognition_gap", "metric:rel_1y", "metric:ret_1y"])
    if rd.get("implied_growth_pct") is not None and rd.get("delivered_growth_pct") is not None and rd["implied_growth_pct"] > rd["delivered_growth_pct"] + 5:
        add("risk", "The price already assumes faster growth than the company has delivered — little room for disappointment.", "inference", ["valuation:reverse_dcf"])
    return claims


# ── dossier rendering ────────────────────────────────────────────────────────

def to_markdown(out: dict) -> str:
    c = out["company"]
    lines = [f"# {c['name']} ({c['symbol']}) — research dossier", ""]
    if c["is_sample"]:
        lines += ["> **Sample data.** This is a fictional company used to test the platform. None of these numbers are real.", ""]
    lines += [f"As of {out['as_of']} · code {out['code_version']} · status **{out['status']}** — {out['status_note']}", "",
              "Read-only research. Not investment advice; nothing here is a recommendation to buy or sell.", ""]
    if out["flags"]:
        lines += ["## Flags", ""] + [f"- **{f['level']}** — {f['text']}" for f in out["flags"]] + [""]
    lines += ["## Dimensions (kept separate — no combined score)", "", "| Dimension | Score | Data coverage |", "|---|---|---|"]
    for d in out["dimensions"]:
        score = "—" if d["score"] is None else f"{d['score']:.0f}"
        lines.append(f"| {d['label']} | {score} | {d['coverage_pct']}% |")
    for section, title in (("why", "Why it may be interesting"), ("risk", "What could go wrong"), ("valuation", "What the valuation implies"), ("catalyst", "Catalysts and filings")):
        items = [x for x in out["claims"] if x["section"] == section]
        if items:
            lines += ["", f"## {title}", ""]
            lines += [f"- {x['text']} *[{x['type']}; evidence: {', '.join(x['evidence'][:4]) or 'none'}]*" for x in items]
    lines += ["", "## Financials (₹ crore)", "", "| Period | Revenue | EBIT | Net profit | CFO | EBIT margin |", "|---|---|---|---|---|---|"]
    for r in out["financial_table"]:
        margin = "—" if r["ebit_margin_pct"] is None else f"{r['ebit_margin_pct']:.1f}%"
        cells = [r["period_end"]] + ["—" if r[k] is None else f"{r[k]:,.1f}" for k in ("revenue_cr", "ebit_cr", "net_income_cr", "cfo_cr")] + [margin]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "## Unavailable metrics", ""]
    for k, v in out["metrics"].items():
        if v["status"] != "ok":
            lines.append(f"- {v['label']}: {v['status']} — {v['reason']}")
    p = out["provenance"]
    lines += ["", "## Provenance", "", f"- Providers: {', '.join(p['providers']) or 'none'}", f"- Facts used: {p['facts']}",
              f"- Filings: {p['announcements']}, documents: {p['documents']}, AI extractions: {p['ai_extractions']}"]
    if p["fact_issues"]:
        lines += [f"- Data note: {x}" for x in p["fact_issues"]]
    return "\n".join(lines) + "\n"


# ── snapshots ────────────────────────────────────────────────────────────────

def build_snapshot(symbol: str) -> dict:
    with tx() as conn:
        comp = company_row(conn, symbol)
        if comp is None:
            raise KeyError(symbol)
        inputs = gather_inputs(conn, comp)
    out = compute(inputs)
    in_hash, out_hash = digest(inputs), digest(out)
    model = ",".join(sorted({e.get("model", "") for e in inputs["extractions"]} - {""})) or None
    with tx() as conn:
        s = m.research_snapshots.c
        cond = (s.company_id == comp["id"]) & (s.input_hash == in_hash) & (s.code_version == CODE_VERSION)
        cond &= (s.model_version == model) if model else s.model_version.is_(None)
        existing = conn.execute(select(s.id).where(cond)).first()
        if existing:
            return {"snapshot_id": existing.id, "created": False, "status": out["status"], "output": out}
        sid = conn.execute(m.research_snapshots.insert().values(
            company_id=comp["id"], as_of=date.fromisoformat(out["as_of"]) if out["as_of"] else date.today(), input_hash=in_hash, output_hash=out_hash,
            code_version=CODE_VERSION, model_version=model, status=out["status"],
            dimensions={d["key"]: d["score"] for d in out["dimensions"]}, flags=out["flags"], inputs=inputs, output=out, created_at=now(),
        )).inserted_primary_key[0]
    return {"snapshot_id": sid, "created": True, "status": out["status"], "output": out}


def replay_snapshot(snapshot_id: int) -> dict:
    with tx() as conn:
        row = conn.execute(select(m.research_snapshots).where(m.research_snapshots.c.id == snapshot_id)).mappings().first()
    if not row:
        raise KeyError(snapshot_id)
    if row["code_version"] != CODE_VERSION:
        return {"snapshot_id": snapshot_id, "reproducible": None, "reason": f"snapshot made by {row['code_version']}, running {CODE_VERSION}"}
    out = compute(row["inputs"])
    return {"snapshot_id": snapshot_id, "reproducible": digest(out) == row["output_hash"], "stored_hash": row["output_hash"], "replayed_hash": digest(out),
            "input_hash_check": digest(row["inputs"]) == row["input_hash"]}


def latest_snapshot(conn, company_id: int):
    s = m.research_snapshots.c
    return conn.execute(select(m.research_snapshots).where(s.company_id == company_id).order_by(s.id.desc()).limit(1)).mappings().first()
