"""Research API end to end on sample data (offline)."""
import io
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app)
R = "/api/research"


@pytest.fixture(scope="module", autouse=True)
def seeded():
    r = c.post(f"{R}/seed-samples")
    assert r.status_code == 200, r.text
    return r.json()


def test_health_and_settings():
    h = c.get(f"{R}/health").json()
    assert h["status"] == "ok" and h["database"]["schema"] == [1, 2, 3] and h["ai_enabled"] is False
    s = c.get(f"{R}/settings").json()
    assert s["sources"] and s["ai"]["enabled"] is False and len(s["deep_symbols"]) == 20


def test_dashboard():
    d = c.get(f"{R}/dashboard").json()
    assert d["totals"]["researched"] >= 20 and d["candidates"] and all(x["status"] == "ELIGIBLE_FOR_RESEARCH" for x in d["candidates"])
    assert "Not recommendations" in d["note"]


def test_explorer_search_and_filters():
    r = c.get(f"{R}/companies", params={"q": "Aravali"}).json()
    assert any(i["symbol"] == "SMPCABLE" for i in r["items"])
    assert c.get(f"{R}/companies", params={"q": "RELIANCE"}).json()["items"][0]["symbol"] == "RELIANCE"
    sample = c.get(f"{R}/companies", params={"scope": "sample"}).json()
    assert sample["total"] == 20 and all(i["is_sample"] for i in sample["items"])
    assert c.get(f"{R}/companies", params={"scope": "bad"}).status_code == 400
    assert c.get(f"{R}/sectors").json()


def test_company_dossier_prices_facts_markdown():
    d = c.get(f"{R}/company/SMPCABLE").json()
    out = d["snapshot"]["output"]
    assert out["status"] in ("ELIGIBLE_FOR_RESEARCH", "REVIEW_REQUIRED", "BLOCKED") and len(out["dimensions"]) == 7
    assert d["freshness"]["last_price_date"] and d["freshness"]["price_stale"] is False
    p = c.get(f"{R}/company/SMPCABLE/prices", params={"days": 100}).json()
    assert len(p["bars"]) == 100 and p["actions"]
    f = c.get(f"{R}/company/SMPTEXT/facts").json()["facts"]
    fy25 = [x for x in f if x["metric"] == "revenue" and x["period_end"] == "2025-03-31" and x["period_type"] == "FY"]
    assert sorted(x["provider"] for x in fy25) == ["fixture", "manual"]          # original and restated both kept
    md = c.get(f"{R}/company/SMPCABLE/dossier.md").text
    assert md.startswith("# Aravali Cables") and "Sample data" in md
    assert c.get(f"{R}/company/NOPE123").status_code == 404
    assert c.get(f"{R}/company/bad symbol!").status_code == 400


def test_snapshot_replay():
    sid = c.get(f"{R}/company/SMPPHARMA").json()["snapshot"]["id"]
    assert c.post(f"{R}/snapshots/{sid}/replay").json()["reproducible"] is True


def test_discovery_queue():
    q = c.get(f"{R}/discovery", params={"sort": "recognition_gap"}).json()
    assert q["sort"] == "recognition_gap" and len(q["items"]) >= 20
    statuses = [i["status"] for i in q["items"]]
    assert statuses == sorted(statuses, key=["ELIGIBLE_FOR_RESEARCH", "REVIEW_REQUIRED", "BLOCKED"].index)
    assert all("percentiles" in i for i in q["items"])
    only = c.get(f"{R}/discovery", params={"status": "REVIEW_REQUIRED"}).json()["items"]
    assert only and all(i["status"] == "REVIEW_REQUIRED" for i in only)


def test_news_and_announcements():
    n = c.get(f"{R}/news").json()
    assert n["items"] and "leads" in n["note"]
    dup = [i for i in n["items"] if i.get("duplicates")]
    assert dup, "duplicate story should be grouped"
    a = c.get(f"{R}/announcements", params={"event_type": "governance"}).json()["items"]
    assert any(x["symbol"] == "SMPCHEM" for x in a)


def test_evidence_chain():
    d = c.get(f"{R}/company/SMPCABLE").json()
    sid, out = d["snapshot"]["id"], d["snapshot"]["output"]
    claim = next(x for x in out["claims"] if x["type"] == "computed" and x["evidence"][0].startswith("metric:"))
    metric = c.get(f"{R}/evidence/{claim['evidence'][0]}", params={"snapshot_id": sid}).json()
    assert metric["metric"]["inputs"] and metric["inputs"][0]["fact"]["id"] == metric["metric"]["inputs"][0]
    fact = c.get(f"{R}/evidence/{claim['evidence'][1]}").json()
    assert fact["source"]["sha256"] and fact["versions"]
    raw = c.get(f"{R}/raw/{fact['source']['id']}/content")
    assert raw.status_code == 200 and raw.headers["x-sha256"] == fact["source"]["sha256"]
    ann = next(x for x in out["claims"] if x["type"] == "sourced")
    ev = c.get(f"{R}/evidence/{ann['evidence'][0]}").json()
    assert ev["announcement"]["text"]
    assert c.get(f"{R}/evidence/fact:999999").status_code == 404
    assert c.get(f"{R}/evidence/drop table").status_code in (400, 404)


def test_document_and_search():
    hits = c.get(f"{R}/search", params={"q": "guidance"}).json()
    assert hits["passages"]
    doc = c.get(f"{R}/documents/{hits['passages'][0]['document_id']}").json()
    assert doc["chunks"] and doc["chunks"][0]["page"] == 1 and doc["document"]["sha256"]


