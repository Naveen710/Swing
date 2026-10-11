"""Price-proxy backtest of the multibagger framework's price half (Tailwind + Price layers).

Spec §Validation (a). The fundamental layers (≈70% of the score) cannot be backtested:
there is no free point-in-time history of company financials. So this module answers a
narrower question honestly: did stocks that ranked high on the *price* layers at each
month-end go on to beat the same-survivorship equal-weight universe over 1/2/3 years?

Rules: signal at the last session of each month; entry at the NEXT session's close (no
look-ahead); buy-and-hold per cohort for 252/504/756 sessions; top 20 with ≤ 3 per sector;
equal weight; 0.25% round-trip cost (+ a 1% sensitivity line). A member with a one-day
move > 40% inside its window (unadjusted split / bad print) is dropped and counted.
Pass/fail thresholds were fixed in the spec before any result was seen.
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from app.config import settings
from app.services.curated_algo import DATA_BREAK, Panel
from app.services.quant_screen import pick_top

logger = logging.getLogger(__name__)

HORIZONS = {"1y": 252, "2y": 504, "3y": 756}
TOP_N = 20
MIN_PRICE = 20.0
MIN_VALUE = 1e7            # ₹1 Cr median daily traded value
MIN_HISTORY = 252
HIGH_COST_PCT = 1.0
PRIMARY = "nifty_smallcap_250"
SURVIVORSHIP_WARNING = (
    "Survivorship bias: the universe is today's index members, which partly exist because they went up. "
    "Absolute returns and the 2× hit rate are inflated; compare against the equal-weight universe, which has the same bias."
)


def _lin_frame(x: pd.DataFrame, lo: float, hi: float) -> pd.DataFrame:
    return ((x - lo) / (hi - lo) * 100.0).clip(0, 100)


def month_end_positions(index: pd.DatetimeIndex, start: str, last_allowed: int) -> list[int]:
    idx = pd.DatetimeIndex(index)
    tz = idx.tz
    begin = pd.Timestamp(start, tz=tz) if tz is not None else pd.Timestamp(start)
    periods = idx.to_period("M") if tz is None else idx.tz_localize(None).to_period("M")
    out = []
    for i in range(len(idx) - 1):
        if periods[i] != periods[i + 1] and idx[i] >= begin and i <= last_allowed:
            out.append(i)
    return out


def price_scores(p: Panel, positions: list[int], excluded: set[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Price-half score (0–100) at each signal row, and the eligibility mask."""
    c, h, l, v = p.close, p.high, p.low, p.volume
    idx = p.index_close.astype("float64")

    def at(frame):
        return frame.iloc[positions]

    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    rising = (sma200 > sma200.shift(21)) & (sma200.shift(21) > sma200.shift(42)) & (sma200.shift(42) > sma200.shift(63))
    low52 = l.rolling(252, min_periods=200).min()
    high52 = h.rolling(252, min_periods=200).max()
    made_high = (h >= high52 * 0.999).astype("float32").rolling(126, min_periods=1).max() > 0
    checks = [(c >= 30), rising, (sma50 > sma200), (c > sma200), (c >= 2 * low52), made_high]
    stage2 = at(sum(x.astype("float32") for x in checks)) / 6 * 100
    del rising, low52, made_high, checks, sma50, sma200
    dist = ((1 - c / high52) * 100).clip(lower=0)
    near = at(_lin_frame(dist, 40, 10))
    rs = c.div(idx, axis=0)
    rs_high = rs.rolling(252, min_periods=60).max()
    new_high = rs.rolling(3).max() >= rs_high * 0.999
    dist_rs = (rs_high - rs) / rs_high * 100
    rs_line = at(_lin_frame(dist_rs, 20, 0).where(~new_high, 100.0))
    del rs, rs_high, new_high, dist_rs, dist
    l5 = (35 * stage2 + 25 * near + 20 * rs_line) / 80

    bench6 = idx / idx.shift(126) - 1
    bench12_1 = idx.shift(21) / idx.shift(252) - 1
    rs_raw = at(0.5 * (c / c.shift(126) - 1).sub(bench6, axis=0) + 0.5 * (c.shift(21) / c.shift(252) - 1).sub(bench12_1, axis=0))
    ex1 = at((c / c.shift(21) - 1).sub(idx / idx.shift(21) - 1, axis=0) * 100)
    ex3 = at((c / c.shift(63) - 1).sub(idx / idx.shift(63) - 1, axis=0) * 100)

    value = (c * v).rolling(60, min_periods=40).median()
    history = c.notna().cumsum()
    elig = at((c >= MIN_PRICE) & (value >= MIN_VALUE) & (history >= MIN_HISTORY))
    elig &= l5.notna() & rs_raw.notna()
    if excluded:
        elig.loc[:, [s for s in elig.columns if s in excluded]] = False

    sectors = pd.Series(p.sectors).reindex(c.columns).fillna("Unknown")
    scores = pd.DataFrame(np.nan, index=l5.index, columns=c.columns, dtype="float64")
    for d in l5.index:
        e = elig.loc[d]
        syms = e[e].index
        if len(syms) < 5:
            continue
        rsd = rs_raw.loc[d, syms]
        rs_pctl = rsd.rank(pct=True) * 100
        sec = sectors[syms]
        frame = pd.DataFrame({"sec": sec, "ex1": ex1.loc[d, syms], "ex3": ex3.loc[d, syms], "rs": rsd})
        good = frame[~frame["sec"].isin(["Unknown", "Benchmark"])]
        counts = good["sec"].value_counts()
        valid = counts[counts >= 3].index
        sect = good[good["sec"].isin(valid)].groupby("sec").agg(ex1=("ex1", "median"), ex3=("ex3", "median"))
        sect_score = (0.4 * sect["ex1"] + 0.6 * sect["ex3"]).rank(ascending=False)
        n = len(sect_score)
        sect_pts = (100 * (1 - (sect_score - 1) / (n - 1))) if n > 1 else sect_score * 0 + 100
        sector_metric = sec.map(sect_pts).fillna(50.0)
        # Same rule as the live screen: only sectors with ≥ 3 known members are ranked;
        # everything else (Unknown, or 1–2 members) is neutral 50 for both sector and peer rank.
        peer = frame.groupby("sec")["rs"].rank(ascending=False)
        peer_n = frame.groupby("sec")["rs"].transform("count")
        peer_metric = 100 * (1 - (peer - 1) / (peer_n - 1))
        peer_metric[~sec.isin(valid)] = 50.0
        l4 = (40 * sector_metric + 40 * rs_pctl + 20 * peer_metric) / 100
        scores.loc[d, syms] = ((l4 + l5.loc[d, syms]) / 2).values
    return scores, elig


