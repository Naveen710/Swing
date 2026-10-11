"""Curated 5-layer swing system. Every trade must pass all five layers.

  1. Market regime gate   Nifty 500 > 50 DMA > 200 DMA; India VIX < 20 and not spiking;
                          > 50% of the universe above its 50 DMA. Fails → no new longs.
  2. Universe quality     20-day avg traded value > ₹10 Cr; price > ₹50; no circuit-like
                          days in the last 10 sessions; not on ASM/GSM; positive earnings
                          growth over the last 2 quarters.
  3. Leadership           top 20% by blended 3- & 6-month RS vs the index; in a top-5 sector
                          by sector RS; within 15% of the 52-week high.
  4. Setup trigger        Trend pullback  — 20 EMA > 50 EMA, RSI(14) turns up from 40–50,
                                            price reclaims the 20 EMA.
                          Base breakout   — ATR contracting 10+ days, close in the top 25%
                                            of the day's range and above the 10-day base,
                                            volume ≥ 1.5× its 50-day average.
  5. Confirmation (score) 1 point each: delivery % above its average, close above the prior
                          day's high, OBV at a 20-day high, no earnings within 7 days, sector
                          green today. Trade only with ≥ 4 / 5.

Exits: stop = tighter of entry − 1.5×ATR and the 10-day swing low. Sell half at +1.5R and
move the stop to breakeven; trail the rest under the 10 EMA. Time stop after 10 sessions if
+1.5R was never reached. Size halves after 3 consecutive losses; new entries pause for 10
sessions after 5.

Point-in-time limits (stated in every result): ASM/GSM lists, delivery %, earnings dates and
quarterly results have no free point-in-time history, so the backtest cannot apply them.
The backtest therefore requires the testable confirmations instead (configurable).
"""
from __future__ import annotations

import importlib
import io
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import requests

from app.config import settings
from app.schemas import MarketCapBucket, ScanUniverse
from app.services.db import load_artifact, save_artifact
from app.services.universe import StockListing, get_benchmark_candidates, load_universe

logger = logging.getLogger(__name__)

# ── rule parameters ──────────────────────────────────────────────────────────
VIX_MAX = 20.0
VIX_SPIKE = 1.25            # VIX up >25% over 5 sessions = spiking
BREADTH_MIN = 0.50
MIN_VALUE_CR = 10.0
MIN_PRICE = 50.0
LEADER_PCTL = 0.80          # top 20%
TOP_SECTORS = 5
NEAR_HIGH = 0.85            # within 15% of the 52-week high
MIN_POINTS_LIVE = 4
ATR_STOP_MULT = 1.5
SWING_LOW_DAYS = 10
PARTIAL_R = 1.5
TIME_STOP_SESSIONS = 10
MAX_HOLD = 40
MAX_POSITIONS = 5
MAX_PER_SECTOR = 2
RISK_PER_TRADE = 0.01
MAX_POSITION_PCT = 0.20
# Costs per side (%): STT 0.10 (delivery, both sides) + exchange/stamp/GST/brokerage ~0.03 + slippage 0.15
COST_STT, COST_CHARGES, COST_SLIPPAGE = 0.10, 0.03, 0.15
DATA_BREAK = 0.40
TARGETS = {"expectancy_r": 0.30, "profit_factor": 1.5, "min_trades": 200}
NIFTY500_SYMBOL = "^CRSLDX"
VIX_SYMBOL = settings.vix_symbol


def cost_per_side() -> float:
    return (COST_STT + COST_CHARGES + COST_SLIPPAGE) / 100


# ── data ─────────────────────────────────────────────────────────────────────

@dataclass
class Panel:
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    sectors: dict[str, str]
    names: dict[str, str]
    index_close: pd.Series
    index_name: str
    vix: pd.Series | None


def _index_frame(scanner, rows: int) -> tuple[pd.Series, str]:
    candidates = [StockListing(NIFTY500_SYMBOL, "Nifty 500", "Benchmark", MarketCapBucket.LARGE), *get_benchmark_candidates()]
    for listing in candidates:
        try:
            frame = scanner.market_data.get_history(listing, lookback_days=rows)
            if len(frame) > 210:
                name = "Nifty 500" if listing.symbol == NIFTY500_SYMBOL else settings.benchmark_name
                return frame["Close"], name
        except Exception as exc:  # noqa: BLE001
            logger.warning("Algo index %s unavailable. %s", listing.symbol, exc)
    raise RuntimeError("No benchmark index data available.")


def _vix(scanner, rows: int) -> pd.Series | None:
    if settings.market_data_provider == "demo" or not settings.enable_external_overlays:
        return None
    try:
        frame = scanner.market_data.get_history(
            StockListing(VIX_SYMBOL, "India VIX", "Benchmark", MarketCapBucket.LARGE), lookback_days=rows)
        series = frame["Close"]
        return series if 5 <= float(series.iloc[-1]) <= 90 else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("India VIX unavailable. %s", exc)
        return None


