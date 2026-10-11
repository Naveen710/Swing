"""Multibagger framework service: universe run, single-stock check, backtest job, forward ledger.

Spec: docs/features/multibagger-framework/spec.md. One multibagger job (run or backtest)
at a time, in a daemon thread, with progress in `state` — the Render free tier has 512 MB.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.config import settings
from app.schemas import MarketCapBucket, ScanUniverse
from app.services import multibagger_rules as mb
from app.services.curated_algo import _fetch_surveillance, _index_frame, load_panel
from app.services.db import get_engine, load_artifact, multibagger_snapshots_table as snaps, save_artifact
from app.services.multibagger_backtest import run_backtest
from app.services.multibagger_data import get_deep_financials
from app.services.trend_overlays import build_peer_ranks, build_sector_rotation, trailing_returns
from app.services.universe import StockListing, find_listing, load_universe

logger = logging.getLogger(__name__)

PANEL_ROWS = 520
MIN_PRICE = 20.0
MIN_VALUE = 1e7
MIN_HISTORY = 500
MAX_CIRCUIT_DAYS = 2
HORIZON_MONTHS = (6, 12, 24, 36)
DISCLAIMER = ("Educational research tool, not investment advice. A high score means a stock matches these rules today, "
              "not that it will rise. Do your own research.")


def is_demo() -> bool:
    return settings.market_data_provider == "demo" or not settings.enable_external_overlays


def _naive(frame: pd.DataFrame | pd.Series):
    out = frame.copy()
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    return out


def rs_raw(close: pd.Series, bench: pd.Series) -> float | None:
    """0.5 × (6-month return − index) + 0.5 × (12-1 month return − index)."""
    c = close.dropna()
    b = bench.reindex(c.index).ffill()
    if len(c) < 253 or b.isna().iloc[-253:].any():
        return None
    r6 = c.iloc[-1] / c.iloc[-127] - 1 - (b.iloc[-1] / b.iloc[-127] - 1)
    r12 = c.iloc[-22] / c.iloc[-253] - 1 - (b.iloc[-22] / b.iloc[-253] - 1)
    return float(0.5 * r6 + 0.5 * r12)


def pinned_days(frame: pd.DataFrame, sessions: int = 60) -> int:
    """Circuit-like days, as in curated_algo: |return| ≥ 4.9% and close at the day's high/low."""
    f = frame.tail(sessions + 1)
    c, h, l = f["Close"], f["High"], f["Low"]
    ret = c / c.shift(1) - 1
    up = (ret >= 0.049) & ((h - c).abs() <= 0.0005 * c)
    dn = (ret <= -0.049) & ((c - l).abs() <= 0.0005 * c)
    return int((up | dn).tail(sessions).sum())


def prefilter(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame, vol: pd.DataFrame,
              buckets: dict[str, MarketCapBucket], exclude: set[str], surveillance: set[str] | None
              ) -> tuple[list[str], dict[str, int], int]:
    """Price-only gate before any fundamentals call. Returns (survivors, eliminated_by, after_size_bucket)."""
    eliminated: dict[str, int] = {}
    survivors: list[str] = []
    after_bucket = 0
    for sym in close.columns:
        if buckets.get(sym) == MarketCapBucket.LARGE:
            code = "large_cap"
        else:
            after_bucket += 1
            c = close[sym].dropna()
            value = (close[sym] * vol[sym]).dropna().tail(60).median()
            frame = pd.DataFrame({"Close": close[sym], "High": high[sym], "Low": low[sym]}).dropna()
            base = sym.removesuffix(".NS")
            if c.empty or float(c.iloc[-1]) < MIN_PRICE:
                code = "penny"
            elif not value >= MIN_VALUE:
                code = "illiquid"
            elif len(c) < MIN_HISTORY:
                code = "short_history"
            elif pinned_days(frame) > MAX_CIRCUIT_DAYS:
                code = "circuit"
            elif base in exclude or (surveillance is not None and base in surveillance):
                code = "asm_gsm"
            else:
                survivors.append(sym)
                continue
        eliminated[code] = eliminated.get(code, 0) + 1
    return survivors, eliminated, after_bucket


