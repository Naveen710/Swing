"""End-to-end smoke tests: every page's endpoint answers with the demo provider."""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_search_finds_reliance():
    r = client.get("/api/search", params={"q": "RELIANCE"})
    assert r.status_code == 200 and any("RELIANCE" in x["symbol"] for x in r.json())


def test_analyze_returns_grade():
    r = client.get("/api/analyze/RELIANCE")
    assert r.status_code == 200
    body = r.json()
    assert body["overall_grade"] in list("ABCDE") and 0 <= body["overall_score"] <= 100


def test_valuation_rejects_bad_method():
    assert client.get("/api/valuation/RELIANCE", params={"method": "xyz"}).status_code == 400


def test_valuation_runs():
    r = client.get("/api/valuation/RELIANCE")
    assert r.status_code == 200


def test_vcp_monitor_empty():
    r = client.post("/api/vcp/monitor", json={"positions": []})
    assert r.status_code == 200


@pytest.mark.slow
@pytest.mark.parametrize("path", ["/api/quant", "/api/vcp", "/api/algo", "/api/regime", "/api/sectors"])
def test_universe_screens(path):
    r = client.get(path, params={"universe": "nifty_smallcap_250"})
    assert r.status_code in (200, 503), r.text


# ── Multibagger (AC 11) ──────────────────────────────────────────────────────
import time  # noqa: E402


def test_multibagger_backtest_404_before_any_run():
    assert client.get("/api/multibagger/backtest", params={"universe": "mid_small_2000_plus"}).status_code == 404


def test_multibagger_run_end_to_end():
    first = client.post("/api/multibagger/run", params={"universe": "nifty_smallcap_250"}).json()
    assert first["started"] is True
    second = client.post("/api/multibagger/run", params={"universe": "nifty_smallcap_250"}).json()
    assert second["started"] is False and second["state"]["reason"]
    deadline = time.time() + 240
    state = {}
    while time.time() < deadline:
        state = client.get("/api/multibagger/status").json()
        if not state["running"]:
            break
        time.sleep(1)
    assert state["stage"] == "done", state
    body = client.get("/api/multibagger", params={"universe": "nifty_smallcap_250"}).json()
    assert body["available"] is True and body["demo"] is True
    assert set(body["funnel"]) == {"universe_count", "after_size_bucket", "after_prefilter", "deep_pass", "not_deep_passed",
                                   "passed_kill_switches", "tiered", "insufficient"}
    assert body["funnel"]["deep_pass"] <= 120
    row = body["results"][0]
    assert row["rank"] == 1 and row["tier"] in "ABCD" and set(row["layers"]) == {"growth", "quality", "valuation", "tailwind", "price"}
    assert [r["score"] for r in body["results"]] == sorted((r["score"] for r in body["results"]), reverse=True)


def test_multibagger_single_stock():
    r = client.get("/api/multibagger/stock/RELIANCE")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("scored", "excluded", "insufficient")
    assert body.get("tier") or body.get("code") or body.get("reason")


def test_multibagger_ledger_endpoint():
    r = client.get("/api/multibagger/ledger", params={"universe": "nifty_smallcap_250"})
    assert r.status_code == 200 and "decision" in r.json()