def load_panel(scanner, universe: ScanUniverse, rows: int, max_symbols: int | None = None,
               progress=None, listings: list[StockListing] | None = None) -> Panel:
    listings = list(listings) if listings is not None else load_universe(universe=universe)
    if max_symbols and len(listings) > max_symbols:
        step = len(listings) / max_symbols
        listings = [listings[int(i * step)] for i in range(max_symbols)]
    try:
        scanner.market_data.prefetch_histories(listings, rows)
    except Exception:  # noqa: BLE001
        pass
    frames: dict[str, pd.DataFrame] = {}
    meta: dict[str, tuple[str, str]] = {}
    done = 0

    def fetch(listing):
        try:
            return listing, scanner.market_data.get_history(listing, lookback_days=rows)
        except Exception:  # noqa: BLE001
            return listing, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        for listing, frame in pool.map(fetch, listings):
            done += 1
            if progress and done % 25 == 0:
                progress(done, len(listings))
            if frame is None or len(frame) < 260:
                continue
            frames[listing.symbol] = frame
            meta[listing.symbol] = (listing.company_name, listing.sector)
    if not frames:
        raise RuntimeError("No price history could be loaded.")

    def wide(col: str) -> pd.DataFrame:
        return pd.DataFrame({s: f[col] for s, f in frames.items()}).astype("float32")

    close = wide("Close")
    keep = close.notna().mean(axis=1) >= 0.5        # drop stray single-feed holiday bars
    close = close[keep]
    idx = close.index
    index_close, index_name = _index_frame(scanner, rows)
    vix = _vix(scanner, rows)
    return Panel(
        open=wide("Open").reindex(idx), high=wide("High").reindex(idx), low=wide("Low").reindex(idx),
        close=close, volume=wide("Volume").reindex(idx),
        sectors={s: m[1] for s, m in meta.items()}, names={s: m[0] for s, m in meta.items()},
        index_close=index_close.reindex(idx).ffill(), index_name=index_name,
        vix=None if vix is None else vix.reindex(idx).ffill(),
    )


# ── layers ───────────────────────────────────────────────────────────────────

@dataclass
class Layers:
    regime_ok: pd.Series
    regime_parts: pd.DataFrame
    quality: pd.DataFrame
    leader: pd.DataFrame
    pullback: pd.DataFrame
    breakout: pd.DataFrame
    conf_prev_high: pd.DataFrame
    conf_obv: pd.DataFrame
    conf_sector_green: pd.DataFrame
    atr: pd.DataFrame
    ema10: pd.DataFrame
    swing_low: pd.DataFrame
    rs_pctl: pd.DataFrame
    top_sectors: pd.DataFrame          # dates × sectors (bool)
    sector_rank: pd.DataFrame
    data_break: pd.DataFrame
    extras: dict = field(default_factory=dict)


