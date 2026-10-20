"""Market-cap segments: constituent lists, live-download fallback, segment fetch job, templates, API."""
import csv
import io
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.research import ingest, models as m, segments
from app.research.db import now, tx
from app.research.providers.base import ERROR, OK, ProviderResult, RawPayload
from app.research.service import ensure_master
from sqlalchemy import select

c = TestClient(app)
R = "/api/research"
CSV_HEAD = "Company Name,Industry,Symbol,Series,ISIN Code\n"


@pytest.fixture(scope="module", autouse=True)
def master():
    ensure_master()
    segments.ensure_segments()


def _wait_job(timeout=30):
    end = time.time() + timeout
    while ingest.job.get().get("running") and time.time() < end:
        time.sleep(0.05)
    assert not ingest.job.get().get("running")


def test_parse_constituents_skips_placeholders_and_duplicates():
    body = (CSV_HEAD + "ABC Ltd.,Capital Goods,ABC,EQ,INE000A01011\nABC Ltd.,Capital Goods,ABC,EQ,INE000A01011\n"
            "Dummy,Metals,DUMMYVEDL1,EQ,\nXYZ Ltd.,Chemicals,xyz,EQ,INE000B01012\n").encode()
    rows = segments.parse_constituents(body)
    assert [r["symbol"] for r in rows] == ["ABC", "XYZ"] and rows[0]["industry"] == "Capital Goods" and rows[1]["isin"] == "INE000B01012"
    with pytest.raises(ValueError):
        segments.parse_constituents(b"foo,bar\n1,2\n")


def test_bundled_lists_split_nifty_500_exactly():
    lg, md, sm = ({r["symbol"] for r in segments.bundled(s)} for s in ("large", "mid", "small"))
    assert (len(lg), len(md), len(sm)) == (100, 150, 250)
    assert not (lg & md or lg & sm or md & sm)
    n500 = {r["symbol"] for r in segments.parse_constituents((segments.DATA / "nifty500.csv").read_bytes())}
    assert lg | md | sm == n500


def test_membership_loaded_for_all_segments():
    with tx() as conn:
        counts = {s: len(segments.members(conn, s)) for s in segments.SEGMENTS}
    assert counts == {"large": 100, "mid": 150, "small": 250}


def test_fiscal_year_and_template_known_answer():
    assert segments.last_fiscal_year_end(date(2026, 5, 31)) == date(2025, 3, 31)
    assert segments.last_fiscal_year_end(date(2026, 6, 1)) == date(2026, 3, 31)
    text = segments.template_csv("mid", [("AAA", False), ("BANKX", True)], date(2026, 3, 31))
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [r["case_id"] for r in rows] == ["M001", "M002", "M003", "M004"]
    assert rows[0]["field"] == "revenue" and rows[1]["field"] == "net_income" and rows[0]["expected"] == ""
    assert "revenue from operations" in rows[0]["source_note"] and "total income" in rows[2]["source_note"]
    assert all(r["period"] == "2026-03-31" and r["unit"] == "INR_cr" for r in rows)


def test_shipped_templates_match_bundled_lists():
    for seg, n in (("large", 100), ("mid", 150), ("small", 250)):
        path = segments.Path(segments.__file__).parent / "verification" / f"real_cases_{seg}_cap.csv"
        rows = list(csv.DictReader(path.open(encoding="utf-8")))
        assert len(rows) == 2 * n and len({r["symbol"] for r in rows}) == n
        assert rows[0]["case_id"] == f"{segments.TEMPLATE_PREFIX[seg]}001"


def test_live_list_is_archived_and_failure_falls_back(monkeypatch):
    monkeypatch.setattr(ingest, "demo_mode", lambda: False)
    good = segments.bundled("large")[:99] + [{"symbol": "NEWCO", "name": "New Co Ltd.", "industry": "Services", "isin": "INE999Z01019", "series": "EQ"}]
    body = (CSV_HEAD + "".join(f"{r['name']},{r['industry'] or ''},{r['symbol']},EQ,{r['isin'] or ''}\n" for r in good)).encode()
    monkeypatch.setattr(segments, "fetch_live", lambda seg: ProviderResult(status=ERROR, error="HTTP 403"))
    out = segments.refresh_lists(["large"])
    assert out["large"]["source"] == "bundled" and out["large"]["count"] == 100
    with tx() as conn:
        issue = conn.execute(select(m.data_issues).where(m.data_issues.c.field == "index.large")).mappings().first()
    assert issue["status"] == "open" and "403" in issue["detail"]

    monkeypatch.setattr(segments, "fetch_live", lambda seg: ProviderResult(status=OK, data=segments.parse_constituents(body),
                                                                          raw=[RawPayload("niftyindices:ind_nifty100list.csv", body, "text/csv", url="u")]))
    out = segments.refresh_lists(["large"])
    assert out["large"]["source"] == "niftyindices" and out["large"]["added"] == ["NEWCO"] and len(out["large"]["removed"]) == 1
    with tx() as conn:
        assert "NEWCO" in segments.members(conn, "large")
        issue = conn.execute(select(m.data_issues.c.status).where(m.data_issues.c.field == "index.large")).scalar()
        raw = conn.execute(select(m.raw_sources.c.provider).where(m.raw_sources.c.source_key == "niftyindices:ind_nifty100list.csv")).all()
    assert issue == "resolved" and any(r[0] == "niftyindices" for r in raw)
    segments.refresh_lists(["large"], live=False)        # restore the bundled list for the other tests
    with tx() as conn:
        assert "NEWCO" not in segments.members(conn, "large")


