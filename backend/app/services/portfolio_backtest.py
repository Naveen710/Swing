"""Walk-forward portfolio simulation of the whole system.

Replays the live scanner over history, day by day, across the universe, and
trades the signals under the same portfolio rules you use: regime thresholds,
weekly gate, max 5 positions, max 2 per sector, 1.5% risk per trade capped at
20% of equity, buy-stop entries, conservative exits and round-trip costs.

Known limits (reported with the results):
- Survivorship bias: only stocks listed today are tested.
- Earnings blackout, fundamentals and sector-peer ranks can't be reconstructed
  historically, so those gates are not applied in the simulation.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.config import settings
from app.schemas import ScanUniverse
from app.services.data_quality import data_quality_issue
from app.services.db import load_artifact, save_artifact
from app.services.indicators import apply_indicators
from app.services.market_regime import REGIME_THRESHOLDS
from app.services.patterns import detect_best_pattern
from app.services.relative_strength import RelativeStrengthContext, build_relative_strength_snapshot
from app.services.trade_sim import TradePlan, plan_is_tradeable, simulate_trade
from app.services.universe import load_universe

logger = logging.getLogger(__name__)

WINDOW = 300          # bars of context each historical "scan" sees
WARMUP = 260          # bars needed before the first tradable day (EMA200, 52W high)
BASE_MIN_PROB = 0.55
BASE_MIN_RR = 1.8
MAX_POSITIONS = 5
MAX_PER_SECTOR = 2
RISK_PCT = 1.5
MAX_POSITION_PCT = 20.0
START_CAPITAL = 1_000_000.0


@dataclass
class HistSignal:
    day: pd.Timestamp
    symbol: str
    sector: str
    pattern: str
    plan: TradePlan
    probability: float
    ranking_score: float
    risk_reward: float
    weekly_aligned: bool
    liquid: bool


def artifact_key(universe: ScanUniverse) -> str:
    return f"portfolio_backtest:{universe.value}"


def latest_result(universe: ScanUniverse) -> dict | None:
    return load_artifact(artifact_key(universe))


class PortfolioBacktestRunner:
    """Runs one simulation at a time in a background thread and reports progress."""

    def __init__(self, scanner) -> None:
        self.scanner = scanner
        self._lock = threading.Lock()
        self.state: dict = {"running": False}

    def start(self, universe: ScanUniverse, years: int, max_symbols: int | None) -> bool:
        with self._lock:
            if self.state.get("running"):
                return False
            self.state = {
                "running": True, "universe": universe.value, "years": years, "stage": "loading data",
                "progress": 0.0, "started_at": datetime.now(UTC).isoformat(), "error": None,
            }
        threading.Thread(
            target=self._run, args=(universe, years, max_symbols), daemon=True, name="portfolio-backtest"
        ).start()
        return True

    def _update(self, **kwargs) -> None:
        with self._lock:
            self.state.update(kwargs)

    def _run(self, universe: ScanUniverse, years: int, max_symbols: int | None) -> None:
        try:
            result = run_portfolio_backtest(self.scanner, universe, years, max_symbols, progress=self._update)
            self._update(running=False, stage="done", progress=1.0,
                         finished_at=datetime.now(UTC).isoformat(), summary=result["metrics"])
        except Exception as exc:  # noqa: BLE001
            logger.exception("Portfolio backtest failed")
            self._update(running=False, stage="failed", error=str(exc))


def run_portfolio_backtest(scanner, universe: ScanUniverse, years: int = 2, max_symbols: int | None = None, progress=None) -> dict:
    progress = progress or (lambda **_: None)
    years = max(1, min(years, 3))
    test_bars = 252 * years
    lookback = test_bars + WARMUP + 20

    listings = load_universe(universe=universe)
    if max_symbols and len(listings) > max_symbols:
        step = len(listings) / max_symbols
        listings = [listings[int(i * step)] for i in range(max_symbols)]

    benchmark_ctx = scanner._load_benchmark_context(lookback)
    if benchmark_ctx is None:
        raise RuntimeError("Benchmark history unavailable; cannot define the trading calendar.")
    bench = benchmark_ctx.benchmark_frame
    calendar = bench.index[-test_bars:]
    start_day = calendar[0]
    reference_date = bench.index[-1].date()

    try:
        scanner.market_data.prefetch_histories(listings, lookback)
    except Exception:  # noqa: BLE001
        pass

    signals: list[HistSignal] = []
    samples: list[dict] = []
    bars: dict[str, pd.DataFrame] = {}
    breadth_above: defaultdict[pd.Timestamp, int] = defaultdict(int)
    breadth_total: defaultdict[pd.Timestamp, int] = defaultdict(int)
    skipped: dict[str, str] = {}
    fill_window = settings.trade_fill_window_sessions

    for n, listing in enumerate(listings, start=1):
        progress(stage=f"scanning history {n}/{len(listings)}", progress=round(0.85 * n / len(listings), 3))
        try:
            raw = scanner.market_data.get_history(listing, lookback_days=lookback)
        except Exception as exc:  # noqa: BLE001
            skipped[listing.symbol] = f"no data: {exc}"
            continue
        issue = data_quality_issue(raw, reference_date)
        if issue:
            skipped[listing.symbol] = issue
            continue
        frame = apply_indicators(raw)
        if len(frame) < WARMUP + 40:
            skipped[listing.symbol] = "insufficient history"
            continue

        test_mask = frame.index >= start_day
        above = (frame["Close"] > frame["ema50"])[test_mask]
        for day, flag in above.items():
            breadth_total[day] += 1
            breadth_above[day] += int(flag)
        bars[listing.symbol] = frame[["Open", "High", "Low", "Close"]]

        first = max(int(np.argmax(test_mask)) if test_mask.any() else len(frame), WARMUP)
        i = first
        while i < len(frame) - 1:
            window = frame.iloc[max(0, i - WINDOW + 1): i + 1]
            day = frame.index[i]
            rs = build_relative_strength_snapshot(
                window,
                RelativeStrengthContext(benchmark_listing=benchmark_ctx.benchmark_listing, benchmark_frame=bench.loc[:day]),
            )
            match = detect_best_pattern(window, rs)
            if match is None:
                i += 1
                continue
            try:
                cand = scanner._build_trade_candidate(
                    listing=listing, frame=window, match=match, investment_amount=100000,
                    relative_strength=rs, delivery_trends={},
                    benchmark_close=bench["Close"].loc[:day],
                )
            except Exception:  # noqa: BLE001
                i += 1
                continue
            if cand.probability_score < BASE_MIN_PROB or cand.risk_reward_ratio < BASE_MIN_RR:
                i += 1
                continue

            plan = TradePlan(cand.entry_price, cand.stop_loss, cand.target_price)
            if not plan_is_tradeable(plan):
                i += 1
                continue
            signals.append(HistSignal(
                day=day, symbol=listing.symbol, sector=listing.sector, pattern=match.pattern.value, plan=plan,
                probability=cand.probability_score, ranking_score=cand.ranking_score,
                risk_reward=cand.risk_reward_ratio,
                weekly_aligned=bool(cand.weekly_trend.aligned) if cand.weekly_trend else True,
                liquid=cand.liquidity.passes_filter,
            ))
            # Independent outcome of every signal → calibration data.
            outcome = simulate_trade(frame.iloc[i + 1:], plan)
            if outcome.filled and outcome.resolved:
                samples.append({"ranking_score": cand.ranking_score, "probability": cand.probability_score,
                                "pattern": match.pattern.value, "win": outcome.win,
                                "r": outcome.r_multiple_net})
            i += fill_window  # the same setup persisting for days is one signal, not five

    progress(stage="simulating portfolio", progress=0.9)
    regimes = _regime_series(bench, calendar, breadth_above, breadth_total)
    result = _simulate_portfolio(signals, bars, calendar, regimes, bench, universe)
    result["signal_samples"] = len(samples)
    result["symbols_tested"] = len(bars)
    result["symbols_skipped"] = len(skipped)
    result["skipped_examples"] = [f"{k}: {v}" for k, v in list(skipped.items())[:10]]
    result["universe"] = universe.value
    result["years"] = years
    result["generated_at"] = datetime.now(UTC).isoformat()
    save_artifact(artifact_key(universe), result)

    progress(stage="calibrating scores", progress=0.97)
    ledger_samples = scanner.ledger.resolved_samples() if hasattr(scanner, "ledger") else []
    scanner.calibration.refit(
        [(s["ranking_score"], s["win"]) for s in samples] + ledger_samples,
        source=f"{len(samples)} backtest trades ({universe.value}, {years}y) + {len(ledger_samples)} live ledger trades",
    )
    return result


def _regime_series(bench, calendar, breadth_above, breadth_total) -> dict[pd.Timestamp, str]:
    close = bench["Close"]
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    ret20 = (close / close.shift(20) - 1) * 100
    out: dict[pd.Timestamp, str] = {}
    for day in calendar:
        points = 2 if close[day] > ema200[day] else -2
        points += 1 if close[day] > ema50[day] else -1
        points += 1 if ema50[day] > ema200[day] else -1
        r = ret20.get(day, 0.0)
        points += 1 if r > 2 else -1 if r < -3 else 0
        total = breadth_total.get(day, 0)
        if total >= 20:
            pct = breadth_above[day] / total * 100
            points += 2 if pct >= 60 else -2 if pct < 35 else -1 if pct < 45 else 0
        out[day] = "bull" if points >= 3 else "bear" if points <= -2 else "neutral"
    return out


def _simulate_portfolio(signals, bars, calendar, regimes, bench, universe) -> dict:
    cost_side = settings.trade_round_trip_cost_pct / 2 / 100
    fill_window = settings.trade_fill_window_sessions
    max_hold = settings.trade_max_hold_sessions
    strict_liquidity = universe == ScanUniverse.MID_SMALL_2000_PLUS

    by_day: defaultdict[pd.Timestamp, list[HistSignal]] = defaultdict(list)
    for s in signals:
        by_day[s.day].append(s)

    cash = START_CAPITAL
    positions: list[dict] = []
    pending: list[tuple[HistSignal, int]] = []  # (signal, day index it was created)
    trades: list[dict] = []
    equity_curve: list[tuple[pd.Timestamp, float]] = []
    invested_days = 0

    def bar(symbol, day):
        frame = bars.get(symbol)
        if frame is None or day not in frame.index:
            return None
        return frame.loc[day]

    for d_idx, day in enumerate(calendar):
        # 1) manage open positions
        still_open = []
        for p in positions:
            b = bar(p["symbol"], day)
            if b is None:
                still_open.append(p)
                continue
            p["held"] += 1
            exit_price = reason = None
            if b["Low"] <= p["stop"]:
                exit_price, reason = min(b["Open"], p["stop"]) if p["held"] > 1 else p["stop"], "stop"
            elif b["High"] >= p["target"]:
                exit_price, reason = max(b["Open"], p["target"]) if p["held"] > 1 else p["target"], "target"
            elif p["held"] >= max_hold:
                exit_price, reason = b["Close"], "time"
            if exit_price is None:
                p["last"] = b["Close"]
                still_open.append(p)
                continue
            proceeds = exit_price * p["qty"] * (1 - cost_side)
            cash += proceeds
            pnl = proceeds - p["cost_basis"]
            trades.append({
                "symbol": p["symbol"], "sector": p["sector"], "pattern": p["pattern"], "regime": p["regime"],
                "entry_date": p["entry_date"].date().isoformat(), "exit_date": day.date().isoformat(),
                "entry": round(p["fill"], 2), "exit": round(exit_price, 2), "qty": p["qty"], "reason": reason,
                "pnl": round(pnl, 2), "return_pct": round(pnl / p["cost_basis"] * 100, 2),
                "r": round(pnl / (p["risk_per_share"] * p["qty"]), 2), "sessions": p["held"],
                "probability": p["probability"],
            })
        positions = still_open

        # 2) try to fill pending buy-stop orders
        equity_now = cash + sum(p["qty"] * p.get("last", p["fill"]) for p in positions)
        pending = [(s, created) for s, created in pending if d_idx - created <= fill_window]
        pending.sort(key=lambda item: item[0].ranking_score, reverse=True)
        remaining = []
        for s, created in pending:
            if len(positions) >= MAX_POSITIONS:
                remaining.append((s, created))
                continue
            if any(p["symbol"] == s.symbol for p in positions):
                continue
            if sum(1 for p in positions if p["sector"] == s.sector) >= MAX_PER_SECTOR:
                remaining.append((s, created))
                continue
            b = bar(s.symbol, day)
            if b is None or b["High"] < s.plan.entry:
                remaining.append((s, created))
                continue
            fill = max(b["Open"], s.plan.entry)
            risk_per_share = fill - s.plan.stop
            if risk_per_share <= 0:
                continue
            size_mult = REGIME_THRESHOLDS[regimes[day]][2]
            qty = int(min(
                equity_now * RISK_PCT / 100 * size_mult / risk_per_share,
                equity_now * MAX_POSITION_PCT / 100 / fill,
                cash / (fill * (1 + cost_side)),
            ))
            if qty <= 0:
                continue
            cost_basis = fill * qty * (1 + cost_side)
            cash -= cost_basis
            pos = {
                "symbol": s.symbol, "sector": s.sector, "pattern": s.pattern, "regime": regimes[day],
                "fill": fill, "stop": s.plan.stop, "target": s.plan.target, "qty": qty,
                "cost_basis": cost_basis, "risk_per_share": risk_per_share, "entry_date": day,
                "held": 1, "last": b["Close"], "probability": s.probability,
            }
            # same-bar stop check on the entry day
            if b["Low"] <= s.plan.stop:
                proceeds = s.plan.stop * qty * (1 - cost_side)
                cash += proceeds
                pnl = proceeds - cost_basis
                trades.append({
                    "symbol": s.symbol, "sector": s.sector, "pattern": s.pattern, "regime": regimes[day],
                    "entry_date": day.date().isoformat(), "exit_date": day.date().isoformat(),
                    "entry": round(fill, 2), "exit": s.plan.stop, "qty": qty, "reason": "stop",
                    "pnl": round(pnl, 2), "return_pct": round(pnl / cost_basis * 100, 2),
                    "r": round(pnl / (risk_per_share * qty), 2), "sessions": 1, "probability": s.probability,
                })
            else:
                positions.append(pos)
        pending = remaining

        # 3) today's closing scan → new buy-stop orders for the following sessions
        min_prob, min_rr, _ = REGIME_THRESHOLDS[regimes[day]]
        for s in by_day.get(day, []):
            if s.probability < min_prob or s.risk_reward < min_rr or not s.weekly_aligned:
                continue
            if strict_liquidity and not s.liquid:
                continue
            pending.append((s, d_idx))

        equity = cash + sum(p["qty"] * p.get("last", p["fill"]) for p in positions)
        equity_curve.append((day, equity))
        if positions:
            invested_days += 1

    # close anything still open at the last close (marked, not a real exit)
    final_equity = equity_curve[-1][1] if equity_curve else START_CAPITAL
    return _metrics(trades, equity_curve, final_equity, invested_days, calendar, bench, positions)


def _metrics(trades, equity_curve, final_equity, invested_days, calendar, bench, open_positions) -> dict:
    eq = pd.Series([e for _, e in equity_curve], index=[d for d, _ in equity_curve], dtype=float)
    years = max(len(calendar) / 252, 1e-9)
    final_equity = float(final_equity)
    total_return = float((final_equity / START_CAPITAL - 1) * 100)
    cagr = float(((final_equity / START_CAPITAL) ** (1 / years) - 1) * 100) if final_equity > 0 else -100.0
    running_max = eq.cummax()
    max_dd = float(((eq / running_max) - 1).min() * 100) if len(eq) else 0.0
    bench_close = bench["Close"].reindex(calendar).ffill()
    bench_return = float((bench_close.iloc[-1] / bench_close.iloc[0] - 1) * 100) if len(bench_close) else 0.0

    tdf = pd.DataFrame(trades)
    metrics = {
        "start": calendar[0].date().isoformat(), "end": calendar[-1].date().isoformat(),
        "starting_capital": START_CAPITAL, "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return, 2), "cagr_pct": round(cagr, 2),
        "benchmark_return_pct": round(bench_return, 2), "max_drawdown_pct": round(max_dd, 2),
        "trades": int(len(tdf)), "exposure_pct": round(invested_days / max(len(calendar), 1) * 100, 1),
        "open_positions_at_end": len(open_positions),
        "avg_monthly_return_pct": None,
        "win_rate": None, "average_r": None, "profit_factor": None, "average_sessions": None,
    }
    monthly = eq.resample("ME").last()
    monthly_returns = (monthly / monthly.shift(1).fillna(START_CAPITAL) - 1) * 100
    metrics["avg_monthly_return_pct"] = round(float(monthly_returns.mean()), 2) if len(monthly_returns) else None
    metrics["positive_months_pct"] = round(float((monthly_returns > 0).mean() * 100), 1) if len(monthly_returns) else None

    by_pattern, by_regime = [], []
    if not tdf.empty:
        wins = tdf["pnl"] > 0
        gross_win, gross_loss = tdf.loc[wins, "pnl"].sum(), -tdf.loc[~wins, "pnl"].sum()
        metrics.update(
            win_rate=round(float(wins.mean()), 3), average_r=round(float(tdf["r"].mean()), 3),
            profit_factor=round(float(gross_win / gross_loss), 2) if gross_loss > 0 else None,
            average_sessions=round(float(tdf["sessions"].mean()), 1),
        )
        for col, sink in (("pattern", by_pattern), ("regime", by_regime)):
            for key, sub in tdf.groupby(col):
                w = sub["pnl"] > 0
                sink.append({"key": key, "trades": int(len(sub)), "win_rate": round(float(w.mean()), 3),
                             "average_r": round(float(sub["r"].mean()), 3), "pnl": round(float(sub["pnl"].sum()), 2)})
        by_pattern.sort(key=lambda r: r["trades"], reverse=True)

    step = max(1, len(eq) // 120)
    curve = [{"date": d.date().isoformat(), "equity": round(float(v), 0),
              "benchmark": round(float(START_CAPITAL * bench_close[d] / bench_close.iloc[0]), 0)}
             for d, v in list(eq.items())[::step]]
    return {
        "metrics": metrics,
        "monthly_returns": [{"month": d.strftime("%Y-%m"), "return_pct": round(float(r), 2)} for d, r in monthly_returns.items()],
        "by_pattern": by_pattern, "by_regime": by_regime,
        "equity_curve": curve,
        "trades": trades[-300:],
        "assumptions": [
            f"Starting capital ₹{START_CAPITAL:,.0f}; {RISK_PCT}% risk per trade (scaled by regime), max {MAX_POSITION_PCT:.0f}% per position",
            f"Max {MAX_POSITIONS} positions, {MAX_PER_SECTOR} per sector; buy-stop entries valid {settings.trade_fill_window_sessions} sessions",
            f"Exits: stop, target, or time stop after {settings.trade_max_hold_sessions} sessions; gaps fill at the open",
            f"Costs: {settings.trade_round_trip_cost_pct}% round trip (brokerage, STT, stamp duty, slippage)",
            "Regime thresholds and weekly-trend gate applied as in the live system",
            "Not modelled (no point-in-time history): earnings blackout, fundamentals, sector-peer rank, sector rotation, delivery spikes, bulk deals",
            "Survivorship bias: only currently listed stocks are tested, which flatters results",
        ],
    }
