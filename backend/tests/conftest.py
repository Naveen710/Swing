"""Shared test setup: everything runs offline against the deterministic demo provider.

Settings are read from the environment at import time, so these must be set
before any `app.*` module is imported.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MARKET_DATA_PROVIDER", "demo")
os.environ.setdefault("UNIVERSE_PROVIDER", "bundled_csv")
os.environ.setdefault("ALLOW_DEMO_FALLBACK", "1")
os.environ.setdefault("ENABLE_EXTERNAL_OVERLAYS", "0")
os.environ.setdefault("DATA_CACHE_DIR", tempfile.mkdtemp(prefix="swing-test-cache-"))
os.environ["DATABASE_URL"] = ""          # never touch a real database from tests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest


def make_bars(closes, start="2024-01-01", spread=0.01, volume=1_000_000):
    """Daily OHLCV frame from a list of closes (open = previous close)."""
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(start, periods=len(closes))
    opens = np.r_[closes[0], closes[:-1]]
    highs = np.maximum(opens, closes) * (1 + spread)
    lows = np.minimum(opens, closes) * (1 - spread)
    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes,
                         "Volume": np.full(len(closes), volume)}, index=idx)


def bars(rows, start="2024-01-01"):
    """Frame from explicit (open, high, low, close) tuples."""
    idx = pd.bdate_range(start, periods=len(rows))
    o, h, l, c = zip(*rows)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": [1_000_000] * len(rows)}, index=idx)


@pytest.fixture
def make_bars_fn():
    return make_bars


@pytest.fixture
def bars_fn():
    return bars