class MultibaggerService:
    def __init__(self, scanner) -> None:
        self.scanner = scanner
        self._lock = threading.Lock()
        self.state: dict = {"running": False, "job": None, "stage": "idle", "progress": 0}
        self._last_eval = 0.0
        self._grading = False
        self._ref_cache: tuple[float, dict | None] = (0.0, None)

    # ── jobs ────────────────────────────────────────────────────────────────
    def _claim(self, job: str, universe: ScanUniverse) -> bool:
        with self._lock:
            if self.state.get("running"):
                return False
            self.state = {"running": True, "job": job, "stage": "loading prices", "progress": 0,
                          "universe": universe.value, "started_at": datetime.now(UTC).isoformat()}
            return True

    def _update(self, **kw) -> None:
        with self._lock:
            self.state = {**self.state, **kw}      # replace, never mutate a dict a reader may hold

    def get_state(self) -> dict:
        with self._lock:
            return dict(self.state)

    def start_run(self, universe: ScanUniverse, exclude: set[str], refresh: bool) -> bool:
        if not self._claim("run", universe):
            return False
        threading.Thread(target=self._job_run, args=(universe, exclude, refresh), daemon=True, name="multibagger-run").start()
        return True

    def _job_run(self, universe, exclude, refresh) -> None:
        try:
            self.run(universe, exclude, refresh)
            self._update(running=False, stage="done", progress=100, finished_at=datetime.now(UTC).isoformat())
        except Exception as exc:  # noqa: BLE001
            logger.exception("Multibagger run failed")
            self._update(running=False, stage="error", error=str(exc), finished_at=datetime.now(UTC).isoformat())

    def start_backtest(self, universe: ScanUniverse, start: str) -> bool:
        if not self._claim("backtest", universe):
            return False
        threading.Thread(target=self._job_backtest, args=(universe, start), daemon=True, name="multibagger-bt").start()
        return True

    def _job_backtest(self, universe, start) -> None:
        try:
            self.backtest(universe, start)
            self._update(running=False, stage="done", progress=100, finished_at=datetime.now(UTC).isoformat())
        except Exception as exc:  # noqa: BLE001
            logger.exception("Multibagger backtest failed")
            self._update(running=False, stage="error", error=str(exc), finished_at=datetime.now(UTC).isoformat())

    # ── universe run ────────────────────────────────────────────────────────
    def run(self, universe: ScanUniverse, exclude: set[str] | None = None, refresh: bool = False) -> dict:
        exclude = {s.upper().removesuffix(".NS") for s in (exclude or set())}
        listings = load_universe(universe=universe)
        by_symbol = {l.symbol: l for l in listings}
        p = load_panel(self.scanner, universe, rows=PANEL_ROWS,
                       progress=lambda d, n: self._update(progress=round(d / n * 30)))
        self._update(stage="pre-filter", progress=32)
        bench = _naive(p.index_close)
        close, high, low, vol = (_naive(x) for x in (p.close, p.high, p.low, p.volume))
        demo = is_demo()
        surveillance, surv_note = (None, "demo") if demo else _fetch_surveillance()

        notes: list[str] = []
        buckets = {sym: l.market_cap_bucket for sym, l in by_symbol.items()}
        survivors, eliminated, after_bucket = prefilter(close, high, low, vol, buckets, exclude, surveillance)
        missing = len(listings) - len(close.columns)
        if missing > 0:
            eliminated["short_history"] = eliminated.get("short_history", 0) + missing

        def drop(code: str) -> None:
            eliminated[code] = eliminated.get(code, 0) + 1

        if surveillance is None and not demo:
            notes.append("ASM/GSM lists are not configured (ASM_GSM_URLS), so only your exclusion list was applied.")

        # ranking (cross-sectional tailwind inputs)
        self._update(stage="ranking", progress=36)
        rs = {s: rs_raw(close[s], bench) for s in survivors}
        rs = {s: v for s, v in rs.items() if v is not None}
        survivors = [s for s in survivors if s in rs]
        sectors = {s: (by_symbol[s].sector if s in by_symbol else p.sectors.get(s, "Unknown")) for s in survivors}
        bench_ret = trailing_returns(bench.dropna())
        stock_ret = {}
        for s in survivors:
            tr = trailing_returns(close[s].dropna())
            if tr:
                stock_ret[s] = (sectors[s], tr[0], tr[1])
        rotation = build_sector_rotation(stock_ret, bench_ret) if bench_ret else {}
        known = {s: (sectors[s], rs[s]) for s in survivors if sectors[s] not in ("Unknown", "Benchmark") and sectors[s] in rotation}
        peers = build_peer_ranks(known, list(known))
        rs_series = pd.Series(rs)
        rs_pctl = rs_series.rank(pct=True)
        dist = pd.Series({s: 1 - float(close[s].dropna().iloc[-1]) / float(high[s].dropna().tail(252).max()) for s in survivors})
        prerank = 0.5 * rs_pctl + 0.5 * (1 - dist.rank(pct=True))
        order = sorted(survivors, key=lambda s: (-float(prerank.get(s, 0)), s))
        deep_syms = order[: settings.multibagger_deep_cap]
        breakpoints = [round(float(x), 6) for x in np.quantile(rs_series.values, np.linspace(0, 1, 21))] if len(rs_series) else []

        # fundamentals (slow part)
        self._update(stage="fundamentals", progress=40)
        deep: dict[str, dict] = {}
        done = 0

        def fetch(sym):
            return sym, get_deep_financials(sym, demo=demo, refresh=refresh, fundamentals=self.scanner.fundamentals)

        with ThreadPoolExecutor(max_workers=max(1, settings.multibagger_fund_workers)) as pool:
            for sym, data in pool.map(fetch, deep_syms):
                deep[sym] = data
                done += 1
                self._update(progress=40 + round(done / max(1, len(deep_syms)) * 50), detail=f"{done}/{len(deep_syms)} stocks")

        # scoring
        self._update(stage="scoring", progress=92)
        results, excluded, insufficient = [], [], []
        opens = _naive(p.open)
        for sym in deep_syms:
            frame = pd.DataFrame({"Open": opens[sym], "High": high[sym], "Low": low[sym], "Close": close[sym], "Volume": vol[sym]}).dropna(subset=["Close"])
            listing = by_symbol.get(sym)
            sec = sectors[sym]
            rot = rotation.get(sec)
            row = mb.evaluate_stock(
                symbol=sym, company_name=listing.company_name if listing else p.names.get(sym, sym), listing_sector=sec,
                frame=frame, bench=bench, deep=deep[sym],
                sector_rank=(rot.rank, rot.sector_count) if rot else None,
                rs_pctl=float(rs_pctl[sym]), peer_pctl=peers[sym].percentile if sym in peers else None)
            status = row.pop("status")
            if status == "scored":
                results.append(row)
            elif status == "excluded":
                excluded.append(row)
                drop(row["code"])
            else:
                insufficient.append(row)
                drop("insufficient_data")
        results.sort(key=mb.sort_key)
        for i, r in enumerate(results, start=1):
            r["rank"] = i
        prev = self.previous_tiers(universe.value, str(close.index[-1].date())[:7])
        for r in results:
            if prev.get(r["symbol"]) == "D" and r["tier"] == "D":
                r["thesis_check"].append({"code": "LEDGER", "status": "warning",
                                          "text": "Tier D in two consecutive monthly snapshots — review the thesis"})

        out = {
            "available": True, "universe": universe.value,
            "as_of": str(close.index[-1].date()), "generated_at": datetime.now(UTC).isoformat(), "demo": demo,
            "funnel": {"universe_count": len(listings), "after_size_bucket": after_bucket, "after_prefilter": len(survivors),
                       "deep_pass": len(deep_syms), "not_deep_passed": max(0, len(survivors) - len(deep_syms)),
                       "passed_kill_switches": len(results) + sum(1 for r in insufficient if r.get("stage") == "coverage"),
                       "tiered": len(results),
                       "insufficient": len(insufficient)},
            "eliminated_by": eliminated, "weights": dict(mb.WEIGHTS), "rules": mb.RULES_TEXT,
            "notes": notes + ([f"Pre-filter survivors beyond the top {settings.multibagger_deep_cap} by momentum were not checked fundamentally — use 'Check any stock' for them."]
                              if len(survivors) > len(deep_syms) else [])
                     + (["Demo mode: fundamentals are generated, not real."] if demo else []),
            "disclaimer": DISCLAIMER,
            "sectors": [{"sector": s.sector, "rank": s.rank, "count": s.sector_count, "score": s.score} for s in sorted(rotation.values(), key=lambda x: x.rank)],
            "rs_breakpoints": breakpoints,
            "results": results, "excluded": excluded, "insufficient": insufficient,
        }
        self._update(stage="saving", progress=97)
        fetch_failed = sum(1 for r in insufficient if r.get("stage") == "fetch")
        skip = None
        if demo:
            skip = "demo data is never recorded in the forward ledger"
        elif exclude:
            skip = "runs with your own exclusion list are not recorded (the ledger keeps the default screen)"
        elif not results:
            skip = "nothing was scored"
        elif deep_syms and fetch_failed > 0.2 * len(deep_syms):
            skip = f"{fetch_failed} of {len(deep_syms)} fundamentals fetches failed — will retry on the next run"
        if skip:
            out["snapshot"] = {"month": out["as_of"][:7], "recorded": False, "reason": skip}
        else:
            try:
                out["snapshot"] = self.record_snapshot(out)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Multibagger snapshot failed. %s", exc)
                out["snapshot"] = {"month": out["as_of"][:7], "recorded": False, "error": str(exc)}
        save_artifact(f"multibagger:{universe.value}", out)
        return out

    def latest(self, universe: ScanUniverse) -> dict | None:
        return load_artifact(f"multibagger:{universe.value}")

    # ── single stock ────────────────────────────────────────────────────────
    def stock(self, symbol: str) -> dict | None:
        sym = symbol.strip().upper()
        if not sym.endswith(".NS") and not sym.startswith("^"):
            sym += ".NS"
        listing = find_listing(sym) or StockListing(sym, sym.removesuffix(".NS"), "Unknown", MarketCapBucket.SMALL)
        try:
            frame = _naive(self.scanner.market_data.get_history(listing, lookback_days=PANEL_ROWS)).dropna(subset=["Close"])
        except Exception:  # noqa: BLE001
            return None
        if frame.empty or len(frame) < 60:
            return None
        try:
            bench_close, _ = _index_frame(self.scanner, PANEL_ROWS)
            bench = _naive(bench_close)
        except Exception:  # noqa: BLE001
            bench = None
        ref = self._reference_run()
        no_ref = ref is None
        rs_p = sector_rank = None
        notes = []
        if ref:
            raw = rs_raw(frame["Close"], bench) if bench is not None else None
            bp = ref.get("rs_breakpoints") or []
            if raw is not None and len(bp) >= 2:
                rs_p = float(np.interp(raw, bp, np.linspace(0, 1, len(bp))))
            sec = next((s for s in ref.get("sectors", []) if s["sector"] == listing.sector), None)
            if sec:
                sector_rank = (sec["rank"], sec["count"])
            notes.append(f"Tailwind ranked against the latest {ref['universe']} run ({ref['as_of']}). Peer rank not available for a single stock — scored neutral.")
        if listing.market_cap_bucket == MarketCapBucket.LARGE:
            notes.append("Nifty 100 stock — the universe screen skips large caps; scored here for reference.")
        demo = is_demo()
        deep = get_deep_financials(sym, demo=demo, fundamentals=self.scanner.fundamentals)
        row = mb.evaluate_stock(symbol=sym, company_name=listing.company_name, listing_sector=listing.sector,
                                frame=frame, bench=bench, deep=deep, sector_rank=sector_rank, rs_pctl=rs_p,
                                peer_pctl=None, no_reference=no_ref)
        row["notes"] = notes + (["Demo mode: fundamentals are generated, not real."] if demo else [])
        row["demo"] = demo
        row["disclaimer"] = DISCLAIMER
        return row

    def _reference_run(self) -> dict | None:
        ts, ref = self._ref_cache
        if ref is not None and time.time() - ts < 600:
            return ref
        ref = self.latest(ScanUniverse.NIFTY500)
        if not ref:
            for u in ScanUniverse:
                ref = self.latest(u)
                if ref:
                    break
        self._ref_cache = (time.time(), ref)
        return ref

    # ── backtest ────────────────────────────────────────────────────────────
    def backtest(self, universe: ScanUniverse, start: str = "2015-01-01") -> dict:
        rows = int((date.today() - date.fromisoformat(start)).days * 252 / 365) + 300
        # Nifty 100 names are dropped before loading and every universe is capped (evenly sampled)
        # at MULTIBAGGER_BACKTEST_MAX_SYMBOLS so a 10-year panel fits in Render's 512 MB.
        listings = [l for l in load_universe(universe=universe) if l.market_cap_bucket != MarketCapBucket.LARGE]
        cap = settings.multibagger_backtest_max_symbols
        if len(listings) > cap:     # even sample, but always keep the hindsight names
            keep = [l for l in listings if l.symbol in settings.multibagger_hindsight_symbols]
            rest = [l for l in listings if l.symbol not in settings.multibagger_hindsight_symbols]
            n = max(0, cap - len(keep))
            step = len(rest) / n if n else 1
            listings = keep + [rest[int(i * step)] for i in range(n)]
        p = load_panel(self.scanner, universe, rows=rows, max_symbols=None,
                       progress=lambda d, n: self._update(progress=round(d / n * 50)), listings=listings)
        self._update(stage="scoring months", progress=55)
        result = run_backtest(p, universe.value, start=start, excluded=None,
                              hindsight=settings.multibagger_hindsight_symbols,
                              progress=lambda pct: self._update(progress=pct))
        result["run_at"] = datetime.now(UTC).isoformat()
        result["demo"] = is_demo()
        save_artifact(f"multibagger_bt:{universe.value}", result)
        return result

    def latest_backtest(self, universe: ScanUniverse) -> dict | None:
        return load_artifact(f"multibagger_bt:{universe.value}")

    # ── forward ledger ──────────────────────────────────────────────────────
    def record_snapshot(self, result: dict) -> dict:
        month = result["as_of"][:7]
        universe = result["universe"]
        engine = get_engine()
        with engine.begin() as conn:
            exists = conn.execute(select(snaps.c.id).where((snaps.c.snapshot_month == month) & (snaps.c.universe == universe)).limit(1)).first()
            if exists:
                return {"month": month, "recorded": False, "reason": "already recorded this month"}
            now = datetime.now(UTC)
            snap_date = date.fromisoformat(result["as_of"])
            rows = []
            for r in result["results"]:
                L = r["layers"]
                rows.append({"snapshot_month": month, "snapshot_date": snap_date, "universe": universe, "symbol": r["symbol"],
                             "company_name": r["company_name"][:200], "sector": (r.get("sector") or "")[:120], "rank": r["rank"],
                             "score": r["score"], "tier": r["tier"], "timing": r["timing"], "price": r["price"],
                             "layer_growth": L["growth"]["score"], "layer_quality": L["quality"]["score"],
                             "layer_valuation": L["valuation"]["score"], "layer_tailwind": L["tailwind"]["score"],
                             "layer_price": L["price"]["score"], "coverage_pct": r["coverage_pct"], "recorded_at": now})
            for r in result["excluded"] + result["insufficient"]:
                rows.append({"snapshot_month": month, "snapshot_date": snap_date, "universe": universe, "symbol": r["symbol"],
                             "company_name": (r.get("company_name") or "")[:200], "sector": (r.get("sector") or "")[:120],
                             "tier": "X", "price": r.get("price"), "recorded_at": now})
            cols = ("rank", "score", "timing", "layer_growth", "layer_quality", "layer_valuation", "layer_tailwind",
                    "layer_price", "coverage_pct", "price")
            rows = [{**{c: None for c in cols}, **row} for row in rows]   # executemany needs identical keys
            if rows:
                conn.execute(snaps.insert(), rows)
        return {"month": month, "recorded": True, "rows": len(rows)}

    def previous_tiers(self, universe: str, month: str) -> dict[str, str]:
        """Tiers from the most recent snapshot before `month` (for the two-months-in-D warning)."""
        try:
            with get_engine().connect() as conn:
                last = conn.execute(select(snaps.c.snapshot_month).where((snaps.c.universe == universe) & (snaps.c.snapshot_month < month))
                                    .order_by(snaps.c.snapshot_month.desc()).limit(1)).scalar()
                if not last:
                    return {}
                rows = conn.execute(select(snaps.c.symbol, snaps.c.tier).where((snaps.c.universe == universe) & (snaps.c.snapshot_month == last))).all()
            return {sym: tier for sym, tier in rows}
        except Exception:  # noqa: BLE001
            return {}

    def has_snapshot(self, universe: ScanUniverse, month: str) -> bool:
        with get_engine().connect() as conn:
            return conn.execute(select(snaps.c.id).where((snaps.c.snapshot_month == month) & (snaps.c.universe == universe.value)).limit(1)).first() is not None

    def evaluate_ledger(self) -> dict:
        engine = get_engine()
        with engine.connect() as conn:
            pending = conn.execute(select(snaps).where(snaps.c.ret_36m.is_(None))).mappings().all()
        today = pd.Timestamp(date.today())

        def next_due(r) -> bool:
            for h in HORIZON_MONTHS:
                if r[f"ret_{h}m"] is None:
                    return pd.Timestamp(r["snapshot_date"]) + pd.DateOffset(months=h) <= today
            return False

        due = [r for r in pending if next_due(r)]
        if not due:
            self._last_eval = time.time()
            return {"checked": 0, "updated": 0}
        oldest = min(pd.Timestamp(r["snapshot_date"]) for r in due)
        lookback = int((today - oldest).days * 252 / 365) + 30
        try:
            bench_close, _ = _index_frame(self.scanner, lookback)
            bench = _naive(bench_close).dropna()
        except Exception:  # noqa: BLE001
            bench = None
        cache: dict[str, pd.Series | None] = {}
        updated = 0
        for r in due:
            sym = r["symbol"]
            if sym not in cache:
                listing = find_listing(sym) or StockListing(sym, sym, "Unknown", MarketCapBucket.SMALL)
                try:
                    cache[sym] = _naive(self.scanner.market_data.get_history(listing, lookback_days=lookback))["Close"].dropna()
                except Exception:  # noqa: BLE001
                    cache[sym] = None
            series = cache[sym]
            if series is None or series.empty:
                continue
            snap = pd.Timestamp(r["snapshot_date"])
            after = series[series.index > snap]
            if after.empty:
                continue
            entry_date, entry_price = after.index[0], float(after.iloc[0])
            values = {"entry_date": entry_date.date(), "entry_price": entry_price}
            for h in HORIZON_MONTHS:
                if r[f"ret_{h}m"] is not None:
                    continue
                target = snap + pd.DateOffset(months=h)
                later = series[series.index >= target]
                if later.empty:
                    continue
                values[f"ret_{h}m"] = round((float(later.iloc[0]) / entry_price - 1) * 100, 2)
                if bench is not None:
                    b_after, b_later = bench[bench.index > snap], bench[bench.index >= target]
                    if not b_after.empty and not b_later.empty:
                        values[f"bench_{h}m"] = round((float(b_later.iloc[0]) / float(b_after.iloc[0]) - 1) * 100, 2)
            values["evaluated_at"] = datetime.now(UTC)
            with engine.begin() as conn:
                conn.execute(snaps.update().where(snaps.c.id == r["id"]).values(**values))
            updated += 1
        self._last_eval = time.time()
        return {"checked": len(due), "updated": updated}

    def _evaluate_in_background(self) -> None:
        with self._lock:
            if self._grading or time.time() - self._last_eval < 3600:
                return
            self._grading = True

        def work():
            try:
                self.evaluate_ledger()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Multibagger ledger evaluation failed. %s", exc)
            finally:
                self._last_eval = time.time()
                self._grading = False

        threading.Thread(target=work, daemon=True, name="multibagger-ledger").start()

    def ledger(self, universe: ScanUniverse) -> dict:
        self._evaluate_in_background()     # never block the request; the daily cron also grades
        with get_engine().connect() as conn:
            rows = conn.execute(select(snaps).where(snaps.c.universe == universe.value)).mappings().all()
        frame = pd.DataFrame([dict(r) for r in rows])
        if frame.empty:
            return {"universe": universe.value, "started": None, "snapshots": [], "decision": self._decision([])}
        out = []
        for month, g in frame.groupby("snapshot_month"):
            entry = {"month": month, "stocks": int(len(g)), "tiers": {t: int(n) for t, n in g["tier"].value_counts().items()}, "horizons": {}}
            for h in HORIZON_MONTHS:
                col = f"ret_{h}m"
                done = g[g[col].notna()]
                if done.empty:
                    entry["horizons"][f"{h}m"] = None
                    continue

                def med(tiers):
                    s = done[done["tier"].isin(tiers)][col]
                    return round(float(s.median()), 2) if len(s) else None

                scored = done[done["tier"].isin(list("ABCD"))]
                ic = None
                if len(scored) >= 10:
                    ic = round(float(scored["score"].rank().corr(scored[col].rank())), 3)
                ab, ctrl = med(["A", "B"]), med(["X", "D"])
                entry["horizons"][f"{h}m"] = {
                    "ab": ab, "c": med(["C"]), "d": med(["D"]), "x": med(["X"]), "ic": ic,
                    "ab_minus_control": None if ab is None or ctrl is None else round(ab - ctrl, 2),
                    "nifty": round(float(done[f"bench_{h}m"].median()), 2) if done[f"bench_{h}m"].notna().any() else None,
                }
            out.append(entry)
        out.sort(key=lambda e: e["month"], reverse=True)
        return {"universe": universe.value, "started": min(e["month"] for e in out), "snapshots": out, "decision": self._decision(out)}

    @staticmethod
    def _decision(snapshots: list[dict]) -> dict:
        graded = [s["horizons"].get("24m") for s in snapshots if s["horizons"].get("24m")]
        if len(graded) < 12:
            started = min((s["month"] for s in snapshots), default=None)
            return {"verdict": "PENDING", "graded_24m": len(graded),
                    "text": f"Not yet validated — ledger started {started}" if started else "Not yet validated — no snapshot recorded yet"}
        excess = [g["ab_minus_control"] for g in graded if g["ab_minus_control"] is not None]
        ics = [g["ic"] for g in graded if g["ic"] is not None]
        med_ex = float(np.median(excess)) if excess else None
        mean_ic = float(np.mean(ics)) if ics else None
        ok = med_ex is not None and med_ex >= 10 and mean_ic is not None and mean_ic >= 0.05
        return {"verdict": "PASS" if ok else "FAIL", "graded_24m": len(graded),
                "median_ab_minus_control_24m": None if med_ex is None else round(med_ex, 2),
                "mean_ic_24m": None if mean_ic is None else round(mean_ic, 3),
                "text": "Forward test: tiers A+B beat the low-score control by ≥ 10 pp at 24 months" if ok
                else "Forward test failed the 24-month thresholds — fundamental weights to be revisited (one documented change)."}