def _rsi(close: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def compute_layers(p: Panel) -> Layers:
    c, h, l, v = p.close, p.high, p.low, p.volume
    prev_c = c.shift(1)

    # Layer 1 — regime
    idx = p.index_close
    sma50, sma200 = idx.rolling(50).mean(), idx.rolling(200).mean()
    above50 = c > c.rolling(50).mean()
    eligible = c.rolling(50).count() >= 50
    breadth = above50.where(eligible).sum(axis=1) / eligible.sum(axis=1).replace(0, np.nan)
    parts = pd.DataFrame({
        "index_above_50dma": idx > sma50,
        "50dma_above_200dma": sma50 > sma200,
        "breadth_ok": breadth > BREADTH_MIN,
    })
    parts["breadth"] = breadth
    parts["index_close"], parts["index_sma50"], parts["index_sma200"] = idx, sma50, sma200
    if p.vix is not None:
        parts["vix"] = p.vix
        parts["vix_ok"] = (p.vix < VIX_MAX) & ((p.vix / p.vix.shift(5)) < VIX_SPIKE)
    else:
        parts["vix"] = np.nan
        parts["vix_ok"] = True       # unknown → not used (stated in the output)
    regime_ok = parts["index_above_50dma"] & parts["50dma_above_200dma"] & parts["breadth_ok"] & parts["vix_ok"].astype(bool)

    # Layer 2 — quality (price-based parts; ASM/GSM and earnings applied live only)
    value20 = (c * v).rolling(20, min_periods=15).mean()
    ret = c / prev_c - 1
    day_range = (h - l).replace(0, np.nan)
    pinned_up = (ret >= 0.049) & ((h - c).abs() <= 0.0005 * c)
    pinned_dn = (ret <= -0.049) & ((c - l).abs() <= 0.0005 * c)
    circuit_recent = (pinned_up | pinned_dn).astype("float32").rolling(10, min_periods=1).max() > 0
    data_break = ret.abs() > DATA_BREAK
    quality = (value20 >= MIN_VALUE_CR * 1e7) & (c >= MIN_PRICE) & ~circuit_recent

    # Layer 3 — leadership
    bench63, bench126 = idx / idx.shift(63) - 1, idx / idx.shift(126) - 1
    rs = 0.5 * (c / c.shift(63) - 1).sub(bench63, axis=0) + 0.5 * (c / c.shift(126) - 1).sub(bench126, axis=0)
    rs_pctl = rs.rank(axis=1, pct=True)
    sector_series = pd.Series(p.sectors)
    sector_rs = rs.T.groupby(sector_series).median().T
    sector_counts = sector_series.value_counts()
    sector_rs = sector_rs.loc[:, [s for s in sector_rs.columns if sector_counts.get(s, 0) >= 3 and s not in ("Unknown",)]]
    sector_rank = sector_rs.rank(axis=1, ascending=False)
    top_sectors = sector_rank <= TOP_SECTORS
    in_top_sector = pd.DataFrame({s: top_sectors[p.sectors[s]] if p.sectors[s] in top_sectors.columns else False for s in c.columns}, index=c.index).fillna(False).astype(bool)
    high52 = h.rolling(252, min_periods=200).max()
    leader = (rs_pctl >= LEADER_PCTL) & in_top_sector & (c >= NEAR_HIGH * high52)

    # Layer 4 — setups
    ema10 = c.ewm(span=10, adjust=False).mean()
    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    rsi = _rsi(c)
    rsi_dip = pd.concat([rsi.shift(k) for k in (1, 2, 3)]).groupby(level=0).min().reindex(c.index)
    reclaim = (c > ema20) & ((prev_c <= ema20.shift(1)) | (c.shift(2) <= ema20.shift(2)))
    pullback = (ema20 > ema50) & (rsi_dip >= 40) & (rsi_dip <= 50) & (rsi > rsi.shift(1)) & reclaim

    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()]).groupby(level=0).max().reindex(c.index)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    atr_contracting = (atr.shift(1) <= 0.9 * atr.shift(11)) & (atr.shift(1) < atr.shift(6))
    close_pos = (c - l) / day_range
    base_high = h.rolling(10).max().shift(1)
    vol50 = v.rolling(50, min_periods=40).mean().shift(1)
    breakout = atr_contracting & (close_pos >= 0.75) & (c > base_high) & (v >= 1.5 * vol50)

    # Layer 5 — testable confirmations
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    conf_obv = obv >= obv.rolling(20).max()
    conf_prev_high = c > h.shift(1)
    sector_ret = ret.T.groupby(sector_series).median().T
    conf_sector_green = pd.DataFrame({s: sector_ret[p.sectors[s]] > 0 if p.sectors[s] in sector_ret.columns else False for s in c.columns}, index=c.index).fillna(False).astype(bool)

    swing_low = l.rolling(SWING_LOW_DAYS).min()
    return Layers(
        regime_ok=regime_ok.fillna(False), regime_parts=parts, quality=quality.fillna(False), leader=leader.fillna(False),
        pullback=pullback.fillna(False), breakout=breakout.fillna(False),
        conf_prev_high=conf_prev_high.fillna(False), conf_obv=conf_obv.fillna(False), conf_sector_green=conf_sector_green,
        atr=atr, ema10=ema10, swing_low=swing_low, rs_pctl=rs_pctl, top_sectors=top_sectors, sector_rank=sector_rank,
        data_break=data_break.fillna(False),
        extras={"value20": value20, "high52": high52, "rsi": rsi, "rs": rs, "sector_rs": sector_rs, "vol50": vol50, "close_pos": close_pos},
    )


def stop_for(close: float, atr: float, swing_low: float) -> float:
    """Tighter (higher) of close − 1.5×ATR and the swing low, but always below the close."""
    candidates = [x for x in (close - ATR_STOP_MULT * atr, swing_low) if x is not None and not math.isnan(x) and x < close]
    return max(candidates) if candidates else close - ATR_STOP_MULT * atr


# ── backtest ─────────────────────────────────────────────────────────────────

