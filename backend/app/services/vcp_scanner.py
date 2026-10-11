"""Trend template + Volatility Contraction Pattern (VCP) scanner.

Elimination (all must pass):
  price ≥ ₹30 · 200-day MA rising for ≥ 3 months · 50 MA > 200 MA · price > 200 MA
  (preferably > 50 MA, shown as a flag) · price ≥ 2× the 52-week low (+100%) ·
  a new 52-week high within the last 6 months.

VCP (weekly chart, completed weeks only — "make the list after the weekly close"):
  Base starts at the highest weekly high of the last 30 weeks. A zig-zag (3% reversals)
  splits it into swing high → swing low legs. Contraction depth = swing low / swing high − 1.
  Valid VCP: ≥ 2 contractions, each shallower than the one before; with only 2, the second
  must be ≥ 70% smaller than the first. Ideal: 3+, each ~50% smaller, volume drying up, more
  volume on up weeks than down weeks, breakout within 4–8 weeks of the base starting.
  Pivot = the last swing high. Buy when the pivot is taken out.

Stop: entry − 2 × ATR(20, daily), never more than 10% below entry.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.schemas import MarketCapBucket, ScanUniverse
from app.services.universe import StockListing, find_listing

logger = logging.getLogger(__name__)

MIN_PRICE = 30.0
ZIGZAG = 0.03
BASE_LOOKBACK_WEEKS = 30
IDEAL_BASE_WEEKS = (4, 8)
NEAR_PIVOT = 0.05
EXTENDED = 0.05           # > 5% above pivot = extended, don't chase
MAX_STOP = 0.10
ATR_MULT = 2.0


# ── indicators ───────────────────────────────────────────────────────────────

def atr(df: pd.DataFrame, period: int = 20) -> pd.Series:
    prev = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def vcp_stop(entry: float, atr20: float) -> float:
    """2×ATR(20) below entry, capped at a 10% loss."""
    return round(max(entry - ATR_MULT * atr20, entry * (1 - MAX_STOP)), 2)


def trend_template(df: pd.DataFrame) -> dict:
    c = df["Close"]
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    price = float(c.iloc[-1])
    s200 = sma200.iloc[-1]
    # rising for 3 months: each month's 200 MA above the month before
    months = [sma200.iloc[-1 - 21 * k] if len(sma200) > 21 * k else np.nan for k in range(4)]
    rising = all(pd.notna(m) for m in months) and months[0] > months[1] > months[2] > months[3]
    low52 = float(df["Low"].tail(252).min())
    high52 = df["High"].rolling(252, min_periods=200).max()
    made_high = bool((df["High"].tail(126) >= high52.tail(126) * 0.999).any()) if high52.notna().any() else False
    checks = {
        "price_above_30": price >= MIN_PRICE,
        "ma200_rising_3m": bool(rising),
        "ma50_above_ma200": bool(pd.notna(s200) and sma50.iloc[-1] > s200),
        "price_above_ma200": bool(pd.notna(s200) and price > s200),
        "doubled_from_52w_low": low52 > 0 and price >= 2 * low52,
        "new_52w_high_6m": made_high,
    }
    return {
        "pass": all(checks.values()),
        "checks": checks,
        "above_ma50": bool(price > sma50.iloc[-1]) if pd.notna(sma50.iloc[-1]) else False,
        "pct_above_52w_low": round((price / low52 - 1) * 100, 1) if low52 > 0 else None,
        "pct_below_52w_high": round((1 - price / float(high52.iloc[-1])) * 100, 1) if pd.notna(high52.iloc[-1]) else None,
    }


# ── weekly structure ─────────────────────────────────────────────────────────

def weekly_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Completed weeks only: the current week is dropped unless the last bar is a Friday."""
    w = df.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
    if len(w) and df.index[-1].weekday() != 4:
        w = w.iloc[:-1]
    return w


