"""Full technical + fundamental analysis of any NSE stock, with a transparent rating.

Every score is a weighted average of plain rules (listed in each factor's note), so a
grade can always be traced back to the numbers that produced it. The rating describes
the current technical and fundamental condition of the stock; it is not a price
forecast or investment advice.
"""
from __future__ import annotations

import logging
import math
from functools import lru_cache

import pandas as pd

from app.schemas import (
    AnalysisCategory,
    AnalysisFactor,
    AnalysisLevels,
    AnalysisSection,
    AnalysisSetup,
    MarketCapBucket,
    ScanUniverse,
    StockAnalysisResponse,
    StockSearchResult,
)
from app.services.indicators import apply_indicators
from app.services.market_data import MarketDataError
from app.services.patterns import detect_best_pattern
from app.services.relative_strength import build_relative_strength_snapshot
from app.services.selection_overlays import build_accumulation_snapshot
from app.services.trade_sim import plan_is_tradeable, plan_trade
from app.services.trend_overlays import build_price_levels, build_rs_line, build_weekly_trend
from app.services.universe import StockListing, find_listing, load_universe

logger = logging.getLogger(__name__)

GRADES = [(80, "A", "Strong"), (65, "B", "Favourable"), (50, "C", "Neutral"), (35, "D", "Weak"), (0, "E", "Poor")]


def grade_for(score: float) -> tuple[str, str]:
    for floor, grade, label in GRADES:
        if score >= floor:
            return grade, label
    return "E", "Poor"


def _status(score: float | None) -> str:
    if score is None:
        return "na"
    return "good" if score >= 65 else "bad" if score < 40 else "neutral"


def _band(value: float | None, bands: list[tuple[float, float]], default: float) -> float | None:
    """bands: [(threshold, score)] checked in order with value > threshold."""
    if value is None:
        return None
    for threshold, score in bands:
        if value > threshold:
            return score
    return default


def _weighted(categories: list[AnalysisCategory]) -> float | None:
    usable = [c for c in categories if c.score is not None]
    total = sum(c.weight for c in usable)
    if not usable or total == 0:
        return None
    return round(sum(c.score * c.weight for c in usable) / total, 1)


def _num(info: dict, key: str) -> float | None:
    try:
        v = float(info.get(key))
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _pct(info: dict, key: str) -> float | None:
    v = _num(info, key)
    return None if v is None else v * 100


