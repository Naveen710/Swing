"""NSE corporate announcements (official exchange disclosures).

Endpoint and response shape checked on 2026-10-20: GET
https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=<SYMBOL>
returns a JSON array of objects with keys including seq_id, symbol, sm_name, sm_isin, desc
(NSE's category), attchmntText, attchmntFile (PDF URL on nsearchives.nseindia.com), an_dt
("01-Oct-2026 15:59:23", IST), sort_date, hasXbrl.

NSE expects browser-like headers and a session cookie from the home page, and is known to
block some cloud IP ranges. Failures are returned as status=error and logged as data issues;
nothing is invented. Attachments are only fetched from NSE's own archive host.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from app.research.providers.base import (
    ERROR, MISSING, OK, AnnouncementRecord, ProviderResult, RateLimiter, RawPayload, SecurityRef, with_retries,
)

IST = ZoneInfo("Asia/Kolkata")
BASE = "https://www.nseindia.com"
API = BASE + "/api/corporate-announcements?index=equities&symbol={symbol}"
ATTACHMENT_HOSTS = {"nsearchives.nseindia.com", "archives.nseindia.com", "www.nseindia.com"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": BASE + "/companies-listing/corporate-filings-announcements",
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
_limiter = RateLimiter(1.0)


def parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def parse_announcements(payload: list | dict, symbol: str, limit: int) -> list[AnnouncementRecord]:
    rows = payload if isinstance(payload, list) else payload.get("data", [])
    out = []
    for r in rows[:limit]:
        if not isinstance(r, dict) or not r.get("seq_id"):
            continue
        if r.get("symbol") and str(r["symbol"]).upper() != symbol.upper():
            continue
        out.append(AnnouncementRecord(
            external_id=str(r["seq_id"]), category=r.get("desc"), text=r.get("attchmntText"),
            published_at=parse_time(r.get("an_dt")) or parse_time(r.get("sort_date")),
            attachment_url=r.get("attchmntFile") or None, isin=r.get("sm_isin"),
        ))
    return out


class NseProvider:
    name = "nse"

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout
        self._session: requests.Session | None = None
        self._lock = threading.Lock()

    def _get_session(self) -> requests.Session:
        with self._lock:
            if self._session is None:
                s = requests.Session()
                s.headers.update(HEADERS)
                s.get(BASE, timeout=self.timeout)          # sets the cookies the API requires
                self._session = s
            return self._session

    def get_announcements(self, sec: SecurityRef, limit: int = 40) -> ProviderResult:
        url = API.format(symbol=sec.symbol)
        try:
            def call():
                _limiter.wait()
                resp = self._get_session().get(url, timeout=self.timeout)
                if resp.status_code in (401, 403):
                    with self._lock:
                        self._session = None             # refresh cookies once
                resp.raise_for_status()
                return resp

            resp = with_retries(call, attempts=3)
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(status=ERROR, error=f"NSE announcements for {sec.symbol}: {exc}")
        records = parse_announcements(payload, sec.symbol, limit)
        raw = RawPayload(f"nse:announcements:{sec.symbol}", json.dumps(payload, sort_keys=True).encode(), "application/json", url=url)
        for r in records:
            r.raw_key = raw.source_key
        return ProviderResult(status=OK if records else MISSING, data=records, raw=[raw],
                              error=None if records else "No announcements returned")

    def fetch(self, url: str) -> ProviderResult:
        host = urlparse(url).hostname or ""
        if host not in ATTACHMENT_HOSTS:
            return ProviderResult(status=ERROR, error=f"Refusing to fetch attachment from {host} (not an NSE archive host)")
        try:
            _limiter.wait()
            resp = with_retries(lambda: self._get_session().get(url, timeout=self.timeout, stream=True), attempts=2)
            resp.raise_for_status()
            data = b""
            for chunk in resp.iter_content(64 * 1024):
                data += chunk
                if len(data) > MAX_ATTACHMENT_BYTES:
                    return ProviderResult(status=ERROR, error="Attachment larger than 10 MB — skipped")
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(status=ERROR, error=f"NSE attachment {url}: {exc}")
        return ProviderResult(status=OK, data=data, raw=[RawPayload(f"nse:file:{url}", data, resp.headers.get("Content-Type", "application/pdf"), url=url)])