def zigzag_from(highs: np.ndarray, lows: np.ndarray, start: int, threshold: float = ZIGZAG) -> list[tuple[int, float, str]]:
    """Alternating swing points beginning with the swing high at `start`.
    The final leg is included even if not yet confirmed (a contraction in progress)."""
    swings: list[tuple[int, float, str]] = [(start, float(highs[start]), "H")]
    state = "H"
    hi_idx, hi = start, float(highs[start])
    lo_idx, lo = start, float(lows[start])
    for i in range(start + 1, len(highs)):
        h, l = float(highs[i]), float(lows[i])
        if state == "H":                                  # falling from a swing high
            if h > hi:                                    # higher high before a real pullback → move the high
                if lo >= hi * (1 - threshold) or len(swings) == 1:
                    swings[-1] = (i, h, "H")
                    hi_idx, hi, lo_idx, lo = i, h, i, l
                    continue
            if l < lo:
                lo_idx, lo = i, l
            if lo <= hi * (1 - threshold) and h >= lo * (1 + threshold) and i > lo_idx:
                swings.append((lo_idx, lo, "L"))
                state = "L"
                hi_idx, hi = i, h
        else:                                             # rising from a swing low
            if l < swings[-1][1]:                         # undercut: the contraction is deeper than thought
                swings[-1] = (i, l, "L")
                hi_idx, hi = i, h
                continue
            if h > hi:
                hi_idx, hi = i, h
            if l <= hi * (1 - threshold) and i > hi_idx:
                swings.append((hi_idx, hi, "H"))
                state = "H"
                lo_idx, lo = i, l
    if state == "H" and lo <= hi * (1 - threshold) and swings[-1][2] == "H" and lo_idx > swings[-1][0]:
        swings.append((lo_idx, lo, "L"))                  # contraction in progress
    return swings


def detect_vcp(weekly: pd.DataFrame) -> dict | None:
    if len(weekly) < 12:
        return None
    window = weekly.tail(BASE_LOOKBACK_WEEKS)
    highs, lows = window["High"].to_numpy(float), window["Low"].to_numpy(float)
    closes, vols = window["Close"].to_numpy(float), window["Volume"].to_numpy(float)
    start = int(np.argmax(highs))
    if start >= len(highs) - 2:
        return None                                       # at new highs — no base yet
    swings = zigzag_from(highs, lows, start)
    legs = [(swings[k], swings[k + 1]) for k in range(len(swings) - 1) if swings[k][2] == "H" and swings[k + 1][2] == "L"]
    if not legs:
        return None
    depths = [round((lo[1] / hi[1] - 1) * 100, 2) for hi, lo in legs]       # negative %, as in your notes
    mags = [-d for d in depths]
    durations = [lo[0] - hi[0] + 1 for hi, lo in legs]
    shrinking = all(mags[k + 1] < mags[k] for k in range(len(mags) - 1))
    two_ok = len(mags) != 2 or mags[1] <= 0.30 * mags[0]
    valid = len(mags) >= 2 and shrinking and two_ok
    halving = len(mags) >= 2 and all(mags[k + 1] <= 0.6 * mags[k] for k in range(len(mags) - 1))
    pivot_idx, pivot = legs[-1][0][0], legs[-1][0][1]

    # volume: advances on good volume, pullbacks on lower volume
    base = slice(start, len(window))
    wk_ret = np.diff(closes, prepend=closes[0])
    up_vol = vols[base][wk_ret[base] > 0].sum()
    down_vol = vols[base][wk_ret[base] < 0].sum()
    first_leg = vols[legs[0][0][0]:legs[0][1][0] + 1].mean()
    last_leg = vols[legs[-1][0][0]:legs[-1][1][0] + 1].mean()
    vol_dry = bool(last_leg < first_leg)
    base_weeks = len(window) - start
    return {
        "valid": bool(valid),
        "contractions": len(mags),
        "depths_pct": depths,
        "durations_weeks": durations,
        "shrinking": bool(shrinking),
        "halving": bool(halving),
        "two_contraction_rule_ok": bool(two_ok),
        "pivot": round(float(pivot), 2),
        "pivot_week": window.index[pivot_idx].date().isoformat(),
        "base_high": round(float(highs[start]), 2),
        "base_start_week": window.index[start].date().isoformat(),
        "base_weeks": int(base_weeks),
        "up_down_volume": round(float(up_vol / down_vol), 2) if down_vol > 0 else None,
        "volume_dry_up": vol_dry,
        "time_contracting": bool(all(durations[k + 1] <= durations[k] for k in range(len(durations) - 1))),
        "week_ending": window.index[-1].date().isoformat(),
    }


# ── per-stock scan ───────────────────────────────────────────────────────────

