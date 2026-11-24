# coding=utf-8
"""
Common item format for everything collected through Bright Data.

Every source (SERP, Reddit, X, YouTube, Web Unlocker) is normalized into
CollectedItem. The collector then turns these into TrendRadar's hot-list
format {platform_id: {title: {"ranks": [...], "url": ..., "mobileUrl": ...}}}.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional
from urllib.parse import urlparse, urlunparse

from trendradar.utils.url import normalize_url

# source key -> (platform_id used in TrendRadar storage, display name)
PLATFORMS: Dict[str, tuple] = {
    "google_news": ("bd-google-news", "Google News"),
    "google": ("bd-google", "Google"),
    "reddit": ("bd-reddit", "Reddit"),
    "x": ("bd-x", "X"),
    "youtube": ("bd-youtube", "YouTube"),
    "web": ("bd-web", "Web"),
}

# Order used when the same link is found by several sources: the first wins
# the display slot, the others still count towards recurrence.
SOURCE_PRIORITY = ["google_news", "web", "google", "reddit", "x", "youtube"]


@dataclass
class CollectedItem:
    title: str
    url: str
    source: str                                  # key of PLATFORMS
    published_at: Optional[datetime] = None      # timezone-aware (UTC)
    engagement: Optional[float] = None           # combined raw engagement, None = no metrics
    position: Optional[int] = None               # 1-based position in a SERP result list
    position_total: Optional[int] = None         # number of results in that list
    metrics: Dict[str, float] = field(default_factory=dict)
    query: str = ""                              # keyword / subreddit / account / page it came from
    publisher: str = ""

    @property
    def platform_id(self) -> str:
        return PLATFORMS[self.source][0]

    @property
    def key(self) -> str:
        return url_key(self.url)

    @property
    def title_key(self) -> str:
        return title_key(self.title)


_HOST_PREFIXES = ("www.", "m.", "mobile.", "old.", "amp.")
_HOST_ALIASES = {"twitter.com": "x.com", "youtu.be": "youtube.com"}


def url_key(url: str) -> str:
    """Canonical form of a URL used for dedup and recurrence across runs and days."""
    if not url:
        return ""
    url = normalize_url(url.strip())
    try:
        p = urlparse(url)
    except ValueError:
        return url
    host = (p.hostname or "").lower()
    for prefix in _HOST_PREFIXES:
        if host.startswith(prefix):
            host = host[len(prefix):]
    host = _HOST_ALIASES.get(host, host)
    path = p.path or "/"
    if host == "youtube.com" and p.netloc.endswith("youtu.be"):
        # youtu.be/<id> -> youtube.com/watch?v=<id>
        return f"https://youtube.com/watch?v={path.strip('/')}"
    if len(path) > 1:
        path = path.rstrip("/")
    query = p.query
    if host == "youtube.com" and path == "/watch":
        v = [kv for kv in query.split("&") if kv.startswith("v=")]
        query = v[0] if v else query
    return urlunparse(("https", host, path, "", query, ""))


_WORD_RE = re.compile(r"[\w']+", re.UNICODE)
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are",
    "at", "by", "from", "as", "its", "it", "this", "that", "be", "was", "after", "new",
}


def title_key(title: str, words: int = 8) -> str:
    """Rough story fingerprint: first significant words of the title, lower-cased.

    Used to notice the same story on different sites (different URLs).
    """
    tokens = [t for t in _WORD_RE.findall((title or "").lower()) if t not in _STOP and len(t) > 1]
    # drop a trailing " - Publisher" style suffix by ignoring very short titles' tails
    return " ".join(tokens[:words])


def clean_title(text: str, max_len: int = 220) -> str:
    """Single line, trimmed, length-limited title (X posts and YouTube titles can be long)."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text
