"""Turns the scanner's ranking score into an empirical win rate.

The heuristic confidence number saturates (many picks show 95%) and was never
checked against outcomes. Isotonic regression learns a monotonic map
"ranking score → share of trades that actually made money" from resolved
backtest and ledger trades, so '62%' means 62% of similar setups won.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime

import numpy as np

from app.services.db import load_artifact, save_artifact

logger = logging.getLogger(__name__)
ARTIFACT_KEY = "calibration"
MIN_SAMPLES = 60
MIN_BIN = 30      # never estimate a win rate from fewer trades than this
SHRINK = 20       # pull thin bins toward the overall win rate
RELOAD_SECONDS = 600


def fit_calibration(samples: list[tuple[float, bool]], source: str) -> dict | None:
    if len(samples) < MIN_SAMPLES:
        return None
    from sklearn.isotonic import IsotonicRegression

    x = np.array([s for s, _ in samples], dtype=float)
    y = np.array([1.0 if w else 0.0 for _, w in samples])
    base = float(y.mean())

    # Fit on binned, shrunk win rates rather than raw 0/1 points: raw isotonic
    # overfits the few samples at the extremes (e.g. "100% win rate").
    order = np.argsort(x)
    bin_size = max(MIN_BIN, len(x) // 20)
    centers, rates, weights = [], [], []
    for start in range(0, len(x), bin_size):
        idx = order[start:start + bin_size]
        if len(idx) < MIN_BIN // 2 and centers:
            break
        n = len(idx)
        centers.append(float(x[idx].mean()))
        rates.append((float(y[idx].sum()) + SHRINK * base) / (n + SHRINK))
        weights.append(n)
    model = IsotonicRegression(increasing=True, out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(
        np.array(centers), np.array(rates), sample_weight=np.array(weights)
    )

    quantiles = np.quantile(x, [0, 0.2, 0.4, 0.6, 0.8, 1.0])
    bands = []
    for i in range(5):
        lo, hi = quantiles[i], quantiles[i + 1]
        mask = (x >= lo) & ((x <= hi) if i == 4 else (x < hi))
        if mask.sum():
            bands.append({
                "band": f"Q{i + 1}", "score_from": round(float(lo), 3), "score_to": round(float(hi), 3),
                "trades": int(mask.sum()), "win_rate": round(float(y[mask].mean()), 3),
            })
    # Does the score really separate winners from losers? Compare the top 40% of
    # scores with the bottom 40% using a two-proportion z-test. ~33 trades per
    # band swing ±9 points by chance alone, so eyeballing the bands misleads.
    cut = max(1, int(len(x) * 0.4))
    low, high = y[order[:cut]], y[order[-cut:]]
    p_low, p_high = float(low.mean()), float(high.mean())
    pooled = float(np.concatenate([low, high]).mean())
    se = (pooled * (1 - pooled) * (2 / cut)) ** 0.5
    z = (p_high - p_low) / se if se > 0 else 0.0

    return {
        "low_score_win_rate": round(p_low, 3),
        "high_score_win_rate": round(p_high, 3),
        "z_score": round(z, 2),
        "significant": bool(z >= 1.96),
        "x": [round(float(v), 4) for v in model.X_thresholds_],
        "y": [round(float(v), 4) for v in model.y_thresholds_],
        "samples": int(len(samples)),
        "base_win_rate": round(base, 3),
        "spread": round(float(max(rates) - min(rates)), 3),
        "bands": bands,
        "source": source,
        "fitted_at": datetime.now(UTC).isoformat(),
    }


class Calibrator:
    def __init__(self) -> None:
        self._model: dict | None = None
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def _current(self) -> dict | None:
        with self._lock:
            if time.time() - self._loaded_at > RELOAD_SECONDS:
                try:
                    self._model = load_artifact(ARTIFACT_KEY)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Calibration unavailable. %s", exc)
                    self._model = None
                self._loaded_at = time.time()
            return self._model

    def predict(self, ranking_score: float) -> tuple[float | None, int | None]:
        model = self._current()
        if not model or not model.get("x"):
            return None, None
        return round(float(np.interp(ranking_score, model["x"], model["y"])), 3), model["samples"]

    def refit(self, samples: list[tuple[float, bool]], source: str) -> dict | None:
        model = fit_calibration(samples, source)
        if model is not None:
            save_artifact(ARTIFACT_KEY, model)
            with self._lock:
                self._model, self._loaded_at = model, time.time()
        return model

    def info(self) -> dict | None:
        return self._current()