def scan_stock(df: pd.DataFrame) -> dict | None:
    df = df.dropna(subset=["Close"])
    if len(df) < 260:
        return None
    tt = trend_template(df)
    result = {"trend_template": tt, "vcp": None, "status": "fails trend template"}
    if not tt["pass"]:
        return result
    vcp = detect_vcp(weekly_bars(df))
    result["vcp"] = vcp
    if vcp is None or not vcp["valid"]:
        result["status"] = "no valid VCP"
        return result

    close = float(df["Close"].iloc[-1])
    prev_close = float(df["Close"].iloc[-2])
    pivot = vcp["pivot"]
    a20 = float(atr(df, 20).iloc[-1])
    vol50 = float(df["Volume"].tail(51).iloc[:-1].mean())
    vol_today = float(df["Volume"].iloc[-1])
    recent = df.tail(10)
    tight_pct = (recent["High"].max() / recent["Low"].min() - 1) * 100
    atr10, atr50 = float(atr(df, 10).iloc[-1]), float(atr(df, 50).iloc[-1])
    distance = (close / pivot - 1) * 100

    broke_recently = bool((df["Close"].tail(3) > pivot).any()) and close > pivot
    if close > pivot * (1 + EXTENDED):
        status = "extended"
    elif broke_recently:
        status = "breakout today" if prev_close <= pivot < close else "breakout"
    elif close >= pivot * (1 - NEAR_PIVOT):
        status = "near pivot"
    else:
        status = "building"

    entry = round(max(pivot * 1.001, close) if status.startswith("breakout") else pivot * 1.001, 2)
    stop = vcp_stop(entry, a20)
    ideal_weeks = IDEAL_BASE_WEEKS[0] <= vcp["base_weeks"] <= IDEAL_BASE_WEEKS[1]
    quality = {
        "three_plus_contractions": vcp["contractions"] >= 3,
        "each_about_half": vcp["halving"],
        "last_contraction_tight": abs(vcp["depths_pct"][-1]) <= 10,
        "volume_dry_up": vcp["volume_dry_up"],
        "more_up_volume": (vcp["up_down_volume"] or 0) >= 1.0,
        "base_4_to_8_weeks": ideal_weeks,
        "daily_tightening": atr10 < atr50,
    }
    result.update({
        "status": status,
        "close": round(close, 2),
        "pivot": pivot,
        "distance_to_pivot_pct": round(distance, 2),
        "entry": entry,
        "stop": stop,
        "risk_pct": round((1 - stop / entry) * 100, 2),
        "atr20": round(a20, 2),
        "breakout_volume_ratio": round(vol_today / vol50, 2) if vol50 > 0 else None,
        "ten_day_range_pct": round(float(tight_pct), 2),
        "quality": quality,
        "score": sum(1 for v in quality.values() if v),
        "score_max": len(quality),
    })
    return result


# ── post-buy monitor ─────────────────────────────────────────────────────────

def monitor_position(df: pd.DataFrame, entry_date: str, entry_price: float) -> dict:
    since = df[df.index.date >= pd.Timestamp(entry_date).date()]
    if len(since) < 2:
        return {"available": False, "reason": "Not enough sessions since entry yet."}
    closes = since["Close"]
    chg = closes.diff().iloc[1:]
    vol = since["Volume"].iloc[1:]
    green, red = int((chg > 0).sum()), int((chg < 0).sum())
    up_vol, down_vol = float(vol[chg > 0].sum()), float(vol[chg < 0].sum())
    vol50 = float(df["Volume"][df.index < since.index[0]].tail(50).mean() or 1)

    first5 = since.iloc[1:6]
    first5_chg = first5["Close"].diff().fillna(first5["Close"].iloc[0] - closes.iloc[0])
    follow_up_days = int(((first5_chg > 0) & (first5["Volume"] > vol50)).sum())

    peak = closes.cummax()
    pullback = float(((since["Low"] / peak) - 1).min() * 100)
    a20 = float(atr(df, 20).iloc[-1])
    shallow_limit = max(8.0, 2 * a20 / float(closes.iloc[-1]) * 100)

    # Tennis ball: after a down day (≥1%), how many sessions to close back above the pre-drop close?
    recoveries, c = [], closes.to_numpy(float)
    judged = slow = 0
    for k in range(1, len(c)):
        if c[k] <= c[k - 1] * 0.99:
            target = c[k - 1]
            days = next((j - k for j in range(k + 1, len(c)) if c[j] >= target), None)
            if days is not None:
                recoveries.append(days)
            if days is not None or len(c) - 1 - k >= 3:     # recovered, or had 3+ sessions to do so
                judged += 1
                if days is None or days > 3:
                    slow += 1
    avg_recovery = round(float(np.mean(recoveries)), 1) if recoveries else None

    signals = {
        "follow_up_buying": follow_up_days >= 2,
        "more_green_days": green >= red,
        "more_up_volume": up_vol >= down_vol,
        "tennis_ball_action": None if judged == 0 else slow <= judged / 3,   # None = no pullbacks to judge yet
        "shallow_pullbacks": pullback >= -shallow_limit,
    }
    score = sum(1 for v in signals.values() if v is True)
    applicable = sum(1 for v in signals.values() if v is not None)
    return {
        "available": True,
        "sessions": len(since) - 1,
        "gain_pct": round((float(closes.iloc[-1]) / entry_price - 1) * 100, 2),
        "green_days": green, "red_days": red,
        "up_down_volume": round(up_vol / down_vol, 2) if down_vol > 0 else None,
        "follow_up_days": follow_up_days,
        "max_pullback_pct": round(pullback, 2),
        "shallow_limit_pct": round(shallow_limit, 1),
        "avg_recovery_sessions": avg_recovery,
        "pullbacks_counted": judged,
        "slow_recoveries": slow,
        "signals": signals,
        "health": "strong" if score >= 0.75 * applicable else "mixed" if score >= 0.4 * applicable else "weak",
        "score": score,
        "applicable": applicable,
    }


