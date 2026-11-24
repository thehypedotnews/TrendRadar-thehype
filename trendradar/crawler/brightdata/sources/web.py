# coding=utf-8
"""
Arbitrary web pages through Bright Data Web Unlocker.

One page = one request. Configure each page as:
  - url: https://example.com/news           (required)
    name: Example newsroom                 (optional label)
    listing: true                          (page lists many articles; default false = one article)
    link_pattern: "/news/20"               (optional regex the article links must match)
    max_items: 20                          (listing only)
"""

from typing import Dict, List

from ..client import BrightDataClient
from ..errors import BrightDataError
from ..models import CollectedItem, clean_title
from ..timeparse import parse_time
from .extract import extract_article, extract_listing


def collect_web(client: BrightDataClient, cfg: Dict, zone: str, errors: List[BrightDataError]) -> List[CollectedItem]:
    items: List[CollectedItem] = []
    for page in cfg.get("pages", []) or []:
        if isinstance(page, str):
            page = {"url": page}
        url = str(page.get("url", "")).strip()
        if not url:
            continue
        label = page.get("name") or url
        extra = {"country": page["country"]} if page.get("country") else None
        try:
            html = client.zone_request(zone, url, "web", extra=extra)
        except BrightDataError as e:
            e.source = e.source or "web"
            if e.is_fatal or e.kind == "budget":
                raise
            print(f"[Bright Data] web '{label}' failed: {e.short()}")
            errors.append(e)
            continue

        if page.get("listing"):
            rows = extract_listing(html, url, page.get("link_pattern", ""), int(page.get("max_items", 20)))
        else:
            row = extract_article(html, url)
            rows = [row] if row.get("title") else []

        total = len(rows)
        for i, row in enumerate(rows, 1):
            items.append(
                CollectedItem(
                    title=clean_title(row["title"]),
                    url=row["url"],
                    source="web",
                    published_at=parse_time(row.get("time")) if row.get("time") else None,
                    position=i,
                    position_total=total,
                    query=str(label),
                    publisher=str(label),
                )
            )
        print(f"[Bright Data] web '{label}': {total} items")
    return items