def backtest(p: Panel, L: Layers, start: str = "2015-01-01", required_confirmations: int = 3,
             capital: float = 1_000_000.0) -> dict:
    dates = p.close.index
    begin = int(np.searchsorted(dates, pd.Timestamp(start, tz=dates.tz)))
    begin = max(begin, 260)
    cps = cost_per_side()
    o, h, lo, c = (p.open.to_numpy(), p.high.to_numpy(), p.low.to_numpy(), p.close.to_numpy())
    ema10, atr, swing = L.ema10.to_numpy(), L.atr.to_numpy(), L.swing_low.to_numpy()
    cols = list(p.close.columns)
    breaks = L.data_break.to_numpy()
    setup_any = (L.pullback | L.breakout)
    signal = L.quality & L.leader & setup_any
    conf = L.conf_prev_high.astype(int) + L.conf_obv.astype(int) + L.conf_sector_green.astype(int)
    signal = signal & (conf >= required_confirmations)
    signal_np = signal.to_numpy()
    conf_np, rs_np = conf.to_numpy(), L.rs_pctl.to_numpy()
    breakout_np = L.breakout.to_numpy()
    regime = L.regime_ok.to_numpy()

    cash = capital
    positions: list[dict] = []
    trades: list[dict] = []
    equity_curve: list[tuple] = []
    loss_streak = 0
    pause_until = -1
    excluded = 0
    regime_off_days = 0
    pending: list[tuple] = []

    def close_position(pos, j, price, day, reason):
        nonlocal cash, loss_streak, pause_until
        proceeds = pos["qty"] * price * (1 - cps)
        cash += proceeds
        pnl = pos["realised"] + proceeds - pos["cost_basis_remaining"]
        r_mult = pnl / pos["risk_rupees"] if pos["risk_rupees"] > 0 else 0.0
        trades.append({
            "symbol": cols[j], "sector": p.sectors.get(cols[j]), "setup": pos["setup"],
            "entry_date": dates[pos["entry_i"]].date().isoformat(), "exit_date": dates[day].date().isoformat(),
            "entry": round(pos["entry"], 2), "exit": round(price, 2), "partial": pos["partial_done"],
            "r": round(r_mult, 3), "pnl": round(pnl, 2), "sessions": day - pos["entry_i"] + 1, "reason": reason,
            "size_factor": pos["size_factor"], "year": dates[pos["entry_i"]].year,
        })
        if r_mult < 0:
            loss_streak += 1
            if loss_streak >= 5:
                pause_until = day + 10
                loss_streak = 3          # stay at half size after the pause
        else:
            loss_streak = 0

    for i in range(begin, len(dates)):
        # 1) fill yesterday's orders at today's open
        if pending:
            for j, setup, sig_close, sig_atr, sig_swing, size_factor in pending:
                if len(positions) >= MAX_POSITIONS:
                    break
                if any(pp["j"] == j for pp in positions):
                    continue
                if sum(1 for pp in positions if p.sectors.get(cols[pp["j"]]) == p.sectors.get(cols[j])) >= MAX_PER_SECTOR:
                    continue
                entry = o[i, j]
                if not np.isfinite(entry) or entry <= 0:
                    continue
                stop = stop_for(sig_close, sig_atr, sig_swing)
                if entry <= stop:
                    continue                  # gapped below the stop — skip
                # exclude windows with corrupted data (unadjusted corporate actions)
                if breaks[i:min(i + MAX_HOLD, len(dates)), j].any():
                    excluded += 1
                    continue
                risk_ps = entry - stop
                equity = cash + sum(pp["qty"] * c[i - 1, pp["j"]] for pp in positions if np.isfinite(c[i - 1, pp["j"]]))
                qty = math.floor(min(equity * RISK_PER_TRADE * size_factor / risk_ps, equity * MAX_POSITION_PCT / entry))
                cost = qty * entry * (1 + cps)
                if qty <= 0 or cost > cash:
                    continue
                cash -= cost
                positions.append({
                    "j": j, "entry_i": i, "entry": entry, "stop": stop, "initial_stop": stop, "qty": qty,
                    "risk_rupees": qty * risk_ps, "partial_done": False, "realised": 0.0,
                    "cost_basis_remaining": cost, "setup": setup, "size_factor": size_factor,
                })
            pending = []

        # 2) manage open positions on today's bar
        for pos in list(positions):
            j = pos["j"]
            bo, bh, bl, bc = o[i, j], h[i, j], lo[i, j], c[i, j]
            if not np.isfinite(bc):
                continue
            held = i - pos["entry_i"] + 1
            first_day = held == 1
            if bl <= pos["stop"]:
                price = pos["stop"] if first_day else min(bo, pos["stop"])
                close_position(pos, j, price, i, "stop" if not pos["partial_done"] else "breakeven/trail stop")
                positions.remove(pos)
                continue
            target = pos["entry"] + PARTIAL_R * (pos["entry"] - pos["initial_stop"])
            if not pos["partial_done"] and bh >= target:
                half = pos["qty"] // 2
                if half > 0:
                    fill = max(bo, target) if not first_day else target
                    proceeds = half * fill * (1 - cps)
                    cash += proceeds
                    basis_share = pos["cost_basis_remaining"] / pos["qty"]
                    pos["realised"] += proceeds - half * basis_share
                    pos["cost_basis_remaining"] -= half * basis_share
                    pos["qty"] -= half
                pos["partial_done"] = True
                pos["stop"] = max(pos["stop"], pos["entry"])
            if pos["partial_done"] and bc < ema10[i, j]:
                close_position(pos, j, bc, i, "trail (close < 10 EMA)")
                positions.remove(pos)
                continue
            if not pos["partial_done"] and held >= TIME_STOP_SESSIONS:
                close_position(pos, j, bc, i, "time stop")
                positions.remove(pos)
                continue
            if held >= MAX_HOLD:
                close_position(pos, j, bc, i, "max hold")
                positions.remove(pos)

        # 3) today's signals → orders for tomorrow
        if not regime[i]:
            regime_off_days += 1
        elif i >= pause_until and len(positions) < MAX_POSITIONS:
            cand = np.flatnonzero(signal_np[i])
            if cand.size:
                order = sorted(cand, key=lambda j: (-conf_np[i, j], -(rs_np[i, j] if np.isfinite(rs_np[i, j]) else 0)))
                size_factor = 0.5 if loss_streak >= 3 else 1.0
                pending = [(j, "base breakout" if breakout_np[i, j] else "trend pullback", c[i, j], atr[i, j], swing[i, j], size_factor)
                           for j in order[: MAX_POSITIONS * 2]]

        equity = cash + sum(pp["qty"] * c[i, pp["j"]] for pp in positions if np.isfinite(c[i, pp["j"]]))
        equity_curve.append((dates[i], equity))

    for pos in list(positions):  # mark open trades to the last close
        close_position(pos, pos["j"], c[-1, pos["j"]], len(dates) - 1, "open at end")

    return summarise(trades, equity_curve, capital, p, excluded, regime_off_days, len(dates) - begin, required_confirmations, start)


