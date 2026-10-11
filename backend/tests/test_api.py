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
