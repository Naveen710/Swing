"""Quant multi-factor screen.

Inspired by the publicly known *principles* of systematic funds — combine many weak,
well-documented signals into one statistical score, rank the whole universe
cross-sectionally, diversify, and validate everything out of sample. It does not and
cannot replicate any specific fund's proprietary strategy.

Factors (each z-scored across the universe every day, winsorised at ±3):
  momentum_12_1   12-month return skipping the latest month        (Jegadeesh & Titman)
  high_52w        price / 52-week high                              (George & Hwang)
  trend_quality   net 60-day move / total path travelled (efficiency)
  low_volatility  minus 60-day volatility                           (low-volatility anomaly)
  reversal_5d     minus last week's return                          (short-term reversal)

The validation re-runs the exact same code over history: every 5 sessions it buys the
top-N names (equal weight, max 3 per sector) one session after the signal, holds 5
sessions, pays costs on turnover, and compares with the equal-weight universe and Nifty.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.config import settings
from app.schemas import ScanUniverse
from app.services.universe import load_universe

logger = logging.getLogger(__name__)

FACTOR_WEIGHTS = {
    "momentum_12_1": 0.30,
    "high_52w": 0.20,
    "trend_quality": 0.20,
    "low_volatility": 0.15,
    "reversal_5d": 0.15,
}
FACTOR_LABELS = {
    "momentum_12_1": "12-1 momentum",
    "high_52w": "Near 52-week high",
    "trend_quality": "Trend efficiency",
    "low_volatility": "Low volatility",
    "reversal_5d": "Short-term reversal",
}
MAX_PER_SECTOR = 3
REBALANCE_EVERY = 5
MIN_TRADED_VALUE_CR = 2.0
MIN_PRICE = 20.0
LOOKBACK_DAYS = 760          # ~252 warm-up + ~2 years of validation
CACHE_SECONDS = 1800


# ── data ─────────────────────────────────────────────────────────────────────

def load_panel(scanner, universe: ScanUniverse, lookback: int = LOOKBACK_DAYS):
    listings = load_universe(universe=universe)
    try:
        scanner.market_data.prefetch_histories(listings, lookback)
    except Exception:  # noqa: BLE001
        pass

    def fetch(listing):
        try:
            frame = scanner.market_data.get_history(listing, lookback_days=lookback)
            return listing, frame
        except Exception:  # noqa: BLE001
            return listing, None

    closes, values, meta = {}, {}, {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for listing, frame in pool.map(fetch, listings):
            if frame is None or len(frame) < 260:
                continue
            closes[listing.symbol] = frame["Close"]
            values[listing.symbol] = frame["Close"] * frame["Volume"]
            meta[listing.symbol] = (listing.company_name, listing.sector)
    close_df = pd.DataFrame(closes).sort_index()
    value_df = pd.DataFrame(values).reindex(close_df.index)
    # Only keep dates most stocks traded (drops stray holiday bars from single feeds).
    keep = close_df.notna().mean(axis=1) >= 0.6
    return close_df[keep].ffill(limit=3), value_df[keep], meta


# ── factors ──────────────────────────────────────────────────────────────────

def compute_factors(close: pd.DataFrame) -> dict[str, pd.DataFrame]:
    rets = close.pct_change(fill_method=None)
    path = close.diff().abs().rolling(60).sum()
    return {
        "momentum_12_1": close.shift(21) / close.shift(252) - 1,
        "high_52w": close / close.rolling(252, min_periods=200).max(),
        "trend_quality": (close - close.shift(60)) / path.replace(0, np.nan),
        "low_volatility": -rets.rolling(60).std() * np.sqrt(252),
        "reversal_5d": -(close / close.shift(5) - 1),
    }


def zscore_rows(frame: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    data = frame.where(mask)
    mean = data.mean(axis=1)
    std = data.std(axis=1).replace(0, np.nan)
    return data.sub(mean, axis=0).div(std, axis=0).clip(-3, 3)


def composite_scores(close: pd.DataFrame, traded_value: pd.DataFrame):
    raw = compute_factors(close)
    liquid = traded_value.rolling(20, min_periods=15).median() >= MIN_TRADED_VALUE_CR * 1e7
    mask = liquid & (close >= MIN_PRICE) & raw["momentum_12_1"].notna()
    z = {name: zscore_rows(frame, mask) for name, frame in raw.items()}
    total = sum(z[name].fillna(0) * w for name, w in FACTOR_WEIGHTS.items())
    composite = total.where(mask)
    return composite, z, raw, mask


def pick_top(scores: pd.Series, sectors: dict[str, str], top_n: int) -> list[str]:
    picks: list[str] = []
    per_sector: dict[str, int] = {}
    for symbol in scores.dropna().sort_values(ascending=False).index:
        sector = sectors.get(symbol, "Unknown")
        if per_sector.get(sector, 0) >= MAX_PER_SECTOR:
            continue
        picks.append(symbol)
        per_sector[sector] = per_sector.get(sector, 0) + 1
        if len(picks) >= top_n:
            break
    return picks


# ── validation ───────────────────────────────────────────────────────────────

def validate(close, composite, sectors, top_n, benchmark: pd.Series | None) -> dict:
    cost = settings.trade_round_trip_cost_pct / 100
    dates = composite.index
    start = composite.dropna(how="all").index
    if len(start) == 0:
        return {"available": False, "reason": "Not enough history to validate."}
    first = dates.get_loc(start[0])
    strategy, universe_avg, bench_rets, ics, beats = [], [], [], [], []
    curve_dates = []
    held: set[str] = set()

    i = first
    while i + 1 + REBALANCE_EVERY < len(dates):
        signal = composite.iloc[i]
        entry, exit_ = dates[i + 1], dates[i + 1 + REBALANCE_EVERY]
        fwd = close.loc[exit_] / close.loc[entry] - 1
        valid = signal.dropna().index.intersection(fwd.dropna().index)
        if len(valid) < max(30, top_n * 2):
            i += REBALANCE_EVERY
            continue
        picks = [s for s in pick_top(signal[valid], sectors, top_n)]
        turnover = 1.0 if not held else len(set(picks) - held) / max(len(picks), 1)
        held = set(picks)
        gross = float(fwd[picks].mean())
        strategy.append(gross - turnover * cost)
        universe_avg.append(float(fwd[valid].mean()))
        beats.append(gross > universe_avg[-1])
        ics.append(float(signal[valid].rank().corr(fwd[valid].rank())))
        if benchmark is not None and entry in benchmark.index and exit_ in benchmark.index:
            bench_rets.append(float(benchmark[exit_] / benchmark[entry] - 1))
        else:
            bench_rets.append(np.nan)
        curve_dates.append(exit_)
        i += REBALANCE_EVERY

    if len(strategy) < 10:
        return {"available": False, "reason": "Fewer than 10 rebalances of history — not enough to judge."}

    periods_per_year = 252 / REBALANCE_EVERY

    def stats(series: list[float]) -> dict:
        r = pd.Series(series).dropna()
        eq = (1 + r).cumprod()
        years = len(r) / periods_per_year
        cagr = (eq.iloc[-1] ** (1 / years) - 1) * 100 if years > 0 and eq.iloc[-1] > 0 else None
        vol = r.std() * np.sqrt(periods_per_year) * 100
        dd = ((eq / eq.cummax()) - 1).min() * 100
        return {
            "total_return_pct": round(float((eq.iloc[-1] - 1) * 100), 2),
            "cagr_pct": round(float(cagr), 2) if cagr is not None else None,
            "volatility_pct": round(float(vol), 2),
            "sharpe": round(float(r.mean() / r.std() * np.sqrt(periods_per_year)), 2) if r.std() > 0 else None,
            "max_drawdown_pct": round(float(dd), 2),
        }

    ic = pd.Series(ics).dropna()
    ic_mean = float(ic.mean())
    ic_t = float(ic_mean / (ic.std() / np.sqrt(len(ic)))) if ic.std() > 0 else 0.0
    excess = pd.Series(strategy) - pd.Series(universe_avg)
    excess_t = float(excess.mean() / (excess.std() / np.sqrt(len(excess)))) if excess.std() > 0 else 0.0

    def curve(series):
        return list((1 + pd.Series(series).fillna(0)).cumprod() * 100)

    s_curve, u_curve, b_curve = curve(strategy), curve(universe_avg), curve(bench_rets)
    step = max(1, len(curve_dates) // 120)
    return {
        "available": True,
        "start": curve_dates[0].date().isoformat(),
        "end": curve_dates[-1].date().isoformat(),
        "rebalances": len(strategy),
        "top_n": top_n,
        "strategy": stats(strategy),
        "universe": stats(universe_avg),
        "benchmark": stats(bench_rets) if not all(np.isnan(bench_rets)) else None,
        "hit_rate": round(float(np.mean(beats)), 3),
        "ic_mean": round(ic_mean, 4),
        "ic_t_stat": round(ic_t, 2),
        "excess_t_stat": round(excess_t, 2),
        "significant": bool(excess_t >= 2.0 and ic_t >= 2.0),
        "curve": [
            {"date": curve_dates[k].date().isoformat(), "strategy": round(s_curve[k], 2),
             "universe": round(u_curve[k], 2), "benchmark": round(b_curve[k], 2)}
            for k in range(0, len(curve_dates), step)
        ],
        "cost_pct_round_trip": settings.trade_round_trip_cost_pct,
    }


# ── service ──────────────────────────────────────────────────────────────────

class QuantScreenService:
    def __init__(self, scanner) -> None:
        self.scanner = scanner
        self._cache: dict[tuple[str, int], tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def run(self, universe: ScanUniverse, top_n: int = 20, refresh: bool = False) -> dict:
        key = (universe.value, top_n)
        with self._lock:
            hit = self._cache.get(key)
            if hit and not refresh and time.time() - hit[0] < CACHE_SECONDS:
                return hit[1]
        result = self._compute(universe, top_n)
        with self._lock:
            self._cache[key] = (time.time(), result)
        return result

    def _compute(self, universe: ScanUniverse, top_n: int) -> dict:
        close, value, meta = load_panel(self.scanner, universe)
        if close.empty or close.shape[1] < 30:
            return {"available": False, "reason": "Not enough price history loaded for this universe.", "picks": []}
        sectors = {s: m[1] for s, m in meta.items()}
        composite, z, raw, mask = composite_scores(close, value)

        last = composite.index[-1]
        today = composite.loc[last].dropna()
        picks = pick_top(today, sectors, top_n)
        percentile = today.rank(pct=True)
        vol = -raw["low_volatility"].loc[last]
        inv_vol = (1 / vol[picks]).replace([np.inf, -np.inf], np.nan).fillna(0)
        weights = (inv_vol / inv_vol.sum()) if inv_vol.sum() > 0 else pd.Series(1 / len(picks), index=picks)

        rows = []
        for rank, symbol in enumerate(picks, start=1):
            name, sector = meta[symbol]
            rows.append({
                "rank": rank, "symbol": symbol, "company_name": name, "sector": sector,
                "price": round(float(close.loc[last, symbol]), 2),
                "score": round(float(today[symbol]), 3),
                "percentile": round(float(percentile[symbol]) * 100, 1),
                "weight_pct": round(float(weights[symbol]) * 100, 2),
                "volatility_pct": round(float(vol[symbol]) * 100, 1),
                "factors": {
                    f: {"z": round(float(z[f].loc[last, symbol]), 2) if pd.notna(z[f].loc[last, symbol]) else None,
                        "raw": round(float(raw[f].loc[last, symbol]), 4) if pd.notna(raw[f].loc[last, symbol]) else None}
                    for f in FACTOR_WEIGHTS
                },
            })

        bench = None
        try:
            ctx = self.scanner._load_benchmark_context(LOOKBACK_DAYS)
            if ctx is not None:
                bench = ctx.benchmark_frame["Close"].reindex(close.index).ffill()
        except Exception:  # noqa: BLE001
            pass

        return {
            "available": True,
            "universe": universe.value,
            "as_of": last.date().isoformat(),
            "eligible": int(mask.loc[last].sum()),
            "loaded": int(close.shape[1]),
            "top_n": top_n,
            "factor_weights": FACTOR_WEIGHTS,
            "factor_labels": FACTOR_LABELS,
            "rules": [
                f"Liquidity: 20-day median traded value ≥ ₹{MIN_TRADED_VALUE_CR:g} Cr and price ≥ ₹{MIN_PRICE:g}",
                f"Max {MAX_PER_SECTOR} stocks per sector; weights ∝ 1/volatility (riskier names get less money)",
                f"Rebalance every {REBALANCE_EVERY} sessions; validation enters one session after the signal",
            ],
            "picks": rows,
            "validation": validate(close, composite, sectors, top_n, bench),
            "generated_at": datetime.now(UTC).isoformat(),
        }