def test_segment_fetch_job_counts_skips_fresh_and_stops(monkeypatch):
    calls = []

    def fake_ingest(sym, providers=None, fetch_documents=True):
        calls.append((sym, fetch_documents))
        time.sleep(0.002)
        return {"status": "partial"}

    monkeypatch.setattr(ingest, "ingest_company", fake_ingest)
    monkeypatch.setattr(ingest, "ingest_news", lambda providers=None: {"status": "ok"})
    monkeypatch.setattr("app.research.engine.build_snapshot", lambda sym: {"snapshot_id": None})
    with tx() as conn:
        first = segments.members(conn, "mid")[0]
        cid = conn.execute(select(m.securities.c.company_id).where(m.securities.c.symbol == first)).scalar()
        conn.execute(m.ingestion_runs.insert().values(kind="company", company_id=cid, started_at=now(), finished_at=now(), status="ok", detail={}))
    r = c.post(f"{R}/segments/mid/fetch", json={"refresh_list": False}).json()
    assert r["started"] is True
    _wait_job()
    st = ingest.job.get()
    assert st["stage"] == "done" and st["segment"] == "mid" and st["total"] == 150
    assert st["counts"] == {"ok": 0, "partial": 149, "error": 0, "fresh": 1}
    assert first not in {s for s, _ in calls} and all(d is False for _, d in calls)

    calls.clear()
    monkeypatch.setattr(ingest, "ingest_company", lambda sym, providers=None, fetch_documents=True: (time.sleep(0.05), {"status": "ok"})[1])
    assert c.post(f"{R}/segments/small/fetch", json={"refresh_list": False, "force": True}).json()["started"] is True
    assert c.post(f"{R}/segments/large/fetch").json()["started"] is False      # one job at a time
    time.sleep(0.15)
    assert c.post(f"{R}/ingest/stop").json()["stopping"] is True
    _wait_job()
    st = ingest.job.get()
    assert st["stage"] == "stopped" and st["done"] < 250


def test_recently_refreshed_window():
    with tx() as conn:
        sym = segments.members(conn, "small")[-1]
        cid = conn.execute(select(m.securities.c.company_id).where(m.securities.c.symbol == sym)).scalar()
        conn.execute(m.ingestion_runs.insert().values(kind="company", company_id=cid, started_at=now() - timedelta(hours=30),
                                                      finished_at=now() - timedelta(hours=30), status="ok", detail={}))
    assert ingest.Job._recently_refreshed(sym, 24) is False and ingest.Job._recently_refreshed(sym, 48) is True


def test_segment_api():
    ov = c.get(f"{R}/segments").json()
    assert [s["key"] for s in ov["segments"]] == ["large", "mid", "small"] and [s["count"] for s in ov["segments"]] == [100, 150, 250]
    assert ov["segments"][0]["index"] == "NIFTY 100" and "NIFTY 500" in ov["note"]
    d = c.get(f"{R}/segments/large").json()
    assert d["count"] == 100 and d["source"] == "bundled" and {"symbol", "status", "metrics", "dimensions", "price"} <= set(d["items"][0])
    assert set(d["items"][0]["metrics"]) == {"market_cap", "pe", "pb", "roe", "roce", "revenue_growth", "profit_growth", "debt_equity", "ret_1y"}
    assert c.get(f"{R}/segments/huge").status_code == 404
    t = c.get(f"{R}/segments/small/verification-template.csv")
    assert t.status_code == 200 and "attachment" in t.headers["content-disposition"] and "real_cases_small_cap.csv" in t.headers["content-disposition"]
    assert len(list(csv.DictReader(io.StringIO(t.text)))) == 500
    f = c.get(f"{R}/companies", params={"segment": "mid", "limit": 200}).json()
    assert f["total"] == 150
    assert c.get(f"{R}/companies", params={"segment": "nano"}).status_code == 422
