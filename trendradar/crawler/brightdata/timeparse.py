# coding=utf-8
"""
Parse the many date formats Bright Data sources return into aware UTC datetimes.

Handles ISO 8601 ("2026-03-31T23:43:21.000Z"), RFC 2822, plain dates
("Nov 23, 2026", "2026-11-23") and relative SERP dates ("3 hours ago",
"1 day ago", "yesterday", "2 mins ago").
"""

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, Union

_REL_RE = re.compile(
    r"(?P<n>\d+|an?|one)\s*(?P<unit>sec|second|min|minute|hr|hour|h|day|d|week|wk|w|month|mo|year|yr|y)s?\.?\s*ago",
    re.IGNORECASE,
)
_UNIT_SECONDS = {
    "sec": 1, "second": 1,
    "min": 60, "minute": 60,
    "hr": 3600, "hour": 3600, "h": 3600,
    "day": 86400, "d": 86400,
    "week": 604800, "wk": 604800, "w": 604800,
    "month": 2592000, "mo": 2592000,
    "year": 31536000, "yr": 31536000, "y": 31536000,
}
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
    "%m-%d-%Y",
)


def parse_time(value: Union[str, int, float, None], now: Optional[datetime] = None) -> Optional[datetime]:
    """Best-effort parse; returns None when the value is missing or unrecognized."""
    if value is None or value == "":
        return None
    now = now or datetime.now(timezone.utc)

    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:  # milliseconds
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None
    lower = text.lower()

    if lower in ("just now", "now"):
        return now
    if lower == "yesterday":
        return now - timedelta(days=1)
    if lower == "today":
        return now

    m = _REL_RE.search(lower)
    if m:
        n_raw = m.group("n").lower()
        n = 1 if n_raw in ("a", "an", "one") else int(n_raw)
        unit = m.group("unit").lower()
        return now - timedelta(seconds=n * _UNIT_SECONDS[unit])

    # ISO 8601
    iso = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(iso)
        return _aware(dt)
    except ValueError:
        pass

    # RFC 2822
    try:
        dt = parsedate_to_datetime(text)
        if dt is not None:
            return _aware(dt)
    except (TypeError, ValueError, IndexError):
        pass

    for fmt in _DATE_FORMATS:
        try:
            return _aware(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
