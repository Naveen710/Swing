"""NSE bulk and block deals (large single-client trades disclosed daily).

NSE publishes the latest session's deals as CSV files in its public archive. Each
download is cached by trade date, so the last ~10 sessions build up over time. If
the archive is unreachable (NSE sometimes blocks cloud servers) everything degrades
to "unavailable" and no signal is changed.
"""
from __future__ import annotations

import io
import json
import logging
import threading
import time
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd
import requests

from app.config import settings

logger = logging.getLogger(__name__)
SOURCES = {
    "bulk": "https://archives.nseindia.com/content/equities/bulk.csv",
    "block": "https://archives.nseindia.com/content/equities/block.csv",
}
REFRESH_SECONDS = 6 * 3600
HISTORY_DAYS = 14


class BulkDealProvider:
    def __init__(self) -> None:
        self.dir = settings.cache_dir / "bulk-deals"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last_refresh = 0.0
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def refresh(self) -> None:
        with self._lock:
            if time.time() - self._last_refresh < REFRESH_SECONDS:
                return
            self._last_refresh = time.time()
        got_any = False
        for kind, url in SOURCES.items():
            try:
                resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv"})
                if resp.status_code != 200 or not resp.text.strip():
                    continue
                for trade_date, rows in parse_deals_csv(resp.text).items():
                    (self.dir / f"{kind}_{trade_date.isoformat()}.json").write_text(json.dumps(rows))
                got_any = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Bulk/block deals unavailable (%s). %s", kind, exc)
        self._available = got_any or any(self.dir.glob("*.json"))

    def summary_for(self, symbol: str, days: int = 10) -> dict:
        base = symbol.upper().removesuffix(".NS").removesuffix(".BO")
        cutoff = date.today() - timedelta(days=days + 4)
        buys = sells = 0
        net = 0.0
        for path in self.dir.glob("*.json"):
            try:
                trade_date = date.fromisoformat(path.stem.split("_", 1)[1])
            except ValueError:
                continue
            if trade_date < cutoff:
                continue
            for row in json.loads(path.read_text()):
                if row["symbol"] != base:
                    continue
                if row["side"] == "BUY":
                    buys += 1
                    net += row["qty"]
                else:
                    sells += 1
                    net -= row["qty"]
        return {"buys": buys, "sells": sells, "net_qty": net,
                "source": "nse_archive" if self._available else "unavailable"}

    def prune(self) -> None:
        cutoff = date.today() - timedelta(days=HISTORY_DAYS)
        for path in self.dir.glob("*.json"):
            try:
                if date.fromisoformat(path.stem.split("_", 1)[1]) < cutoff:
                    path.unlink()
            except ValueError:
                continue


def parse_deals_csv(text: str) -> dict[date, list[dict]]:
    """Tolerant parser: NSE has renamed these columns over the years."""
    frame = pd.read_csv(io.StringIO(text))
    cols = {c: c.strip().lower() for c in frame.columns}
    frame = frame.rename(columns=cols)

    def find(*needles: str) -> str | None:
        for c in frame.columns:
            if all(n in c for n in needles):
                return c
        return None

    c_date, c_sym = find("date"), find("symbol")
    c_side = find("buy") or find("sell")
    c_qty = find("quantity") or find("qty")
    if not all([c_date, c_sym, c_side, c_qty]):
        raise ValueError(f"Unrecognised deals file columns: {list(frame.columns)}")

    out: dict[date, list[dict]] = defaultdict(list)
    for row in frame.itertuples(index=False):
        values = dict(zip(frame.columns, row))
        try:
            trade_date = pd.to_datetime(str(values[c_date]).strip(), dayfirst=True).date()
            qty = float(str(values[c_qty]).replace(",", "").strip())
        except (ValueError, TypeError):
            continue
        side = "BUY" if str(values[c_side]).strip().upper().startswith("B") else "SELL"
        out[trade_date].append({"symbol": str(values[c_sym]).strip().upper(), "side": side, "qty": qty})
    return dict(out)