def test_watchlist_lifecycle():
    wid = c.post(f"{R}/watchlists", json={"name": "Inflection ideas"}).json()["id"]
    item = c.post(f"{R}/watchlists/{wid}/items", json={"symbol": "SMPCABLE", "reason": "orders accelerating"}).json()
    assert c.post(f"{R}/watchlists/{wid}/items", json={"symbol": "SMPCABLE"}).status_code == 400
    wl = next(w for w in c.get(f"{R}/watchlists").json() if w["id"] == wid)
    it = wl["items"][0]
    assert it["reason"] == "orders accelerating" and it["added_price"] and it["status_now"]
    assert c.put(f"{R}/watchlists/{wid}", json={"name": "Renamed"}).status_code == 200
    assert c.delete(f"{R}/watchlists/{wid}/items/{item['id']}").status_code == 200
    assert c.delete(f"{R}/watchlists/999999").status_code == 404


def test_decision_journal():
    did = c.post(f"{R}/decisions", json={"symbol": "SMPDEF", "title": "Defence orders", "thesis": "Order book visibility",
                                         "invalidation": "Order inflow falls two quarters running"}).json()["id"]
    fu = c.post(f"{R}/decisions/{did}/follow-ups", json={"note": "Q2 checked", "outcome": "on track"}).json()
    assert fu["price"] and fu["status"]
    assert c.put(f"{R}/decisions/{did}", json={"status": "closed", "outcome": "thesis played out"}).status_code == 200
    assert c.put(f"{R}/decisions/{did}", json={"status": "bogus"}).status_code == 400
    d = next(x for x in c.get(f"{R}/decisions").json() if x["id"] == did)
    assert d["status"] == "closed" and len(d["follow_ups"]) == 1 and d["change_since_entry_pct"] is not None
    assert c.post(f"{R}/decisions", json={"title": "no symbol"}).status_code == 400


def test_data_quality_console():
    dq = c.get(f"{R}/data-quality").json()
    assert dq["issues"] and dq["runs"] and dq["verification"] and dq["sources"]
    iid = dq["issues"][0]["id"]
    assert c.post(f"{R}/issues/{iid}", json={"status": "ignored"}).status_code == 200
    assert c.post(f"{R}/issues/{iid}", json={"status": "nope"}).status_code == 400


def test_uploads():
    csv = b"symbol,metric,period_end,period_type,value,unit,scope,source_note\nSMPIT,other_income,2026-03-31,FY,12.5,INR_cr,consolidated,AR p.80\n"
    r = c.post(f"{R}/import/facts", files={"file": ("facts.csv", io.BytesIO(csv), "text/csv")}).json()
    assert r["inserted"] + r["unchanged"] == 1
    assert c.post(f"{R}/import/facts", files={"file": ("x.txt", io.BytesIO(b"x"), "text/plain")}).status_code == 400
    from app.research.providers.fixtures import _pdf
    pdf = _pdf(["Manual upload test", "Order book of Rs 900 crore."])
    d = c.post(f"{R}/import/document", files={"file": ("note.pdf", io.BytesIO(pdf), "application/pdf")}, data={"symbol": "SMPIT", "title": "Broker note"}).json()
    assert d["text_status"] == "ok" and d["pages"] == 1
    assert c.post(f"{R}/import/document", files={"file": ("n.pdf", io.BytesIO(b"not a pdf"), "application/pdf")}).status_code == 400
    v = b"case_id,symbol,kind,field,period,period_type,expected,unit,tolerance_pct,source_note,explanation\nU1,SMPIT,fact,other_income,2026-03-31,FY,12.5,INR_cr,0.5,AR,\n"
    assert c.post(f"{R}/verify", files={"file": ("v.csv", io.BytesIO(v), "text/csv")}).json()["summary"]["match"] == 1


def test_backup_restore_roundtrip():
    wid = c.post(f"{R}/watchlists", json={"name": "Backup test"}).json()["id"]
    c.post(f"{R}/watchlists/{wid}/items", json={"symbol": "SMPPHARMA", "reason": "quality"})
    c.post(f"{R}/decisions", json={"symbol": "SMPPHARMA", "thesis": "steady compounder"})
    b = c.get(f"{R}/backup").json()
    assert b["format"] == "swing-research-backup" and any(w["name"] == "Backup test" for w in b["watchlists"])
    c.delete(f"{R}/watchlists/{wid}")
    r = c.post(f"{R}/restore", json=b).json()
    assert r["restored"] and not r["skipped"]
    names = [w["name"] for w in c.get(f"{R}/watchlists").json()]
    assert "Backup test" in names
    assert c.post(f"{R}/restore", json={"format": "other"}).status_code == 400


def test_ai_not_configured():
    assert c.post(f"{R}/ai/extract/1").status_code == 409
    assert c.post(f"{R}/ai/narrate/1").status_code == 409


def test_ingest_job():
    r = c.post(f"{R}/ingest", json={"symbols": ["SMPNBFC"], "news": False}).json()
    assert r["started"] is True
    for _ in range(60):
        s = c.get(f"{R}/ingest/status").json()
        if not s["running"]:
            break
        time.sleep(0.5)
    assert s["stage"] == "done" and s["results"][0]["symbol"] == "SMPNBFC"
    assert c.post(f"{R}/ingest", json={}).status_code == 400


def test_password_protection(monkeypatch):
    monkeypatch.setenv("RESEARCH_PASSWORD", "s3cret")
    assert c.get(f"{R}/dashboard").status_code == 401
    assert c.get(f"{R}/health").status_code == 200
    assert c.post(f"{R}/auth/login", json={"password": "wrong"}).status_code == 401
    token = c.post(f"{R}/auth/login", json={"password": "s3cret"}).json()["token"]
    assert c.get(f"{R}/dashboard", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert c.get(f"{R}/dashboard", headers={"Authorization": "Bearer forged.abc"}).status_code == 401