def _forward(close: np.ndarray, brk_cum: np.ndarray, entry: int, exit_: int, cols: np.ndarray) -> tuple[np.ndarray, int]:
    """Returns for the given column indices, NaN where unavailable; and count dropped for data breaks."""
    c0, c1 = close[entry, cols], close[exit_, cols]
    r = c1 / c0 - 1
    broken = (brk_cum[exit_, cols] - brk_cum[entry, cols]) > 0
    dropped = int(np.sum(broken & ~np.isnan(r)))
    r = np.where(broken, np.nan, r)
    return r, dropped


def _spearman(a: pd.Series, b: pd.Series) -> float | None:
    m = a.notna() & b.notna()
    if m.sum() < 10:
        return None
    return float(a[m].rank().corr(b[m].rank()))


def run_backtest(p: Panel, universe: str, start: str = "2015-01-01", excluded: set[str] | None = None,
                 hindsight: tuple[str, ...] = (), cost_pct: float | None = None, progress=None) -> dict:
    cost = (settings.trade_round_trip_cost_pct if cost_pct is None else cost_pct) / 100
    high_cost = HIGH_COST_PCT / 100
    n_rows = len(p.close)
    positions = month_end_positions(p.close.index, start, n_rows - 1 - HORIZONS["1y"] - 1)
    if not positions:
        raise RuntimeError("Not enough history for a 12-month forward test.")
    scores, elig = price_scores(p, positions, excluded)
    if progress:
        progress(70)
    close = p.close.to_numpy(dtype="float64")
    brk = (p.close.pct_change().abs() > DATA_BREAK).to_numpy()
    brk_cum = np.cumsum(np.nan_to_num(brk.astype("float64")), axis=0)
    idx = p.index_close.to_numpy(dtype="float64")
    col_pos = {s: i for i, s in enumerate(p.close.columns)}
    sectors = dict(p.sectors)

    cohorts = []
    ics, quint_rets = [], []
    dropped_total = 0
    hits = {"cohort_2x": 0, "cohort_n": 0, "univ_2x": 0, "univ_n": 0}
    member_months: dict[str, list[tuple[str, int]]] = {}
    for pos, d in zip(positions, scores.index):
        row = scores.loc[d].dropna()
        if len(row) < TOP_N:
            continue
        picks = pick_top(row, sectors, TOP_N)
        entry = pos + 1
        for s in picks:
            member_months.setdefault(s, []).append((str(pd.Timestamp(d).date())[:7], entry))
        rec = {"date": str(pd.Timestamp(d).date()), "entry_date": str(pd.Timestamp(p.close.index[entry]).date()),
               "members": picks, "eligible": int(len(row))}
        univ_cols = np.array([col_pos[s] for s in row.index])
        pick_cols = np.array([col_pos[s] for s in picks])
        for name, h in HORIZONS.items():
            exit_ = entry + h
            if exit_ >= n_rows:
                rec[name] = None
                continue
            pr, dp = _forward(close, brk_cum, entry, exit_, pick_cols)
            ur, _ = _forward(close, brk_cum, entry, exit_, univ_cols)
            dropped_total += dp if name == "2y" else 0
            if np.all(np.isnan(pr)):
                rec[name] = None
                continue
            gross = float(np.nanmean(pr))
            univ = float(np.nanmean(ur))
            nifty = float(idx[exit_] / idx[entry] - 1) if idx[entry] and not math.isnan(idx[exit_]) else None
            rec[name] = {
                "cohort": round((gross - cost) * 100, 2), "cohort_high_cost": round((gross - high_cost) * 100, 2),
                "universe": round(univ * 100, 2), "nifty": None if nifty is None else round(nifty * 100, 2),
                "excess": round((gross - cost - univ) * 100, 2), "excess_high_cost": round((gross - high_cost - univ) * 100, 2),
                "members_used": int(np.sum(~np.isnan(pr))),
            }
            if name == "1y":
                fwd = pd.Series(ur, index=row.index)
                ic = _spearman(row, fwd)
                if ic is not None:
                    ics.append(ic)
                q = pd.qcut(row.rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
                quint_rets.append(fwd.groupby(q, observed=False).mean() * 100)
            if name == "3y":
                hits["cohort_2x"] += int(np.sum(pr >= 1.0))
                hits["cohort_n"] += int(np.sum(~np.isnan(pr)))
                hits["univ_2x"] += int(np.sum(ur >= 1.0))
                hits["univ_n"] += int(np.sum(~np.isnan(ur)))
        cohorts.append(rec)
    if progress:
        progress(90)

    horizons = {}
    for name in HORIZONS:
        done = [c[name] for c in cohorts if c.get(name)]
        if not done:
            horizons[name] = {"cohorts": 0}
            continue
        ex = np.array([x["excess"] for x in done])
        horizons[name] = {
            "cohorts": len(done),
            "mean_return": round(float(np.mean([x["cohort"] for x in done])), 2),
            "median_return": round(float(np.median([x["cohort"] for x in done])), 2),
            "median_universe": round(float(np.median([x["universe"] for x in done])), 2),
            "median_excess": round(float(np.median(ex)), 2),
            "median_excess_high_cost": round(float(np.median([x["excess_high_cost"] for x in done])), 2),
            "beat_universe_pct": round(float(np.mean(ex > 0) * 100), 1),
            "median_excess_vs_nifty": round(float(np.median([x["cohort"] - x["nifty"] for x in done if x["nifty"] is not None])), 2)
            if any(x["nifty"] is not None for x in done) else None,
        }

    def window_excess(year: int, months: range) -> float | None:
        vals = [c["2y"]["excess"] for c in cohorts if c.get("2y") and int(c["date"][:4]) == year and int(c["date"][5:7]) in months]
        return round(float(np.mean(vals)), 2) if vals else None

    jan = [c["2y"]["excess"] for c in cohorts if c.get("2y") and c["date"][5:7] == "01"]
    t_stat = None
    if len(jan) >= 3 and np.std(jan, ddof=1) > 0:
        t_stat = round(float(np.mean(jan) / (np.std(jan, ddof=1) / math.sqrt(len(jan)))), 2)
    hit_ratio = None
    cohort_rate = hits["cohort_2x"] / hits["cohort_n"] if hits["cohort_n"] else None
    univ_rate = hits["univ_2x"] / hits["univ_n"] if hits["univ_n"] else None
    if cohort_rate is not None and univ_rate:
        hit_ratio = round(cohort_rate / univ_rate, 2)
    ic_mean = round(float(np.mean(ics)), 4) if ics else None
    ic_pos = round(float(np.mean(np.array(ics) > 0) * 100), 1) if ics else None
    quintiles = None
    if quint_rets:
        qm = pd.concat(quint_rets, axis=1).mean(axis=1)
        quintiles = [{"quintile": int(k), "mean_12m_return": round(float(v), 2)} for k, v in qm.items()]

    two = horizons["2y"]
    primary = universe == PRIMARY
    crisis = window_excess(2018, range(1, 7))
    criteria = [
        {"name": "Median 24-month excess vs equal-weight universe (pp)", "threshold": "≥ +8" if primary else "> 0",
         "value": two.get("median_excess"),
         "pass": two.get("median_excess") is not None and two["median_excess"] >= (8 if primary else 0.0001)},
        {"name": "Cohorts beating the universe at 24 months (%)", "threshold": "≥ 60" if primary else "> 50",
         "value": two.get("beat_universe_pct"),
         "pass": two.get("beat_universe_pct") is not None and two["beat_universe_pct"] >= (60 if primary else 50.0001)},
    ]
    if primary:
        criteria += [
            {"name": "Mean monthly rank IC vs 12-month return (IC > 0 in % of months)", "threshold": "≥ 0.03 and ≥ 55%",
             "value": None if ic_mean is None else f"{ic_mean:.3f} ({ic_pos:.0f}%)",
             "pass": ic_mean is not None and ic_mean >= 0.03 and ic_pos >= 55},
            {"name": "2× in 3 years hit rate vs universe base rate", "threshold": "≥ 1.5×",
             "value": hit_ratio, "pass": hit_ratio is not None and hit_ratio >= 1.5},
            {"name": "2018-H1 cohorts' mean 24-month excess (pp)", "threshold": "≥ −15",
             "value": crisis, "pass": crisis is not None and crisis >= -15},
        ]
    if two.get("cohorts", 0) < 60:
        verdict = "INCONCLUSIVE"
    else:
        verdict = "PASS" if all(c["pass"] for c in criteria) else "FAIL"

    hind = hindsight_check(p, scores, member_months, hindsight)
    return {
        "universe": universe, "start": start, "verdict": verdict, "criteria": criteria,
        "role": "primary" if primary else "secondary",
        "survivorship_warning": SURVIVORSHIP_WARNING,
        "horizons": horizons, "cohorts_total": len(cohorts),
        "ic": {"mean": ic_mean, "positive_pct": ic_pos, "months": len(ics)},
        "two_x": {"cohort_rate_pct": None if cohort_rate is None else round(cohort_rate * 100, 1),
                  "universe_rate_pct": None if univ_rate is None else round(univ_rate * 100, 1), "ratio": hit_ratio},
        "quintiles": quintiles,
        "stress": {"2018_h1_excess_24m": crisis, "2020_h1_excess_24m": window_excess(2020, range(1, 7))},
        "t_stat_jan_cohorts": t_stat, "jan_cohorts": len(jan),
        "data_breaks_dropped": dropped_total,
        "costs": {"round_trip_pct": cost * 100, "sensitivity_pct": HIGH_COST_PCT},
        "symbols": int(p.close.shape[1]), "index_name": p.index_name,
        "hindsight": hind,
        "recent_cohorts": [{k: c[k] for k in ("date", "entry_date", "members", "eligible")} for c in cohorts[-3:]],
        "cohorts": [{k: c.get(k) for k in ("date", "entry_date", "eligible", "1y", "2y", "3y")} for c in cohorts],
        "notes": [
            "Tests only the price half (Tailwind + Price layers, VCP weight spread over the other price metrics). The fundamental layers are not tested.",
            "Entry is the close of the session after each month-end signal. Returns are price-only (no dividends).",
            "At most 3 names per sector per cohort; 'Unknown' counts as one sector (matters for Mid & Small 2000+, where most sectors are unknown).",
            f"t-stat of 24-month excess on non-overlapping January cohorts: {t_stat if t_stat is not None else 'n/a'} (n = {len(jan)}). Reported, not a pass criterion.",
        ],
    }


def hindsight_check(p: Panel, scores: pd.DataFrame, member_months: dict[str, list[tuple[str, int]]], symbols: tuple[str, ...]) -> dict:
    rows = []
    c = p.close
    for sym in symbols:
        if sym not in c.columns:
            rows.append({"symbol": sym, "in_universe": False})
            continue
        s = c[sym].dropna()
        months = member_months.get(sym, [])
        best_start, best_mult = None, None
        if len(s) > 756:
            fwd = (s.shift(-756) / s).dropna()
            best_start, best_mult = fwd.idxmax(), float(fwd.max())
        prior = None
        if best_start is not None:
            window = scores[sym][(scores.index < best_start) & (scores.index >= best_start - pd.Timedelta(days=365))].dropna()
            prior = round(float(window.mean()), 1) if len(window) else None
        crash = None
        if months:
            entry_pos = months[0][1]                       # the actual entry bar (session after the signal)
            after = c[sym].iloc[entry_pos: entry_pos + 757].dropna()
            if len(after) > 20:
                crash = bool(after.min() / after.iloc[0] < 0.5)   # fell > 50% below the entry price
        rows.append({
            "symbol": sym, "in_universe": True, "first_top20_month": months[0][0] if months else None,
            "months_in_top20": len(months),
            "best_3y_run_start": None if best_start is None else str(pd.Timestamp(best_start).date()),
            "best_3y_multiple": None if best_mult is None else round(best_mult, 2),
            "avg_price_score_12m_before_run": prior, "drawdown_over_50pct_after_entry": crash,
        })
    return {"label": "Hindsight check — these names were chosen because we know what happened. Informational only, not evidence.",
            "rows": rows}
