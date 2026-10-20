"""News from permitted publisher RSS feeds. Headlines are leads, not verified facts.

Default feed: Business Standard markets RSS (RSS 2.0; checked 2026-10-20 — items carry title,
link/guid, description, pubDate with +0530 offset, dc:publisher). Google News RSS is NOT used:
its robots.txt disallows automated fetching. Add feeds with RESEARCH_NEWS_FEEDS (comma-separated).
"""
from __future__ import annotations

import hashlib
import os
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

from app.research.providers.base import ERROR, OK, NewsRecord, ProviderResult, RawPayload, with_retries

DEFAULT_FEEDS = ("https://www.business-standard.com/rss/markets-106.rss",)
NS = {"dc": "http://purl.org/dc/elements/1.1/"}
COMPANY_SUFFIXES = re.compile(r"\b(limited|ltd|private|pvt)\b\.?", re.I)
PARENS = re.compile(r"\([^)]*\)")


def feeds() -> tuple[str, ...]:
    raw = os.getenv("RESEARCH_NEWS_FEEDS", "")
    extra = tuple(u.strip() for u in raw.split(",") if u.strip())
    return extra or DEFAULT_FEEDS


def normalise_headline(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", text.lower())).strip()


def dup_group(headline: str) -> str:
    return hashlib.sha256(normalise_headline(headline).encode()).hexdigest()[:32]


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip() or None


def parse_rss(xml_bytes: bytes, feed_url: str) -> list[NewsRecord]:
    root = ET.fromstring(xml_bytes)
    channel_title = (root.findtext("channel/title") or "").strip() or None
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or item.findtext("guid") or "").strip()
        if not title or not link:
            continue
        if not link.startswith("http"):
            link = "https://" + link.lstrip("/")
        published = None
        if item.findtext("pubDate"):
            try:
                published = parsedate_to_datetime(item.findtext("pubDate"))
            except (TypeError, ValueError):
                published = None
        publisher = (item.findtext("dc:publisher", namespaces=NS) or channel_title or "").strip() or None
        out.append(NewsRecord(headline=title, url=link, publisher=publisher, published_at=published,
                              summary=_strip_html(item.findtext("description")), feed=feed_url))
    return out


def company_keys(name: str, symbol: str) -> list[tuple[str, re.Pattern]]:
    """Patterns that identify a company in a headline: its symbol as a whole word (case-sensitive,
    3+ letters) and its short name (suffixes like Ltd/India removed, 5+ letters)."""
    # Only legal suffixes and parentheticals are removed, so "Reliance Industries" never shortens
    # to "Reliance" (which would also match Reliance Power).
    short = re.sub(r"\s+", " ", COMPANY_SUFFIXES.sub(" ", PARENS.sub(" ", name))).strip(" .,&-")
    pats: list[tuple[str, re.Pattern]] = []
    if len(symbol) >= 3:
        pats.append(("symbol", re.compile(rf"\b{re.escape(symbol)}\b")))
    if len(short) >= 5:
        pats.append(("name", re.compile(rf"\b{re.escape(short)}\b", re.I)))
    return pats


def match_company(text: str, candidates: list[tuple[int, list[tuple[str, re.Pattern]]]]) -> tuple[int | None, str]:
    for company_id, pats in candidates:
        for kind, p in pats:
            if p.search(text):
                return company_id, kind
    return None, "none"


class RssProvider:
    name = "rss"

    def __init__(self, urls: tuple[str, ...] | None = None, timeout: int = 15) -> None:
        self.urls = urls or feeds()
        self.timeout = timeout

    def get_news(self) -> ProviderResult:
        items: list[NewsRecord] = []
        raws: list[RawPayload] = []
        errors = []
        for url in self.urls:
            try:
                resp = with_retries(lambda: requests.get(url, timeout=self.timeout, headers={"User-Agent": "NSE-Swing-Research/1.0"}), attempts=2)
                resp.raise_for_status()
                parsed = parse_rss(resp.content, url)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{url}: {exc}")
                continue
            key = f"rss:{url}"
            raws.append(RawPayload(key, resp.content, "application/rss+xml", url=url))
            for p in parsed:
                p.raw_key = key
            items += parsed
        if not items and errors:
            return ProviderResult(status=ERROR, error="; ".join(errors))
        return ProviderResult(status=OK, data=items, raw=raws, notes=errors)