def _fmt_pct(v: float | None, signed: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


def _fmt_x(v: float | None, dp: int = 1) -> str:
    return "—" if v is None else f"{v:.{dp}f}×"


# ── search ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _search_index() -> tuple[StockListing, ...]:
    seen: dict[str, StockListing] = {}
    for universe in (ScanUniverse.NIFTY500, ScanUniverse.NIFTY_SMALLCAP_250, ScanUniverse.MID_SMALL_2000_PLUS):
        try:
            for listing in load_universe(universe=universe):
                seen.setdefault(listing.symbol.upper(), listing)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Search index: %s unavailable. %s", universe.value, exc)
    return tuple(seen.values())


def search_stocks(query: str, limit: int = 10) -> list[StockSearchResult]:
    q = query.strip().upper()
    if not q:
        return []
    ranked: list[tuple[int, str, StockListing]] = []
    for listing in _search_index():
        base = listing.symbol.upper().removesuffix(".NS").removesuffix(".BO")
        name = listing.company_name.upper()
        if base == q:
            rank = 0
        elif base.startswith(q):
            rank = 1
        elif name.startswith(q):
            rank = 2
        elif any(word.startswith(q) for word in name.split()):
            rank = 3
        elif q in name or q in base:
            rank = 4
        else:
            continue
        ranked.append((rank, base, listing))
    cap_order = {MarketCapBucket.LARGE: 0, MarketCapBucket.MID: 1, MarketCapBucket.SMALL: 2}
    # Exact/prefix matches first; within a tier, larger companies first (what people usually mean).
    ranked.sort(key=lambda r: (r[0], cap_order.get(r[2].market_cap_bucket, 3), len(r[1]), r[1]))
    return [
        StockSearchResult(
            symbol=l.symbol, company_name=l.company_name, sector=l.sector, market_cap_bucket=l.market_cap_bucket
        )
        for _, _, l in ranked[:limit]
    ]


# ── technical ────────────────────────────────────────────────────────────────

def technical_section(frame: pd.DataFrame, benchmark_context) -> tuple[AnalysisSection, list[str], list[str], dict]:
    latest = frame.iloc[-1]
    close = float(latest["Close"])
    strengths: list[str] = []
    concerns: list[str] = []

    # Trend (30%): five moving-average checks
    checks = [
        ("Price above 20 EMA", close > latest["ema20"]),
        ("Price above 50 EMA", close > latest["ema50"]),
        ("Price above 200 EMA", close > latest["ema200"]),
        ("20 EMA above 50 EMA", latest["ema20"] > latest["ema50"]),
        ("50 EMA above 200 EMA", latest["ema50"] > latest["ema200"]),
    ]
    passed = sum(1 for _, ok in checks if ok)
    trend_score = passed / 5 * 100
    trend = AnalysisCategory(name="Trend", weight=30, score=trend_score, factors=[
        AnalysisFactor(name=label, value="Yes" if ok else "No", status="good" if ok else "bad") for label, ok in checks
    ] + [
        AnalysisFactor(name="EMA 20 / 50 / 200", value=f"₹{latest['ema20']:.1f} / ₹{latest['ema50']:.1f} / ₹{latest['ema200']:.1f}", status="na"),
    ])
    if passed == 5:
        strengths.append("Clean uptrend: price above the 20, 50 and 200 EMAs, which are stacked bullishly.")
    elif not checks[2][1]:
        concerns.append("Price is below its 200-day EMA — the long-term trend is down.")

    # Momentum (20%): RSI zone + 3-month return
    rsi = float(latest["rsi14"])
    rsi_score = 100 if 55 <= rsi <= 70 else 70 if 50 <= rsi < 55 or 70 < rsi <= 75 else 45 if 45 <= rsi < 50 else 40 if rsi > 75 else 20
    ret_3m = (close / float(frame["Close"].iloc[-64]) - 1) * 100 if len(frame) > 64 else None
    ret_1m = (close / float(frame["Close"].iloc[-22]) - 1) * 100 if len(frame) > 22 else None
    ret_score = _band(ret_3m, [(20, 100), (10, 80), (0, 60), (-10, 35)], 15)
    mom_score = rsi_score * 0.6 + (ret_score if ret_score is not None else rsi_score) * 0.4
    momentum = AnalysisCategory(name="Momentum", weight=20, score=round(mom_score, 1), factors=[
        AnalysisFactor(name="RSI (14)", value=f"{rsi:.1f}", status=_status(rsi_score),
                       note="Best zone 55–70; above 75 is stretched, below 45 is weak"),
        AnalysisFactor(name="1-month return", value=_fmt_pct(ret_1m, True), status=_status(_band(ret_1m, [(5, 80), (0, 60), (-5, 40)], 20))),
        AnalysisFactor(name="3-month return", value=_fmt_pct(ret_3m, True), status=_status(ret_score)),
    ])
    if rsi > 75:
        concerns.append(f"RSI {rsi:.0f} is overbought — new entries risk buying a short-term top.")

    # Relative strength vs Nifty (20%)
    rs = build_relative_strength_snapshot(frame, benchmark_context) if benchmark_context is not None else None
    rs_line = build_rs_line(frame, benchmark_context.benchmark_frame["Close"]) if benchmark_context is not None else None
    if rs is not None:
        rs_score = rs.score * 100
        factors = [
            AnalysisFactor(name="RS score", value=f"{rs_score:.0f}/100", status=_status(rs_score)),
            AnalysisFactor(name="Excess return 50D", value=_fmt_pct(rs.excess_return_50d_pct, True), status="good" if rs.excess_return_50d_pct > 0 else "bad"),
            AnalysisFactor(name="Excess return 120D", value=_fmt_pct(rs.excess_return_120d_pct, True), status="good" if rs.excess_return_120d_pct > 0 else "bad"),
        ]
        if rs_line is not None:
            factors.append(AnalysisFactor(
                name="RS line", value="New high, leading price" if rs_line.leads_price else "At new high" if rs_line.rs_line_new_high else f"{rs_line.distance_from_rs_high_pct:.1f}% below high",
                status="good" if rs_line.rs_line_new_high else "neutral"))
        relative = AnalysisCategory(name=f"Relative strength vs {rs.benchmark_name}", weight=20, score=round(rs_score, 1), factors=factors)
        if rs_score >= 70:
            strengths.append(f"Outperforming {rs.benchmark_name} (RS {rs_score:.0f}/100).")
        elif rs_score < 40:
            concerns.append(f"Underperforming {rs.benchmark_name} (RS {rs_score:.0f}/100).")
        if rs_line is not None and rs_line.leads_price:
            strengths.append("Relative-strength line is at a new high before price — a sign of institutional buying.")
    else:
        relative = AnalysisCategory(name="Relative strength", weight=20, score=None, factors=[
            AnalysisFactor(name="Benchmark", value="Unavailable", status="na")])

    # Volume & accumulation (15%)
    acc = build_accumulation_snapshot(frame, None)
    vol_ratio = float(latest["volume_ratio"]) if not pd.isna(latest["volume_ratio"]) else None
    acc_score = acc.score * 100
    volume = AnalysisCategory(name="Volume & accumulation", weight=15, score=round(acc_score, 1), factors=[
        AnalysisFactor(name="Accumulation score", value=f"{acc_score:.0f}/100", status=_status(acc_score)),
        AnalysisFactor(name="Up/down volume (10D)", value=f"{acc.up_volume_ratio_10d:.2f}", status="good" if acc.up_volume_ratio_10d >= 1.2 else "bad" if acc.up_volume_ratio_10d < 0.8 else "neutral",
                       note="Above 1 means more volume on up days than down days"),
        AnalysisFactor(name="Today's volume vs 20D avg", value=_fmt_x(vol_ratio, 2), status="good" if (vol_ratio or 0) >= 1.5 else "neutral"),
    ])
    if acc.up_volume_ratio_10d >= 1.5:
        strengths.append("Buyers dominate: up-day volume is well above down-day volume.")
    elif acc.up_volume_ratio_10d < 0.7:
        concerns.append("Sellers dominate recent volume (distribution).")

    # Structure: weekly trend + 52-week position (15%)
    weekly = build_weekly_trend(frame)
    levels_52 = build_price_levels(frame)
    parts = []
    factors = []
    if weekly is not None:
        parts.append(weekly.checks_passed / 3 * 100)
        factors.append(AnalysisFactor(name="Weekly trend", value=f"{weekly.checks_passed}/3 checks", status="good" if weekly.aligned else "bad",
                                      note="Above 20-week EMA, weekly RSI > 50, rising weekly volume"))
    if levels_52 is not None:
        d = levels_52.distance_from_52w_high_pct
        parts.append(100 if d <= 5 else 70 if d <= 15 else 40 if d <= 30 else 15)
        factors.append(AnalysisFactor(name="Distance from 52-week high", value=f"{d:.1f}%", status="good" if d <= 5 else "bad" if d > 30 else "neutral"))
        if levels_52.price_discovery:
            strengths.append("Made a fresh 52-week high in the last 5 sessions (price discovery).")
        elif d > 30:
            concerns.append(f"Trading {d:.0f}% below its 52-week high.")
    structure = AnalysisCategory(name="Structure", weight=15, score=round(sum(parts) / len(parts), 1) if parts else None, factors=factors)

    categories = [trend, momentum, relative, volume, structure]
    score = _weighted(categories)
    grade, label = grade_for(score) if score is not None else (None, None)
    section = AnalysisSection(available=score is not None, score=score, grade=grade, label=label, categories=categories)
    context = {"rs": rs, "ret_1m": ret_1m}
    return section, strengths, concerns, context


# ── fundamental ──────────────────────────────────────────────────────────────

def fundamental_section(info: dict) -> tuple[AnalysisSection, list[str], list[str]]:
    strengths: list[str] = []
    concerns: list[str] = []
    if not info:
        return AnalysisSection(available=False, score=None, grade=None, label=None,
                               note="Fundamental data isn't available for this stock right now."), strengths, concerns

    rev_g, earn_g = _pct(info, "revenueGrowth"), _pct(info, "earningsGrowth")
    rev_s = _band(rev_g, [(20, 100), (10, 80), (5, 60), (0, 45)], 15)
    earn_s = _band(earn_g, [(20, 100), (10, 80), (5, 60), (0, 45)], 15)
    growth = AnalysisCategory(name="Growth", weight=25, score=_avg(rev_s, earn_s), factors=[
        AnalysisFactor(name="Revenue growth (YoY)", value=_fmt_pct(rev_g, True), status=_status(rev_s)),
        AnalysisFactor(name="Earnings growth (YoY)", value=_fmt_pct(earn_g, True), status=_status(earn_s)),
    ])
    if rev_g is not None and earn_g is not None and rev_g > 15 and earn_g > 15:
        strengths.append(f"Strong growth: revenue {rev_g:+.0f}% and earnings {earn_g:+.0f}% year on year.")
    if earn_g is not None and earn_g < -10:
        concerns.append(f"Earnings are shrinking ({earn_g:+.0f}% YoY).")

    roe, op_m, net_m = _pct(info, "returnOnEquity"), _pct(info, "operatingMargins"), _pct(info, "profitMargins")
    roe_s = _band(roe, [(20, 100), (15, 80), (12, 65), (8, 40)], 15)
    op_s = _band(op_m, [(20, 100), (12, 75), (8, 55), (0, 35)], 10)
    net_s = _band(net_m, [(15, 100), (8, 70), (3, 45), (0, 30)], 10)
    profitability = AnalysisCategory(name="Profitability", weight=25, score=_avg(roe_s, op_s, net_s), factors=[
        AnalysisFactor(name="Return on equity", value=_fmt_pct(roe), status=_status(roe_s), note="Above 15% is good"),
        AnalysisFactor(name="Operating margin", value=_fmt_pct(op_m), status=_status(op_s)),
        AnalysisFactor(name="Net profit margin", value=_fmt_pct(net_m), status=_status(net_s)),
    ])
    if roe is not None and roe >= 20:
        strengths.append(f"High return on equity ({roe:.0f}%).")
    if net_m is not None and net_m < 0:
        concerns.append("The company is currently loss-making.")

    raw_de = _num(info, "debtToEquity")
    de = raw_de / 100 if raw_de is not None else None
    cr = _num(info, "currentRatio")
    de_s = _band(None if de is None else -de, [(-0.3, 100), (-0.8, 80), (-1.5, 55), (-2.5, 25)], 10)
    cr_s = _band(cr, [(1.5, 90), (1.0, 65)], 30)
    balance = AnalysisCategory(name="Balance sheet", weight=20, score=_avg(de_s, cr_s), factors=[
        AnalysisFactor(name="Debt / equity", value=_fmt_x(de, 2), status=_status(de_s), note="Below 0.8× is comfortable"),
        AnalysisFactor(name="Current ratio", value=_fmt_x(cr, 2), status=_status(cr_s), note="Above 1.5× is healthy"),
    ])
    if de is not None and de > 2:
        concerns.append(f"High leverage: debt is {de:.1f}× equity.")
    elif de is not None and de < 0.1:
        strengths.append("Virtually debt-free balance sheet.")

    pe, fpe, pb = _num(info, "trailingPE"), _num(info, "forwardPE"), _num(info, "priceToBook")
    ev_ebitda = _num(info, "enterpriseToEbitda")
    pe_s = (10 if pe is not None and pe <= 0 else _band(None if pe is None else -pe, [(-15, 90), (-25, 75), (-40, 55), (-60, 35)], 15))
    pb_s = _band(None if pb is None else -pb, [(-3, 80), (-6, 60), (-10, 40)], 20)
    valuation = AnalysisCategory(name="Valuation", weight=20, score=_avg(pe_s, pb_s), factors=[
        AnalysisFactor(name="P/E (trailing)", value=_fmt_x(pe), status=_status(pe_s),
                       note="Absolute bands; fair P/E differs a lot by sector"),
        AnalysisFactor(name="P/E (forward)", value=_fmt_x(fpe), status="na"),
        AnalysisFactor(name="Price / book", value=_fmt_x(pb), status=_status(pb_s)),
        AnalysisFactor(name="EV / EBITDA", value=_fmt_x(ev_ebitda), status="na"),
    ])
    if pe is not None and pe > 60:
        concerns.append(f"Expensive at {pe:.0f}× trailing earnings — little room for disappointment.")

    ins, inst = _pct(info, "heldPercentInsiders"), _pct(info, "heldPercentInstitutions")
    ins_s = _band(ins, [(50, 90), (35, 70), (20, 50)], 30)
    inst_s = _band(inst, [(25, 85), (10, 65)], 40)
    ownership = AnalysisCategory(name="Ownership", weight=10, score=_avg(ins_s, inst_s), factors=[
        AnalysisFactor(name="Promoter / insider holding", value=_fmt_pct(ins), status=_status(ins_s), note="Pledge data not included"),
        AnalysisFactor(name="Institutional holding", value=_fmt_pct(inst), status=_status(inst_s)),
    ])

    categories = [growth, profitability, balance, valuation, ownership]
    covered = sum(1 for c in categories if c.score is not None)
    if covered < 2:
        return AnalysisSection(available=False, score=None, grade=None, label=None, categories=categories,
                               note="Too little fundamental data was published for a reliable score."), strengths, concerns
    score = _weighted(categories)
    grade, label = grade_for(score)
    note = None if covered == 5 else f"Scored on {covered} of 5 categories; the rest had no published data."
    return AnalysisSection(available=True, score=score, grade=grade, label=label, categories=categories, note=note), strengths, concerns


def _avg(*values: float | None) -> float | None:
    usable = [v for v in values if v is not None]
    return round(sum(usable) / len(usable), 1) if usable else None


# ── orchestration ────────────────────────────────────────────────────────────

def analyze_stock(scanner, symbol: str) -> StockAnalysisResponse | None:
    listing = find_listing(symbol)
    in_universe = listing is not None
    if listing is None:
        sym = symbol.strip().upper()
        sym = sym if "." in sym else f"{sym}.NS"
        listing = StockListing(sym, sym.removesuffix(".NS"), "Unknown", MarketCapBucket.SMALL)

    try:
        raw = scanner.market_data.get_history(listing, lookback_days=320)
    except MarketDataError as exc:
        logger.warning("Analysis: no price data for %s. %s", listing.symbol, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Analysis failed for %s. %s", listing.symbol, exc)
        return None
    if raw is None or len(raw) < 60:
        return None

    frame = apply_indicators(raw)
    benchmark_context = scanner._load_benchmark_context(320)
    notes: list[str] = []

    technical, t_strengths, t_concerns, ctx = technical_section(frame, benchmark_context)

    info: dict = {}
    if scanner._external_overlays_enabled():
        info = scanner.fundamentals.get_info(listing.symbol)
    else:
        notes.append("Fundamental data is switched off in demo mode.")
    fundamental, f_strengths, f_concerns = fundamental_section(info)

    if fundamental.available and technical.available:
        overall = round(technical.score * 0.5 + fundamental.score * 0.5, 1)
        basis = "an equal blend of the technical and fundamental scores"
    else:
        overall = technical.score or 0.0
        basis = "the technical score only (fundamentals unavailable)"
    grade, label = grade_for(overall)

    latest = frame.iloc[-1]
    close = float(latest["Close"])
    prev = float(frame["Close"].iloc[-2])

    setup = None
    match = detect_best_pattern(frame, ctx["rs"])
    if match is not None:
        plan = plan_trade(latest, match.trigger_price, match.support_price, match.reward_multiple)
        setup = AnalysisSetup(
            pattern=match.pattern, explanation=match.explanation,
            entry=plan.entry, stop=plan.stop, target=plan.target,
            risk_reward=round(plan.reward_multiple, 2), tradeable=plan_is_tradeable(plan),
        )

    levels = AnalysisLevels(
        support_20d=round(float(frame["Low"].tail(20).min()), 2),
        resistance_20d=round(float(frame["High"].tail(20).max()), 2),
        support_60d=round(float(frame["Low"].tail(60).min()), 2),
        resistance_60d=round(float(frame["High"].tail(60).max()), 2),
        high_52w=round(float(frame["High"].tail(252).max()), 2),
        low_52w=round(float(frame["Low"].tail(252).min()), 2),
        atr_pct=round(float(latest["atr_pct"]) * 100, 2),  # stored as a fraction
    )

    regime = None
    try:
        regime = scanner.market_regime().regime
    except Exception:  # noqa: BLE001
        pass

    tech_txt = f"technicals {technical.label.lower()} ({technical.score:.0f})" if technical.available else "technicals unavailable"
    fund_txt = f"fundamentals {fundamental.label.lower()} ({fundamental.score:.0f})" if fundamental.available else "fundamentals unavailable"
    summary = f"{grade} · {label}: {tech_txt}, {fund_txt}. Overall score {overall:.0f}/100, based on {basis}."
    if regime == "bear":
        summary += " The broad market is risk-off, which lowers the odds for any long trade."

    market_cap = _num(info, "marketCap")
    description = info.get("longBusinessSummary") if isinstance(info.get("longBusinessSummary"), str) else None
    if description and len(description) > 600:
        description = description[:600].rsplit(" ", 1)[0] + "…"
    sector = listing.sector if listing.sector != "Unknown" else (info.get("sector") or "Unknown")
    company = info.get("longName") or info.get("shortName") or listing.company_name
    if not in_universe:
        notes.append("This stock isn't in the scanner's universes, so it won't appear in scan results.")

    candles = [
        {"date": idx.to_pydatetime(), "open": float(r["Open"]), "high": float(r["High"]),
         "low": float(r["Low"]), "close": float(r["Close"]), "volume": int(r["Volume"])}
        for idx, r in raw.tail(120).iterrows()
    ]

    return StockAnalysisResponse(
        symbol=listing.symbol, company_name=company, sector=sector,
        industry=info.get("industry"), description=description, in_scan_universe=in_universe,
        price=round(close, 2), change_pct=round((close / prev - 1) * 100, 2), as_of=frame.index[-1].date(),
        market_cap_cr=round(market_cap / 1e7, 0) if market_cap else None,
        overall_score=overall, overall_grade=grade, overall_label=label, summary=summary,
        technical=technical, fundamental=fundamental,
        strengths=(t_strengths + f_strengths)[:8], concerns=(t_concerns + f_concerns)[:8],
        setup=setup, levels=levels, regime=regime, candles=candles, data_notes=notes,
    )