# ── service ──────────────────────────────────────────────────────────────────

class VcpScannerService:
    def __init__(self, scanner) -> None:
        self.scanner = scanner
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def scan(self, universe: ScanUniverse, refresh: bool = False) -> dict:
        with self._lock:
            hit = self._cache.get(universe.value)
        if hit and not refresh and time.time() - hit[0] < 1800:
            return hit[1]
        result = self._scan(universe)
        with self._lock:
            self._cache[universe.value] = (time.time(), result)
        return result

    def _scan(self, universe: ScanUniverse) -> dict:
        from concurrent.futures import ThreadPoolExecutor

        from app.services.universe import load_universe

        listings = load_universe(universe=universe)
        try:
            self.scanner.market_data.prefetch_histories(listings, 420)
        except Exception:  # noqa: BLE001
            pass

        def work(listing):
            try:
                df = self.scanner.market_data.get_history(listing, lookback_days=420)
                df = df.copy()
                df.index = pd.to_datetime(df.index).tz_localize(None) if df.index.tz is not None else df.index
                return listing, scan_stock(df)
            except Exception as exc:  # noqa: BLE001
                logger.debug("VCP scan skipped %s: %s", listing.symbol, exc)
                return listing, None

        rows, counts = [], {"loaded": 0, "trend_template": 0, "valid_vcp": 0}
        fail_counts: dict[str, int] = {}
        week_ending = None
        with ThreadPoolExecutor(max_workers=8) as pool:
            for listing, res in pool.map(work, listings):
                if res is None:
                    continue
                counts["loaded"] += 1
                for name, ok in res["trend_template"]["checks"].items():
                    if not ok:
                        fail_counts[name] = fail_counts.get(name, 0) + 1
                if not res["trend_template"]["pass"]:
                    continue
                counts["trend_template"] += 1
                vcp = res["vcp"]
                if vcp and (week_ending is None or vcp["week_ending"] > week_ending):
                    week_ending = vcp["week_ending"]          # latest completed week across stocks
                if not vcp or not vcp["valid"]:
                    continue
                counts["valid_vcp"] += 1
                rows.append({"symbol": listing.symbol, "company_name": listing.company_name, "sector": listing.sector, **res})

        order = {"breakout today": 0, "breakout": 1, "near pivot": 2, "building": 3, "extended": 4}
        rows.sort(key=lambda r: (order.get(r["status"], 9), -r["score"], abs(r["distance_to_pivot_pct"])))
        by_status: dict[str, int] = {}
        for r in rows:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        return {
            "available": True,
            "universe": universe.value,
            "as_of": datetime.now(UTC).date().isoformat(),
            "week_ending": week_ending,
            "counts": counts,
            "by_status": by_status,
            "eliminated_by": fail_counts,
            "results": rows,
        }

    def monitor(self, positions: list[dict]) -> list[dict]:
        out = []
        for p in positions[:20]:
            symbol = p["symbol"]
            listing = find_listing(symbol) or StockListing(symbol, symbol, "Unknown", MarketCapBucket.SMALL)
            try:
                df = self.scanner.market_data.get_history(listing, lookback_days=320).copy()
                df.index = pd.to_datetime(df.index).tz_localize(None) if df.index.tz is not None else df.index
                out.append({"symbol": symbol, **monitor_position(df, p["entry_date"], float(p["entry_price"]))})
            except Exception as exc:  # noqa: BLE001
                out.append({"symbol": symbol, "available": False, "reason": f"No price data ({exc.__class__.__name__})."})
        return out
