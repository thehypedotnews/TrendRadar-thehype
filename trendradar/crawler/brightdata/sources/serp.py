# coding=utf-8
"""
Google / Google News results through the Bright Data SERP API.

One query = one request = one billed result page.
Parsed JSON is requested with the brd_json=1 URL parameter; the News tab is
tbm=nws, recency is Google's tbs=qdr:<h|d|w>.
"""

import json
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from ..client import BrightDataClient
from ..errors import BrightDataError
from ..models import CollectedItem, clean_title
from ..timeparse import parse_time

RECENCY = {"hour": "qdr:h", "day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "any": ""}


def build_google_url(query: str, news: bool, cfg: Dict) -> str:
    params = {
        "q": query,
        "hl": cfg.get("language", "en"),
        "gl": cfg.get("country", "us"),
        "num": int(cfg.get("results_per_query", 10)),
        "brd_json": 1,
    }
    if news:
        params["tbm"] = "nws"
    tbs = RECENCY.get(str(cfg.get("recency", "day")).lower(), "qdr:d")
    if tbs:
        params["tbs"] = tbs
    return "https://www.google.com/search?" + urlencode(params)


def parse_serp(body: str, news: bool, query: str, now: Optional[datetime] = None) -> List[CollectedItem]:
    try:
        data = json.loads(body)
    except ValueError:
        snippet = " ".join((body or "").split())[:160]
        raise BrightDataError(
            "bad_request",
            f"SERP API did not return JSON (is the zone a SERP API zone?). Response starts with: {snippet!r}",
            None,
            "google_news" if news else "google",
        )
    if not isinstance(data, dict):
        return []
    source = "google_news" if news else "google"
    rows = data.get("news") if news else data.get("organic")
    if news and not rows:
        # some layouts put news cards into "top_stories"
        rows = data.get("top_stories") or []
    rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("link") and r.get("title")]
    total = len(rows)
    out = []
    for i, r in enumerate(rows, 1):
        date_raw = r.get("date") or r.get("last_modified_date") or r.get("age")
        out.append(
            CollectedItem(
                title=clean_title(r.get("title", "")),
                url=r["link"],
                source=source,
                published_at=parse_time(date_raw, now) if date_raw else None,
                engagement=None,
                position=i,
                position_total=total,
                query=query,
                publisher=str(r.get("source") or r.get("display_link") or ""),
            )
        )
    return out


def collect_serp(client: BrightDataClient, cfg: Dict, zone: str, errors: List[BrightDataError]) -> List[CollectedItem]:
    """Run every configured query. Errors are appended to `errors`; fatal ones are raised."""
    items: List[CollectedItem] = []
    search_type = str(cfg.get("search_type", "news")).lower()
    tabs = {"news": [True], "web": [False], "both": [True, False]}.get(search_type, [True])
    for query in cfg.get("queries", []) or []:
        query = str(query).strip()
        if not query:
            continue
        for news in tabs:
            source = "google_news" if news else "google"
            tries = 1 + max(0, int(cfg.get("retries_on_bad_response", 1)))
            for attempt in range(1, tries + 1):
                try:
                    body = client.zone_request(zone, build_google_url(query, news, cfg), source)
                    got = parse_serp(body, news, query)
                    if cfg.get("resolve_redirects", True):
                        resolved = sum(_resolve(it) for it in got)
                        if resolved:
                            print(f"[Bright Data] {source} '{query}': resolved {resolved} Google redirect link(s)")
                    print(f"[Bright Data] {source} '{query}': {len(got)} results")
                    items.extend(got)
                    break
                except BrightDataError as e:
                    e.source = e.source or source
                    if e.is_fatal or e.kind == "budget":
                        raise
                    if attempt < tries and "did not return JSON" in e.message:
                        print(f"[Bright Data] {source} '{query}': unexpected response, retrying once")
                        continue
                    print(f"[Bright Data] {source} '{query}' failed: {e.short()}")
                    errors.append(e)
                    break
    return items


# ------------------------------------------------------------ redirect links

_REDIRECT_PATHS = ("/url", "/goto", "/aclk")


def is_google_redirect(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    return (host == "google.com" or host.endswith(".google.com") or host.startswith("google.")) and p.path in _REDIRECT_PATHS


def resolve_redirect(url: str, timeout: float = 6.0, session=None) -> Optional[str]:
    """Real article URL behind a Google News redirect link, or None.

    First looks for a plain URL in the query (google.com/url?q=...), otherwise asks Google
    for the redirect target without following it. This is a direct request to Google,
    not a Bright Data request, so it costs nothing.
    """
    if not is_google_redirect(url):
        return None
    qs = parse_qs(urlparse(url).query)
    for key in ("q", "url", "u"):
        for v in qs.get(key, []):
            if v.startswith("http://") or v.startswith("https://"):
                return v
    try:
        resp = (session or requests).get(
            url, allow_redirects=False, timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"},
        )
    except requests.RequestException:
        return None
    loc = resp.headers.get("Location", "")
    if resp.status_code in (301, 302, 303, 307, 308) and loc.startswith("http") and not is_google_redirect(loc):
        return loc
    return None


def _resolve(item: CollectedItem) -> int:
    real = resolve_redirect(item.url)
    if real:
        item.url = real
        return 1
    return 0
