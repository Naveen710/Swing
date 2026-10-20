"""Deterministic financial metrics. Pure functions over an input bundle — no database, no clock.

Every result is a Metric with: value (or None), status (ok | unavailable | not_applicable),
reason when not ok, formula text + version, and the IDs of the facts it used. Missing, zero,
negative or unit-inconsistent inputs produce an unavailable result with a reason — never a guess.

Sector rules: banks/NBFCs/insurers ("financial") skip ROCE, debt/equity, interest cover, FCF
and EV/EBITDA (borrowing is their raw material); they get ROE and P/B instead.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date

MONEY_UNITS = {"INR": 1.0, "INR_cr": 1e7, "INR_lakh": 1e5, "INR_mn": 1e6}
PROVIDER_PRIORITY = {"manual": 0, "yahoo": 1, "fixture": 2}
SCOPE_PRIORITY = {"consolidated": 0, "unknown": 1, "standalone": 2}
FY_GAP = (330, 400)          # days between consecutive fiscal years
YOY_GAP = (350, 380)         # days between a quarter and the same quarter a year earlier


def to_inr(value: float | None, unit: str) -> float | None:
    if value is None:
        return None
    mult = MONEY_UNITS.get(unit)
    return None if mult is None else value * mult


@dataclass
class Fact:
    id: int
    metric: str
    period_type: str
    period_end: date
    value: float | None
    unit: str
    scope: str
    provider: str
    version: int = 1

    @property
    def inr(self) -> float | None:
        if self.unit in ("shares", "ratio"):
            return self.value
        return to_inr(self.value, self.unit)


@dataclass
class Metric:
    key: str
    label: str
    value: float | None
    unit: str                         # pct | ratio | x | days | INR | pp
    status: str = "ok"                # ok | unavailable | not_applicable
    reason: str | None = None
    formula: str = ""
    version: str = "1"
    inputs: list[int] = field(default_factory=list)
    period: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["value"] is not None:
            d["value"] = round(d["value"], 6)
        return d


def na(key, label, unit, reason, formula="", status="unavailable", inputs=None) -> Metric:
    return Metric(key, label, None, unit, status=status, reason=reason, formula=formula, inputs=inputs or [])


class FactSet:
    """Current facts for one company, one value per (metric, period_type, period_end): the
    preferred scope (consolidated > unknown > standalone) then provider (manual > yahoo > fixture).
    A series never mixes scopes: the scope with most periods wins for that metric."""

    def __init__(self, facts: list[Fact]) -> None:
        self.facts = facts
        self.issues: list[str] = []
        self._series: dict[tuple[str, str], list[Fact]] = {}
        groups: dict[tuple[str, str], list[Fact]] = {}
        for f in facts:
            if f.value is None:
                continue
            groups.setdefault((f.metric, f.period_type), []).append(f)
        for key, items in groups.items():
            scopes: dict[str, int] = {}
            for f in items:
                scopes[f.scope] = scopes.get(f.scope, 0) + 1
            scope = sorted(scopes, key=lambda s: (-scopes[s], SCOPE_PRIORITY.get(s, 9)))[0]
            if len(scopes) > 1:
                self.issues.append(f"{key[0]} ({key[1]}) has mixed scopes {sorted(scopes)}; using {scope}")
            chosen: dict[date, Fact] = {}
            for f in sorted(items, key=lambda f: PROVIDER_PRIORITY.get(f.provider, 9)):
                if f.scope == scope and f.period_end not in chosen:
                    chosen[f.period_end] = f
            self._series[key] = sorted(chosen.values(), key=lambda f: f.period_end)

    def series(self, metric: str, period_type: str = "FY") -> list[Fact]:
        return [f for f in self._series.get((metric, period_type), []) if f.inr is not None]

    def unit_problems(self, metric: str, period_type: str = "FY") -> list[str]:
        raw = self._series.get((metric, period_type), [])
        return sorted({f.unit for f in raw if f.inr is None})

    def latest(self, metric: str, period_type: str = "FY") -> Fact | None:
        s = self.series(metric, period_type)
        return s[-1] if s else None

    def at(self, metric: str, period_end: date, period_type: str = "FY") -> Fact | None:
        return next((f for f in self.series(metric, period_type) if f.period_end == period_end), None)


def _gap_ok(a: date, b: date, rng=FY_GAP) -> bool:
    return rng[0] <= (b - a).days <= rng[1]


def _fy(d: date) -> str:
    return f"FY{str(d.year)[-2:]}" if d.month <= 3 else f"FY{str(d.year + 1)[-2:]}"


# ── growth ───────────────────────────────────────────────────────────────────

def yoy(fs: FactSet, metric: str, key: str, label: str, back: int = 0) -> Metric:
    formula = f"{metric}[FY t] / {metric}[FY t-1] − 1"
    s = fs.series(metric)
    if fs.unit_problems(metric):
        return na(key, label, "pct", f"unrecognised unit(s) {fs.unit_problems(metric)} for {metric}", formula)
    if len(s) < 2 + back:
        return na(key, label, "pct", f"needs {2 + back} annual {metric} values, have {len(s)}", formula)
    cur, prev = s[-1 - back], s[-2 - back]
    if not _gap_ok(prev.period_end, cur.period_end):
        return na(key, label, "pct", f"{prev.period_end} and {cur.period_end} are not consecutive fiscal years", formula, inputs=[prev.id, cur.id])
    if prev.inr <= 0:
        return na(key, label, "pct", f"base-year {metric} is {'zero' if prev.inr == 0 else 'negative'} — growth undefined", formula, inputs=[prev.id, cur.id])
    return Metric(key, label, (cur.inr / prev.inr - 1) * 100, "pct", formula=formula, inputs=[prev.id, cur.id], period=_fy(cur.period_end))


def cagr(fs: FactSet, metric: str, key: str, label: str, years: int = 3) -> Metric:
    formula = f"({metric}[FY t] / {metric}[FY t-n]) ^ (1/n) − 1, n ≤ {years}"
    s = fs.series(metric)
    if fs.unit_problems(metric):
        return na(key, label, "pct", f"unrecognised unit(s) {fs.unit_problems(metric)} for {metric}", formula)
    if len(s) < 3:
        return na(key, label, "pct", f"needs at least 3 annual {metric} values, have {len(s)}", formula)
    last = s[-1]
    start = s[max(0, len(s) - 1 - years)]
    n = round((last.period_end - start.period_end).days / 365.25)
    if n < 2:
        return na(key, label, "pct", "less than 2 years between first and last value", formula, inputs=[start.id, last.id])
    if start.inr <= 0 or last.inr <= 0:
        return na(key, label, "pct", f"{metric} is ≤ 0 at the start or end — CAGR undefined (turnaround or loss)", formula, inputs=[start.id, last.id])
    return Metric(key, label, ((last.inr / start.inr) ** (1 / n) - 1) * 100, "pct", formula=formula.replace("n ≤", f"n = {n}, max"),
                  inputs=[start.id, last.id], period=f"{_fy(start.period_end)}–{_fy(last.period_end)}")


def acceleration(cur: Metric, prev: Metric, key: str, label: str) -> Metric:
    formula = f"{cur.key} − {prev.key} (percentage points)"
    if cur.value is None or prev.value is None:
        return na(key, label, "pp", f"needs both {cur.key} and {prev.key}", formula, inputs=cur.inputs + prev.inputs)
    return Metric(key, label, cur.value - prev.value, "pp", formula=formula, inputs=sorted(set(cur.inputs + prev.inputs)), period=cur.period)


def quarter_yoy(fs: FactSet, metric: str, key: str, label: str) -> Metric:
    formula = f"{metric}[Q t] / {metric}[same quarter a year earlier] − 1 (matched by date, 350–380 days)"
    s = fs.series(metric, "Q")
    if not s:
        return na(key, label, "pct", f"no quarterly {metric}", formula)
    cur = s[-1]
    base = next((f for f in s if _gap_ok(f.period_end, cur.period_end, YOY_GAP)), None)
    if base is None:
        return na(key, label, "pct", f"no quarter a year before {cur.period_end}", formula, inputs=[cur.id])
    if base.inr <= 0:
        return na(key, label, "pct", "year-ago quarter is ≤ 0", formula, inputs=[base.id, cur.id])
    return Metric(key, label, (cur.inr / base.inr - 1) * 100, "pct", formula=formula, inputs=[base.id, cur.id], period=str(cur.period_end))


# ── profitability / returns ──────────────────────────────────────────────────

def margin(fs: FactSet, num: str, key: str, label: str, back: int = 0) -> Metric:
    formula = f"{num} / revenue (same fiscal year)"
    n, r = fs.series(num), fs.series("revenue")
    if len(n) < 1 + back:
        return na(key, label, "pct", f"needs {num} for {1 + back} years", formula)
    f = n[-1 - back]
    rev = fs.at("revenue", f.period_end)
    if rev is None:
        return na(key, label, "pct", f"no revenue for {f.period_end}", formula, inputs=[f.id])
    if rev.inr <= 0:
        return na(key, label, "pct", "revenue ≤ 0", formula, inputs=[f.id, rev.id])
    return Metric(key, label, f.inr / rev.inr * 100, "pct", formula=formula, inputs=[f.id, rev.id], period=_fy(f.period_end))


def margin_change(fs: FactSet, num: str, key: str, label: str, years: int = 2) -> Metric:
    a, b = margin(fs, num, "x", "x"), margin(fs, num, "x", "x", back=years)
    formula = f"{num} margin[FY t] − {num} margin[FY t-{years}] (pp)"
    if a.value is None or b.value is None:
        return na(key, label, "pp", f"needs {num} margin for {years + 1} years", formula, inputs=a.inputs + b.inputs)
    return Metric(key, label, a.value - b.value, "pp", formula=formula, inputs=sorted(set(a.inputs + b.inputs)), period=a.period)


def roe(fs: FactSet) -> Metric:
    key, label, formula = "roe", "Return on equity", "net_income / average(equity t, equity t-1); latest equity if no prior year"
    ni, eq = fs.latest("net_income"), fs.series("equity")
    if ni is None or not eq:
        return na(key, label, "pct", "needs net income and equity", formula)
    cur = fs.at("equity", ni.period_end)
    if cur is None:
        return na(key, label, "pct", f"no equity for {ni.period_end}", formula, inputs=[ni.id])
    prev = next((e for e in eq if _gap_ok(e.period_end, cur.period_end)), None)
    base = (cur.inr + prev.inr) / 2 if prev else cur.inr
    ids = [ni.id, cur.id] + ([prev.id] if prev else [])
    if base <= 0:
        return na(key, label, "pct", "equity ≤ 0 — ROE meaningless", formula, inputs=ids)
    return Metric(key, label, ni.inr / base * 100, "pct", formula=formula, inputs=ids, period=_fy(ni.period_end))


def roce(fs: FactSet, financial: bool, back: int = 0, key: str = "roce", label: str = "Return on capital employed") -> Metric:
    formula = "EBIT / (total assets − current liabilities); proxy: EBIT / (equity + debt)"
    if financial:
        return na(key, label, "pct", "not used for banks/NBFCs/insurers", formula, status="not_applicable")
    e = fs.series("ebit")
    if len(e) < 1 + back:
        return na(key, label, "pct", "needs EBIT", formula)
    ebit = e[-1 - back]
    ta, cl = fs.at("total_assets", ebit.period_end), fs.at("current_liabilities", ebit.period_end)
    if ta and cl and ta.inr - cl.inr > 0:
        return Metric(key, label, ebit.inr / (ta.inr - cl.inr) * 100, "pct", formula=formula, inputs=[ebit.id, ta.id, cl.id], period=_fy(ebit.period_end))
    eq, debt = fs.at("equity", ebit.period_end), fs.at("total_debt", ebit.period_end)
    if eq and eq.inr + (debt.inr if debt else 0) > 0:
        ids = [ebit.id, eq.id] + ([debt.id] if debt else [])
        return Metric(key, label, ebit.inr / (eq.inr + (debt.inr if debt else 0)) * 100, "pct", formula=formula + " [proxy used]", inputs=ids, period=_fy(ebit.period_end))
    return na(key, label, "pct", "no capital employed (assets/liabilities or equity) for that year", formula, inputs=[ebit.id])


def ratio_latest(fs: FactSet, num: str, den: str, key: str, label: str, unit: str = "x", financial_na: bool = False,
                 financial: bool = False, positive_den: bool = True) -> Metric:
    formula = f"{num} / {den} (latest fiscal year)"
    if financial_na and financial:
        return na(key, label, unit, "not used for banks/NBFCs/insurers", formula, status="not_applicable")
    n = fs.latest(num)
    if n is None:
        return na(key, label, unit, f"no {num}", formula)
    d = fs.at(den, n.period_end)
    if d is None:
        return na(key, label, unit, f"no {den} for {n.period_end}", formula, inputs=[n.id])
    if positive_den and d.inr <= 0:
        return na(key, label, unit, f"{den} ≤ 0", formula, inputs=[n.id, d.id])
    if d.inr == 0:
        return na(key, label, unit, f"{den} is zero", formula, inputs=[n.id, d.id])
    return Metric(key, label, n.inr / d.inr, unit, formula=formula, inputs=[n.id, d.id], period=_fy(n.period_end))


def debt_equity(fs: FactSet, financial: bool) -> Metric:
    m = ratio_latest(fs, "total_debt", "equity", "debt_equity", "Debt / equity", financial_na=True, financial=financial)
    if m.status == "unavailable" and m.reason and m.reason.startswith("equity ≤ 0"):
        m.reason = "negative equity — leverage is extreme"
    return m


def interest_cover(fs: FactSet, financial: bool) -> Metric:
    key, label, formula = "interest_cover", "Interest cover", "EBIT / interest expense (latest FY)"
    if financial:
        return na(key, label, "x", "not used for banks/NBFCs/insurers", formula, status="not_applicable")
    e = fs.latest("ebit")
    if e is None:
        return na(key, label, "x", "no EBIT", formula)
    i = fs.at("interest_expense", e.period_end)
    debt = fs.at("total_debt", e.period_end)
    if i is None or abs(i.inr) < 1:
        if debt is not None and debt.inr < 1e7:
            return na(key, label, "x", "effectively debt-free — no meaningful interest", formula, status="not_applicable", inputs=[e.id, debt.id])
        return na(key, label, "x", "no interest expense line although debt exists" if debt else "no interest expense line", formula, inputs=[e.id])
    return Metric(key, label, e.inr / abs(i.inr), "x", formula=formula, inputs=[e.id, i.id], period=_fy(e.period_end))


def sum_ratio(fs: FactSet, num: str, den: str, key: str, label: str, years: int = 3, financial: bool = False, financial_na: bool = False) -> Metric:
    formula = f"Σ {num} / Σ {den} over the last {years} fiscal years where both exist"
    if financial and financial_na:
        return na(key, label, "x", "not meaningful for lenders (loan growth is operating cash outflow)", formula, status="not_applicable")
    pairs = []
    for d in fs.series(den)[-years:]:
        n = fs.at(num, d.period_end)
        if n is not None:
            pairs.append((n, d))
    if len(pairs) < 2:
        return na(key, label, "x", f"needs {num} and {den} for at least 2 of the last {years} years", formula)
    sd = sum(d.inr for _, d in pairs)
    ids = [x.id for p in pairs for x in p]
    if sd <= 0:
        return na(key, label, "x", f"Σ {den} ≤ 0 (losses) — ratio not meaningful", formula, inputs=ids)
    return Metric(key, label, sum(n.inr for n, _ in pairs) / sd, "x", formula=formula, inputs=ids,
                  period=f"{_fy(pairs[0][1].period_end)}–{_fy(pairs[-1][1].period_end)}")


def days_metric(fs: FactSet, item: str, key: str, label: str, back: int = 0) -> Metric:
    formula = f"{item} / revenue × 365 (same fiscal year)"
    s = fs.series(item)
    if len(s) < 1 + back:
        return na(key, label, "days", f"no {item}", formula)
    f = s[-1 - back]
    r = fs.at("revenue", f.period_end)
    if r is None or r.inr <= 0:
        return na(key, label, "days", "no positive revenue that year", formula, inputs=[f.id])
    return Metric(key, label, f.inr / r.inr * 365, "days", formula=formula, inputs=[f.id, r.id], period=_fy(f.period_end))


def change(cur: Metric, prev: Metric, key: str, label: str, unit: str) -> Metric:
    formula = f"{cur.key}[t] − {cur.key}[t-2]"
    if cur.value is None or prev.value is None:
        return na(key, label, unit, "needs both years", formula, inputs=cur.inputs + prev.inputs)
    return Metric(key, label, cur.value - prev.value, unit, formula=formula, inputs=sorted(set(cur.inputs + prev.inputs)), period=cur.period)


def share_change(fs: FactSet, years: int = 3) -> Metric:
    key, label, formula = "share_change", "Share count change", f"shares[t] / shares[t-{years}] − 1"
    s = fs.series("shares") or fs.series("diluted_shares")
    if len(s) < 2:
        return na(key, label, "pct", "needs share count for 2+ years", formula)
    start = s[max(0, len(s) - 1 - years)]
    if start.inr <= 0:
        return na(key, label, "pct", "share count ≤ 0", formula, inputs=[start.id, s[-1].id])
    return Metric(key, label, (s[-1].inr / start.inr - 1) * 100, "pct", formula=formula, inputs=[start.id, s[-1].id],
                  period=f"{_fy(start.period_end)}–{_fy(s[-1].period_end)}")


def other_income_share(fs: FactSet) -> Metric:
    m = ratio_latest(fs, "other_income", "pbt", "other_income_share", "Other income / profit before tax", unit="pct")
    if m.value is not None:
        m.value *= 100
    return m


def ttm_net_income(fs: FactSet) -> tuple[float | None, list[int], str]:
    q = fs.series("net_income", "Q")
    if len(q) >= 4 and (q[-1].period_end - q[-4].period_end).days in range(250, 300):
        return sum(f.inr for f in q[-4:]), [f.id for f in q[-4:]], "sum of last 4 quarters"
    fy = fs.latest("net_income")
    if fy:
        return fy.inr, [fy.id], f"latest fiscal year ({_fy(fy.period_end)})"
    return None, [], "none"


# ── valuation ratios (need price) ────────────────────────────────────────────

def valuation_ratios(fs: FactSet, price: float | None, financial: bool, shares_fallback: float | None) -> dict[str, Metric]:
    out: dict[str, Metric] = {}
    sh = fs.latest("shares") or fs.latest("diluted_shares")
    shares = sh.inr if sh else shares_fallback
    share_ids = [sh.id] if sh else []
    if price is None or not shares:
        reason = "no price" if price is None else "no share count"
        for k, l, u in (("market_cap", "Market cap", "INR"), ("pe", "P/E", "x"), ("pb", "P/B", "x"), ("ev_ebitda", "EV / EBITDA", "x"), ("earnings_yield", "Earnings yield", "pct")):
            out[k] = na(k, l, u, reason)
        return out
    mcap = price * shares
    out["market_cap"] = Metric("market_cap", "Market cap", mcap, "INR", formula="last close × shares outstanding", inputs=share_ids)
    ni, ni_ids, basis = ttm_net_income(fs)
    if ni is None:
        out["pe"] = na("pe", "P/E", "x", "no net income")
        out["earnings_yield"] = na("earnings_yield", "Earnings yield", "pct", "no net income")
    elif ni <= 0:
        out["pe"] = na("pe", "P/E", "x", f"loss-making ({basis}) — P/E not meaningful", inputs=ni_ids)
        out["earnings_yield"] = Metric("earnings_yield", "Earnings yield", ni / mcap * 100, "pct", formula=f"net income ({basis}) / market cap", inputs=share_ids + ni_ids)
    else:
        out["pe"] = Metric("pe", "P/E", mcap / ni, "x", formula=f"market cap / net income ({basis})", inputs=share_ids + ni_ids)
        out["earnings_yield"] = Metric("earnings_yield", "Earnings yield", ni / mcap * 100, "pct", formula=f"net income ({basis}) / market cap", inputs=share_ids + ni_ids)
    eq = fs.latest("equity")
    if eq is None or eq.inr <= 0:
        out["pb"] = na("pb", "P/B", "x", "no positive book equity", inputs=[eq.id] if eq else [])
    else:
        out["pb"] = Metric("pb", "P/B", mcap / eq.inr, "x", formula="market cap / latest equity", inputs=share_ids + [eq.id])
    if financial:
        out["ev_ebitda"] = na("ev_ebitda", "EV / EBITDA", "x", "not used for banks/NBFCs/insurers", status="not_applicable")
    else:
        e = fs.latest("ebitda")
        if e is None or e.inr <= 0:
            out["ev_ebitda"] = na("ev_ebitda", "EV / EBITDA", "x", "no positive EBITDA", inputs=[e.id] if e else [])
        else:
            debt, cash = fs.at("total_debt", e.period_end), fs.at("cash", e.period_end)
            ev = mcap + (debt.inr if debt else 0) - (cash.inr if cash else 0)
            ids = share_ids + [e.id] + ([debt.id] if debt else []) + ([cash.id] if cash else [])
            out["ev_ebitda"] = Metric("ev_ebitda", "EV / EBITDA", ev / e.inr, "x", formula="(market cap + debt − cash) / EBITDA", inputs=ids,
                                      reason=None if (debt and cash) else "debt or cash missing — EV understated/overstated")
    return out


# ── price context ────────────────────────────────────────────────────────────

def price_context(closes: list[tuple[str, float]], index_returns: dict | None) -> dict[str, Metric]:
    out: dict[str, Metric] = {}
    vals = [c for _, c in closes if c is not None and c > 0]
    if len(vals) < 30:
        for k, l in (("ret_1m", "1-month return"), ("ret_3m", "3-month return"), ("ret_1y", "1-year return"), ("below_high", "Below 52-week high"),
                     ("volatility", "Volatility (annualised)"), ("max_drawdown", "Max drawdown (1y)"), ("rel_1y", "1-year return vs Nifty 500")):
            out[k] = na(k, l, "pct", f"needs 30+ daily closes, have {len(vals)}")
        return out

    def ret(n, key, label):
        if len(vals) <= n:
            return na(key, label, "pct", f"needs {n + 1} closes")
        return Metric(key, label, (vals[-1] / vals[-1 - n] - 1) * 100, "pct", formula=f"close[t] / close[t-{n}] − 1")

    out["ret_1m"], out["ret_3m"], out["ret_1y"] = ret(21, "ret_1m", "1-month return"), ret(63, "ret_3m", "3-month return"), ret(252, "ret_1y", "1-year return")
    year = vals[-252:]
    out["below_high"] = Metric("below_high", "Below 52-week high", (1 - vals[-1] / max(year)) * 100, "pct", formula="1 − close / max(close, 252 sessions)")
    rets = [math.log(b / a) for a, b in zip(year[:-1], year[1:])]
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1))
    out["volatility"] = Metric("volatility", "Volatility (annualised)", sd * math.sqrt(252) * 100, "pct", formula="stdev(daily log returns, 1y) × √252")
    peak, mdd = year[0], 0.0
    for v in year:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    out["max_drawdown"] = Metric("max_drawdown", "Max drawdown (1y)", mdd * 100, "pct", formula="min(close / running max − 1), 252 sessions")
    if index_returns and index_returns.get("ret_1y") is not None and out["ret_1y"].value is not None:
        out["rel_1y"] = Metric("rel_1y", "1-year return vs Nifty 500", out["ret_1y"].value - index_returns["ret_1y"], "pp",
                               formula="stock 1y return − Nifty 500 1y return")
    else:
        out["rel_1y"] = na("rel_1y", "1-year return vs Nifty 500", "pp", "index returns unavailable")
    return out


def compute_all(fs: FactSet, financial: bool, price: float | None, shares_fallback: float | None,
                closes: list[tuple[str, float]], index_returns: dict | None) -> dict[str, Metric]:
    m: dict[str, Metric] = {}
    m["revenue_growth"] = yoy(fs, "revenue", "revenue_growth", "Revenue growth (YoY)")
    m["revenue_growth_prev"] = yoy(fs, "revenue", "revenue_growth_prev", "Revenue growth, prior year", back=1)
    m["revenue_acceleration"] = acceleration(m["revenue_growth"], m["revenue_growth_prev"], "revenue_acceleration", "Revenue acceleration")
    m["revenue_cagr"] = cagr(fs, "revenue", "revenue_cagr", "Revenue CAGR (3y)")
    m["profit_growth"] = yoy(fs, "net_income", "profit_growth", "Profit growth (YoY)")
    m["profit_growth_prev"] = yoy(fs, "net_income", "profit_growth_prev", "Profit growth, prior year", back=1)
    m["profit_acceleration"] = acceleration(m["profit_growth"], m["profit_growth_prev"], "profit_acceleration", "Profit acceleration")
    m["profit_cagr"] = cagr(fs, "net_income", "profit_cagr", "Profit CAGR (3y)")
    m["q_revenue_yoy"] = quarter_yoy(fs, "revenue", "q_revenue_yoy", "Latest quarter revenue YoY")
    m["q_profit_yoy"] = quarter_yoy(fs, "net_income", "q_profit_yoy", "Latest quarter profit YoY")
    m["q_acceleration"] = acceleration(m["q_revenue_yoy"], m["revenue_growth"], "q_acceleration", "Quarterly vs annual revenue growth")
    if financial:
        for k, l in (("ebit_margin", "EBIT margin"), ("ebit_margin_change", "EBIT margin change (2y)")):
            m[k] = na(k, l, "pct" if k == "ebit_margin" else "pp", "operating margin not comparable for lenders/insurers", status="not_applicable")
    else:
        m["ebit_margin"] = margin(fs, "ebit", "ebit_margin", "EBIT margin")
        m["ebit_margin_change"] = margin_change(fs, "ebit", "ebit_margin_change", "EBIT margin change (2y)")
    m["net_margin"] = margin(fs, "net_income", "net_margin", "Net margin")
    m["roe"] = roe(fs)
    m["roce"] = roce(fs, financial)
    m["roce_prev"] = roce(fs, financial, back=2, key="roce_prev", label="ROCE two years earlier")
    m["roce_change"] = change(m["roce"], m["roce_prev"], "roce_change", "ROCE change (2y)", "pp") if not financial else na("roce_change", "ROCE change (2y)", "pp", "not used for lenders", status="not_applicable")
    m["debt_equity"] = debt_equity(fs, financial)
    m["interest_cover"] = interest_cover(fs, financial)
    m["cfo_pat"] = sum_ratio(fs, "cfo", "net_income", "cfo_pat", "Cash conversion (CFO / PAT, 3y)", financial=financial, financial_na=True)
    m["fcf_pat"] = sum_ratio(fs, "fcf", "net_income", "fcf_pat", "Free cash flow / PAT (3y)", financial=financial, financial_na=True)
    m["receivable_days"] = days_metric(fs, "receivables", "receivable_days", "Receivable days")
    m["receivable_days_prev"] = days_metric(fs, "receivables", "receivable_days_prev", "Receivable days two years earlier", back=2)
    m["receivable_days_change"] = change(m["receivable_days"], m["receivable_days_prev"], "receivable_days_change", "Receivable days change (2y)", "days")
    m["receivables_growth"] = yoy(fs, "receivables", "receivables_growth", "Receivables growth (YoY)")
    m["inventory_days"] = days_metric(fs, "inventory", "inventory_days", "Inventory days")
    m["inventory_days_prev"] = days_metric(fs, "inventory", "inventory_days_prev", "Inventory days two years earlier", back=2)
    m["inventory_days_change"] = change(m["inventory_days"], m["inventory_days_prev"], "inventory_days_change", "Inventory days change (2y)", "days")
    m["other_income_share"] = other_income_share(fs)
    m["share_change"] = share_change(fs)
    m.update(valuation_ratios(fs, price, financial, shares_fallback))
    m.update(price_context(closes, index_returns))
    return m
