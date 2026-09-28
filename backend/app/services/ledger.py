"""Signal ledger: every pick the scanner publishes is recorded and later graded
against what the market actually did, using the same rules as the backtest."""
from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, date, datetime

import pandas as pd
from sqlalchemy import select

from app.schemas import MarketCapBucket, TradeSetup
from app.services.db import get_engine, signals_table
from app.services.trade_sim import TradePlan, simulate_trade
from app.services.universe import StockListing, find_listing

logger = logging.getLogger(__name__)
OPEN_STATUSES = ("pending", "open")
PROB_BANDS = [(0.0, 0.65, "< 65%"), (0.65, 0.75, "65–75%"), (0.75, 0.85, "75–85%"), (0.85, 1.01, "85%+")]


class SignalLedger:
    def __init__(self, market_data) -> None:
        self.market_data = market_data
        self._eval_lock = threading.Lock()

    # ── recording ──────────────────────────────────────────────────────
    def record(self, setups: list[TradeSetup], universe: str, regime: str | None) -> int:
        rows = [s for s in setups if s.signal_date is not None]
        if not rows:
            return 0
        engine = get_engine()
        inserted = 0
        now = datetime.now(UTC)
        with engine.begin() as conn:
            existing = {
                (r.symbol, r.signal_date, r.pattern)
                for r in conn.execute(
                    select(signals_table.c.symbol, signals_table.c.signal_date, signals_table.c.pattern)
                    .where(signals_table.c.signal_date >= min(s.signal_date for s in rows))
                )
            }
            for s in rows:
                key = (s.symbol, s.signal_date, s.pattern.value)
                if key in existing:
                    continue
                conn.execute(signals_table.insert().values(
                    signal_date=s.signal_date, symbol=s.symbol, company_name=s.company_name,
                    sector=s.sector, universe=universe, pattern=s.pattern.value, regime=regime,
                    entry=s.entry_price, stop=s.stop_loss, target=s.target_price,
                    probability=s.probability_score, ranking_score=s.ranking_score,
                    risk_reward=s.risk_reward_ratio, status="pending", recorded_at=now,
                ))
                existing.add(key)
                inserted += 1
        return inserted

    # ── grading ────────────────────────────────────────────────────────
    def evaluate(self, wait: bool = False) -> dict:
        """Grade every unresolved signal against the bars that followed it.

        wait=True blocks until any background grading finishes, then grades —
        needed by the daily job, whose process exits (killing daemon threads) right after."""
        if not self._eval_lock.acquire(blocking=wait):
            return {"skipped": "evaluation already running"}
        try:
            engine = get_engine()
            with engine.connect() as conn:
                pending = conn.execute(
                    select(signals_table).where(signals_table.c.status.in_(OPEN_STATUSES))
                ).mappings().all()
            updated = 0
            now = datetime.now(UTC)
            for row in pending:
                try:
                    listing = find_listing(row["symbol"]) or StockListing(
                        row["symbol"], row["company_name"] or row["symbol"], row["sector"] or "Unknown", MarketCapBucket.SMALL
                    )
                    frame = self.market_data.get_history(listing, lookback_days=120)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Ledger: no data for %s. %s", row["symbol"], exc)
                    continue
                future = frame[frame.index.date > row["signal_date"]]
                outcome = simulate_trade(future, TradePlan(row["entry"], row["stop"], row["target"]))
                values = {"evaluated_at": now}
                if outcome.status == "open":
                    values["status"] = "open" if outcome.filled else "pending"
                else:
                    values["status"] = outcome.status
                if outcome.filled:
                    values["fill_date"] = future.index[outcome.fill_index].date()
                    values["fill_price"] = outcome.fill_price
                if outcome.exit_index is not None:
                    values.update(
                        exit_date=future.index[outcome.exit_index].date(),
                        exit_price=outcome.exit_price,
                        sessions_held=outcome.sessions_held,
                        return_pct_net=outcome.return_pct_net,
                        r_multiple_net=outcome.r_multiple_net,
                    )
                with engine.begin() as conn:
                    conn.execute(signals_table.update().where(signals_table.c.id == row["id"]).values(**values))
                updated += 1
            return {"checked": len(pending), "updated": updated}
        finally:
            self._eval_lock.release()

    def evaluate_in_background(self) -> None:
        threading.Thread(target=self._safe_evaluate, daemon=True, name="ledger-evaluate").start()

    def _safe_evaluate(self) -> None:
        try:
            self.evaluate()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ledger evaluation failed. %s", exc)

    # ── reporting ──────────────────────────────────────────────────────
    def all_rows(self) -> pd.DataFrame:
        with get_engine().connect() as conn:
            rows = conn.execute(select(signals_table).order_by(signals_table.c.signal_date.desc())).mappings().all()
        return pd.DataFrame([dict(r) for r in rows])

    def resolved_samples(self) -> list[tuple[float, bool]]:
        df = self.all_rows()
        if df.empty:
            return []
        done = df[df["status"].isin(["target", "stop", "time"]) & df["ranking_score"].notna()]
        return [(float(r.ranking_score), bool(r.return_pct_net > 0)) for r in done.itertuples()]

    def summary(self) -> dict:
        df = self.all_rows()
        base = {
            "total_signals": 0, "pending": 0, "open": 0, "expired": 0, "closed": 0,
            "fill_rate": None, "win_rate": None, "average_r": None, "average_return_pct": None,
            "profit_factor": None, "first_signal": None, "last_signal": None,
            "by_pattern": [], "by_regime": [], "by_probability_band": [], "recent": [],
        }
        if df.empty:
            return base
        closed = df[df["status"].isin(["target", "stop", "time"])]
        decided = df[df["status"].isin(["target", "stop", "time", "expired"])]
        base.update(
            total_signals=int(len(df)),
            pending=int((df["status"] == "pending").sum()),
            open=int((df["status"] == "open").sum()),
            expired=int((df["status"] == "expired").sum()),
            closed=int(len(closed)),
            fill_rate=round(len(closed) / len(decided), 3) if len(decided) else None,
            first_signal=str(df["signal_date"].min()),
            last_signal=str(df["signal_date"].max()),
        )
        base.update(_outcome_stats(closed))
        base["by_pattern"] = _grouped(closed, decided, "pattern")
        base["by_regime"] = _grouped(closed, decided, "regime")
        bands = []
        for lo, hi, label in PROB_BANDS:
            sub = closed[(closed["probability"] >= lo) & (closed["probability"] < hi)]
            if len(sub):
                bands.append({"band": label, "predicted": round(float(sub["probability"].mean()), 3),
                              "trades": int(len(sub)), **_outcome_stats(sub)})
        base["by_probability_band"] = bands
        recent = df.head(150).drop(columns=["recorded_at", "evaluated_at"], errors="ignore")
        # to_json turns NaN/NaT into null and numpy scalars into plain JSON values
        base["recent"] = json.loads(recent.to_json(orient="records", date_format="iso"))
        for row in base["recent"]:
            for col in ("signal_date", "fill_date", "exit_date"):
                if row.get(col):
                    row[col] = str(row[col])[:10]
        return base


def _outcome_stats(closed: pd.DataFrame) -> dict:
    if closed.empty:
        return {"win_rate": None, "average_r": None, "average_return_pct": None, "profit_factor": None}
    rets = closed["return_pct_net"].astype(float)
    gains, losses = rets[rets > 0].sum(), -rets[rets <= 0].sum()
    return {
        "win_rate": round(float((rets > 0).mean()), 3),
        "average_r": round(float(closed["r_multiple_net"].astype(float).mean()), 3),
        "average_return_pct": round(float(rets.mean()), 3),
        "profit_factor": round(float(gains / losses), 2) if losses > 0 else None,
    }


def _grouped(closed: pd.DataFrame, decided: pd.DataFrame, column: str) -> list[dict]:
    out = []
    for key, sub in decided.groupby(decided[column].fillna("unknown")):
        c = closed[closed[column].fillna("unknown") == key]
        out.append({"key": str(key), "signals": int(len(sub)), "trades": int(len(c)),
                    "fill_rate": round(len(c) / len(sub), 3) if len(sub) else None, **_outcome_stats(c)})
    return sorted(out, key=lambda r: r["trades"], reverse=True)
