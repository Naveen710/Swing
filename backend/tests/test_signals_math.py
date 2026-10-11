"""Stops, grades, data-quality guard and VCP building blocks."""
from datetime import timedelta

import numpy as np
import pytest

from app.services.curated_algo import stop_for
from app.services.data_quality import data_quality_issue
from app.services.stock_analysis import grade_for
from app.services.vcp_scanner import trend_template, vcp_stop
from tests.conftest import make_bars


@pytest.mark.parametrize("score,grade", [(85, "A"), (80, "A"), (70, "B"), (55, "C"), (40, "D"), (10, "E")])
def test_grades(score, grade):
    assert grade_for(score)[0] == grade


def test_vcp_stop_two_atr_capped_at_ten_pct():
    assert vcp_stop(100, 3) == 94.0
    assert vcp_stop(100, 8) == 90.0


def test_curated_stop_takes_tighter_and_stays_below_close():
    assert stop_for(100, 2, 98) == 98          # swing low tighter than 1.5×ATR (97)
    assert stop_for(100, 2, 90) == 97          # ATR stop tighter
    assert stop_for(100, 2, 101) == 97         # swing low above close is ignored


def test_data_quality_guard():
    clean = make_bars(np.linspace(100, 120, 120))
    ref = clean.index[-1].date()
    assert data_quality_issue(clean, ref) is None
    assert "stale" in data_quality_issue(clean, ref + timedelta(days=10))
    jumped = clean.copy()
    jumped.iloc[-5:, jumped.columns.get_loc("Close")] *= 2
    assert "suspicious" in data_quality_issue(jumped, ref)
    assert data_quality_issue(clean.tail(30), ref) == "insufficient history"


def test_trend_template_uptrend_vs_downtrend():
    up = trend_template(make_bars(np.linspace(50, 200, 300)))
    down = trend_template(make_bars(np.linspace(200, 50, 300)))
    assert up["pass"] is True
    assert down["pass"] is False and down["checks"]["ma50_above_ma200"] is False