def _stats(tr: pd.DataFrame) -> dict:
    if tr.empty:
        return {"trades": 0, "win_rate": None, "expectancy_r": None, "profit_factor": None, "avg_win_r": None, "avg_loss_r": None, "pnl": 0.0}
    wins, losses = tr[tr.pnl > 0], tr[tr.pnl <= 0]
    gross_loss = -losses.pnl.sum()
    return {
        "trades": int(len(tr)),
        "win_rate": round(float(len(wins) / len(tr)), 3),
        "expectancy_r": round(float(tr.r.mean()), 3),
        "profit_factor": round(float(wins.pnl.sum() / gross_loss), 2) if gross_loss > 0 else None,
        "avg_win_r": round(float(wins.r.mean()), 2) if len(wins) else None,
        "avg_loss_r": round(float(losses.r.mean()), 2) if len(losses) else None,
        "pnl": round(float(tr.pnl.sum()), 2),
    }


def summarise(trades, curve, capital, p: Panel, excluded, regime_off_days, total_days, required_confirmations, start) -> dict:
    tr = pd.DataFrame(trades)
    eq = pd.Series([e for _, e in curve], index=[d for d, _ in curve], dtype=float)
    if eq.empty:
        return {"available": False, "reason": "No trading days in the selected period."}
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = ((eq.iloc[-1] / capital) ** (1 / years) - 1) * 100
    dd = float(((eq / eq.cummax()) - 1).min() * 100)
    overall = _stats(tr)
    idx = p.index_close.reindex(eq.index).ffill()
    valid_idx = idx.dropna()
    bench_cagr = float(((valid_idx.iloc[-1] / valid_idx.iloc[0]) ** (1 / years) - 1) * 100) if len(valid_idx) > 1 else None
    bench_base = float(valid_idx.iloc[0]) if len(valid_idx) else None

    per_year = []
    if not tr.empty:
        year_end = eq.groupby(eq.index.year).last()
        year_start = eq.groupby(eq.index.year).first()
        for y, grp in tr.groupby("year"):
            s = _stats(grp)
            s["year"] = int(y)
            s["return_pct"] = round(float((year_end.get(y, np.nan) / year_start.get(y, np.nan) - 1) * 100), 2) if y in year_end.index else None
            per_year.append(s)
    checks = {
        "expectancy_r": overall["expectancy_r"] is not None and overall["expectancy_r"] > TARGETS["expectancy_r"],
        "profit_factor": overall["profit_factor"] is not None and overall["profit_factor"] > TARGETS["profit_factor"],
        "min_trades": overall["trades"] >= TARGETS["min_trades"],
    }
    positive_years = sum(1 for y in per_year if (y["expectancy_r"] or 0) > 0)
    step = max(1, len(eq) // 250)
    curve_out = [{"date": d.date().isoformat(), "equity": round(float(e / capital * 100), 2),
                  "benchmark": round(float(idx.loc[d] / bench_base * 100), 2) if bench_base and pd.notna(idx.loc[d]) else None}
                 for d, e in list(eq.items())[::step]]
    return {
        "available": True,
        "start": eq.index[0].date().isoformat(), "end": eq.index[-1].date().isoformat(),
        "symbols_tested": int(p.close.shape[1]), "index_name": p.index_name,
        "overall": overall,
        "cagr_pct": round(float(cagr), 2), "max_drawdown_pct": round(dd, 2), "benchmark_cagr_pct": None if bench_cagr is None else round(bench_cagr, 2),
        "targets": TARGETS, "checks": checks, "passes_all": all(checks.values()),
        "per_year": per_year, "positive_years": positive_years, "years_with_trades": len(per_year),
        "by_setup": {k: _stats(g) for k, g in tr.groupby("setup")} if not tr.empty else {},
        "by_exit": {k: _stats(g) for k, g in tr.groupby("reason")} if not tr.empty else {},
        "regime_off_pct": round(regime_off_days / max(total_days, 1) * 100, 1),
        "excluded_data_breaks": excluded,
        "cost_per_side_pct": round(cost_per_side() * 100, 2),
        "required_confirmations": required_confirmations,
        "curve": curve_out,
        "recent_trades": tr.tail(40).to_dict("records") if not tr.empty else [],
        "assumptions": [
            f"Universe: today's Nifty 500 members ({p.close.shape[1]} with enough history) — survivorship bias flatters results.",
            f"Index for the regime gate and RS: {p.index_name}.",
            "Signals use the day's close; entries fill at the next day's open (skipped if it opens below the stop).",
            f"Costs per side: STT {COST_STT}% + charges {COST_CHARGES}% + slippage {COST_SLIPPAGE}% = {cost_per_side()*100:.2f}%.",
            f"Risk {RISK_PER_TRADE*100:.0f}% of equity per trade (max {MAX_POSITION_PCT*100:.0f}% per position), max {MAX_POSITIONS} positions and {MAX_PER_SECTOR} per sector.",
            f"Not modelled (no point-in-time history): ASM/GSM lists, delivery %, earnings dates, quarterly earnings growth. Instead the backtest requires {required_confirmations} of the 3 testable confirmations (prior-day high, OBV 20-day high, sector green).",
            "India VIX check uses Yahoo's ^INDIAVIX history when available.",
            f"Data cleaning: trades whose holding window contains a one-day move above {DATA_BREAK*100:.0f}% (usually an unadjusted split/bonus) are excluded ({excluded} trades). This uses future bars, so it is reported, not hidden.",
            "Each calendar year is reported separately: the rules have no fitted parameters, so every year is out of sample — consistency across years (especially 2020 and 2022) is the walk-forward test.",
        ],
    }


# ── live screen ──────────────────────────────────────────────────────────────

def _quarterly_growth(symbol: str) -> tuple[bool | None, str]:
    """Positive earnings growth over the last 2 quarters (YoY when 6+ quarters exist, else QoQ)."""
    try:
        q = importlib.import_module("yfinance").Ticker(symbol).quarterly_income_stmt
        if q is None or q.empty:
            return None, "no quarterly data"
        row = next((r for r in ("Net Income", "Net Income Common Stockholders") if r in q.index), None)
        if row is None:
            return None, "no quarterly profit line"
        ni = q.loc[row].dropna().sort_index()
        vals = list(ni.values)
        if len(vals) >= 6:
            ok = vals[-1] > vals[-5] and vals[-2] > vals[-6]
            return bool(ok), "YoY, last 2 quarters"
        if len(vals) >= 3:
            ok = vals[-1] > vals[-2] > vals[-3]
            return bool(ok), "QoQ, last 2 quarters"
        return None, "too few quarters"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Quarterly results unavailable for %s. %s", symbol, exc)
        return None, "unavailable"


def _fetch_surveillance() -> tuple[set[str] | None, str]:
    """ASM/GSM symbols from URLs set in ASM_GSM_URLS (comma-separated CSVs with a Symbol column)."""
    import os
    urls = [u.strip() for u in os.getenv("ASM_GSM_URLS", "").split(",") if u.strip()]
    if not urls:
        return None, "not configured"
    found: set[str] = set()
    for url in urls:
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            frame = pd.read_csv(io.StringIO(resp.text))
            col = next(c for c in frame.columns if "symbol" in c.lower())
            found |= {str(s).strip().upper() for s in frame[col].dropna()}
        except Exception as exc:  # noqa: BLE001
            logger.warning("ASM/GSM list %s unavailable. %s", url, exc)
            return None, "fetch failed"
    return found, "nse lists"


class CuratedAlgoService:
    def __init__(self, scanner) -> None:
        self.scanner = scanner
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()
        self._bt_lock = threading.Lock()
        self.bt_state: dict = {"running": False, "stage": "idle"}

    # live
    def live(self, universe: ScanUniverse, exclude: set[str], refresh: bool = False) -> dict:
        key = universe.value
        with self._lock:
            hit = self._cache.get(key)
        if hit and not refresh and time.time() - hit[0] < 1800:
            base = hit[1]
        else:
            base = self._live_compute(universe)
            with self._lock:
                self._cache[key] = (time.time(), base)
        return self._apply_exclusions(base, exclude)

    def _apply_exclusions(self, base: dict, exclude: set[str]) -> dict:
        if not exclude or not base.get("available"):
            return base
        out = dict(base)
        keep, dropped = [], []
        for t in base["trades"]:
            (dropped if t["symbol"].removesuffix(".NS") in exclude else keep).append(t)
        out["trades"] = keep
        out["excluded_by_you"] = [t["symbol"] for t in dropped]
        return out

    def _live_compute(self, universe: ScanUniverse) -> dict:
        p = load_panel(self.scanner, universe, rows=420)
        L = compute_layers(p)
        last = p.close.index[-1]
        i = -1
        rp = L.regime_parts.iloc[i]
        regime = {
            "pass": bool(L.regime_ok.iloc[i]),
            "index_name": p.index_name,
            "checks": [
                {"name": f"{p.index_name} above its 50 DMA", "pass": bool(rp["index_above_50dma"]),
                 "value": f"{rp['index_close']:.0f} vs {rp['index_sma50']:.0f}"},
                {"name": "50 DMA above 200 DMA", "pass": bool(rp["50dma_above_200dma"]),
                 "value": f"{rp['index_sma50']:.0f} vs {rp['index_sma200']:.0f}"},
                {"name": f"India VIX below {VIX_MAX:g} and not spiking", "pass": bool(rp["vix_ok"]) if p.vix is not None else None,
                 "value": "unavailable — check not applied" if p.vix is None else f"{rp['vix']:.1f}"},
                {"name": f"Breadth: > {BREADTH_MIN:.0%} above 50 DMA", "pass": bool(rp["breadth_ok"]),
                 "value": f"{rp['breadth']*100:.0f}%" if pd.notna(rp["breadth"]) else "—"},
            ],
        }

        q = L.quality.iloc[i]
        lead = L.leader.iloc[i]
        setups = (L.pullback.iloc[i] | L.breakout.iloc[i])
        n_total = int(p.close.iloc[i].notna().sum())
        after_quality = q
        after_leader = after_quality & lead
        after_setup = after_leader & setups
        candidates = list(after_setup[after_setup].index)

        # live-only checks on the few remaining candidates
        reference = last.date()
        delivery = {}
        try:
            ctx = self.scanner._load_benchmark_context(300)
            delivery = self.scanner._load_delivery_trends(ctx)
        except Exception:  # noqa: BLE001
            pass
        external = self.scanner._external_overlays_enabled()
        surveillance, surveillance_src = _fetch_surveillance()

        trades, near_misses = [], []
        sector_rank_today = L.sector_rank.iloc[i]
        for s in candidates:
            sector = p.sectors.get(s, "Unknown")
            growth_ok, growth_method = _quarterly_growth(s) if external else (None, "demo mode")
            asm = None if surveillance is None else (s.removesuffix(".NS") in surveillance)
            if growth_ok is False or asm is True:
                near_misses.append({"symbol": s, "reason": "negative earnings growth" if growth_ok is False else "on ASM/GSM list"})
                continue
            dtrend = delivery.get(s.upper())
            delivery_point = None if dtrend is None else bool(dtrend.latest_delivery_pct > dtrend.average_delivery_pct_10d)
            earn_point, earn_detail = None, "unknown"
            if external:
                ev = self.scanner.event_risk.get_snapshot(s, reference)
                if ev.days_to_earnings is not None:
                    earn_point = not (0 <= ev.days_to_earnings <= 7)
                    earn_detail = f"results in {ev.days_to_earnings} days" if ev.days_to_earnings >= 0 else "results already out"
            points = [
                {"name": "Delivery % above its average", "pass": delivery_point,
                 "detail": "NSE delivery data unavailable" if dtrend is None else f"{dtrend.latest_delivery_pct:.0f}% vs {dtrend.average_delivery_pct_10d:.0f}% avg"},
                {"name": "Close above prior day's high", "pass": bool(L.conf_prev_high.iloc[i][s]), "detail": ""},
                {"name": "OBV at a 20-day high", "pass": bool(L.conf_obv.iloc[i][s]), "detail": ""},
                {"name": "No earnings within 7 days", "pass": earn_point, "detail": earn_detail},
                {"name": "Sector green today", "pass": bool(L.conf_sector_green.iloc[i][s]), "detail": "median of sector stocks"},
            ]
            score = sum(1 for pt in points if pt["pass"] is True)
            close = float(p.close.iloc[i][s])
            atr = float(L.atr.iloc[i][s])
            stop = stop_for(close, atr, float(L.swing_low.iloc[i][s]))
            risk = close - stop
            row = {
                "symbol": s, "company_name": p.names.get(s, s), "sector": sector,
                "setup": "base breakout" if bool(L.breakout.iloc[i][s]) else "trend pullback",
                "close": round(close, 2), "stop": round(stop, 2), "risk_per_share": round(risk, 2),
                "risk_pct": round(risk / close * 100, 2),
                "partial_target": round(close + PARTIAL_R * risk, 2),
                "atr": round(atr, 2), "ema10": round(float(L.ema10.iloc[i][s]), 2),
                "rs_percentile": round(float(L.rs_pctl.iloc[i][s]) * 100, 1),
                "sector_rank": None if sector not in sector_rank_today.index or pd.isna(sector_rank_today[sector]) else int(sector_rank_today[sector]),
                "pct_from_52w_high": round((1 - close / float(L.extras["high52"].iloc[i][s])) * 100, 1),
                "avg_traded_value_cr": round(float(L.extras["value20"].iloc[i][s]) / 1e7, 1),
                "score": score, "points": points,
                "earnings_growth": {"pass": growth_ok, "method": growth_method},
                "asm_gsm": asm,
            }
            (trades if score >= MIN_POINTS_LIVE else near_misses).append(
                row if score >= MIN_POINTS_LIVE else {**row, "reason": f"confirmation {score}/5 (needs {MIN_POINTS_LIVE})"})

        trades.sort(key=lambda t: (-t["score"], -t["rs_percentile"]))
        top_sector_names = [s for s, r in sector_rank_today.dropna().sort_values().items() if r <= TOP_SECTORS]
        return {
            "available": True,
            "as_of": reference.isoformat(),
            "universe": universe.value,
            "regime": regime,
            "funnel": [
                {"layer": "Universe loaded", "count": n_total},
                {"layer": "2 · Quality (liquidity, price, no circuits)", "count": int(after_quality.sum())},
                {"layer": "3 · Leadership (top 20% RS, top-5 sector, near high)", "count": int(after_leader.sum())},
                {"layer": "4 · Setup trigger", "count": int(after_setup.sum())},
                {"layer": f"5 · Confirmation ≥ {MIN_POINTS_LIVE}/5 (+ earnings, ASM/GSM)", "count": len(trades)},
            ],
            "longs_allowed": regime["pass"],
            "top_sectors": top_sector_names,
            "trades": trades if regime["pass"] else [],
            "blocked_by_regime": trades if not regime["pass"] else [],
            "near_misses": near_misses[:15],
            "data_notes": [n for n in [
                None if p.vix is not None else "India VIX unavailable — the VIX check is not applied.",
                None if surveillance is not None else ("ASM/GSM lists aren't configured on the server — use the exclusion box to remove listed stocks."
                                                        if surveillance_src == "not configured" else "ASM/GSM lists couldn't be downloaded — use the exclusion box."),
                None if delivery else "NSE delivery data unavailable — that confirmation point can't be earned today.",
                None if external else "Demo mode: earnings checks are skipped.",
            ] if n],
            "rules": {"partial_r": PARTIAL_R, "atr_stop_mult": ATR_STOP_MULT, "time_stop_sessions": TIME_STOP_SESSIONS,
                      "trail": "10 EMA", "min_points": MIN_POINTS_LIVE},
        }

    # backtest
    def start_backtest(self, universe: ScanUniverse, start: str, required_confirmations: int, max_symbols: int | None) -> bool:
        with self._bt_lock:
            if self.bt_state.get("running"):
                return False
            self.bt_state = {"running": True, "stage": "loading prices", "progress": 0, "started_at": datetime.now(UTC).isoformat()}
        threading.Thread(target=self._run_bt, args=(universe, start, required_confirmations, max_symbols), daemon=True, name="algo-backtest").start()
        return True

    def _run_bt(self, universe, start, required, max_symbols) -> None:
        try:
            rows = int((date.today() - date.fromisoformat(start)).days * 252 / 365) + 300
            p = load_panel(self.scanner, universe, rows=rows, max_symbols=max_symbols,
                           progress=lambda d, n: self.bt_state.update(progress=round(d / n * 80)))
            self.bt_state.update(stage="computing layers", progress=85)
            L = compute_layers(p)
            self.bt_state.update(stage="simulating trades", progress=90)
            result = backtest(p, L, start=start, required_confirmations=required)
            result["universe"] = universe.value
            result["run_at"] = datetime.now(UTC).isoformat()
            save_artifact(f"curated_algo:{universe.value}", result)
            self.bt_state = {"running": False, "stage": "done", "progress": 100}
        except Exception as exc:  # noqa: BLE001
            logger.exception("Algo backtest failed")
            self.bt_state = {"running": False, "stage": "error", "error": str(exc)}

    def latest_backtest(self, universe: ScanUniverse) -> dict | None:
        return load_artifact(f"curated_algo:{universe.value}")
