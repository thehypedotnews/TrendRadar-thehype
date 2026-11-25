# coding=utf-8
"""
Reddit, X and YouTube through the Bright Data Web Scraper API.

Jobs are triggered first (one request per discovery method per platform),
then all of them are polled together, so the run waits for the slowest job
only once. Bright Data bills these per delivered record, so every job sets
limit_per_input.

Dataset IDs and discovery methods (docs.brightdata.com, November 2026):
  Reddit posts   gd_lvz8ah06191smkebj4  discover_by=keyword       {keyword, date, num_of_posts}
                                        discover_by=subreddit_url {url, sort_by}
  X posts        gd_lwxkxvnf1cynvib9co  discover_by=profile_url   {url}
                 (X has no keyword discovery; use accounts, or Google with site:x.com)
  YouTube videos gd_lk56epmy2i5g7lzu0k  discover_by=keyword       {keyword, num_of_posts, start_date}
                                        discover_by=url           {url, num_of_posts, start_date}  (channel)
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..client import BrightDataClient, PendingJob
from ..errors import BrightDataError
from ..models import CollectedItem, clean_title
from ..timeparse import parse_time

DEFAULT_DATASETS = {
    "reddit": "gd_lvz8ah06191smkebj4",
    "x": "gd_lwxkxvnf1cynvib9co",
    "youtube": "gd_lk56epmy2i5g7lzu0k",
}

# Values are case-sensitive and must match Reddit's own labels. The docs list "Past day",
# but the API has rejected it, so each recency has fallbacks tried on a validation error.
REDDIT_DATES = {
    "hour": ["Past hour"],
    "day": ["Past 24 hours", "Today", "Past day"],
    "week": ["Past week"],
    "month": ["Past month"],
    "year": ["Past year"],
}
REDDIT_SORTS = {"hot": ["Hot", "hot"], "new": ["New", "new"], "top": ["Top", "top"], "rising": ["Rising", "rising"]}


def _num(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").lower()
    mult = 1.0
    if s.endswith("k"):
        mult, s = 1e3, s[:-1]
    elif s.endswith("m"):
        mult, s = 1e6, s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return 0.0


def _handle_to_url(handle: str, base: str) -> str:
    h = str(handle).strip()
    if h.startswith("http"):
        return h
    return f"{base}/{h.lstrip('@')}"


# --------------------------------------------------------------- job planning

def plan_jobs(platform: str, cfg: Dict) -> List[Tuple[str, Optional[str], List[Dict], int]]:
    """Return [(description, discover_by, inputs, limit_per_input)] for one platform."""
    limit = int(cfg.get("posts_per_input", 10))
    jobs = []
    if platform == "reddit":
        date = REDDIT_DATES.get(str(cfg.get("recency", "day")).lower(), REDDIT_DATES["day"])[0]
        kws = [k for k in (cfg.get("keywords") or []) if str(k).strip()]
        if kws:
            jobs.append(("keywords", "keyword",
                         [{"keyword": str(k), "date": date, "num_of_posts": limit} for k in kws], limit))
        subs = [s for s in (cfg.get("subreddits") or []) if str(s).strip()]
        if subs:
            sort_by = REDDIT_SORTS.get(str(cfg.get("sort_by", "hot")).lower(), ["Hot"])[0]
            inputs = []
            for s in subs:
                s = str(s).strip()
                url = s if s.startswith("http") else f"https://www.reddit.com/r/{s.removeprefix('r/').strip('/')}/"
                inputs.append({"url": url, "sort_by": sort_by})
            jobs.append(("subreddits", "subreddit_url", inputs, limit))
    elif platform == "x":
        accounts = [a for a in (cfg.get("accounts") or []) if str(a).strip()]
        if accounts:
            jobs.append(("accounts", "profile_url",
                         [{"url": _handle_to_url(a, "https://x.com")} for a in accounts], limit))
    elif platform == "youtube":
        days = int(cfg.get("max_age_days", 2))
        start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%m-%d-%Y")
        kws = [k for k in (cfg.get("keywords") or []) if str(k).strip()]
        if kws:
            inputs = [{"keyword": str(k), "num_of_posts": limit, "start_date": start} for k in kws]
            if cfg.get("country"):
                for i in inputs:
                    i["country"] = str(cfg["country"]).upper()
            jobs.append(("keywords", "keyword", inputs, limit))
        channels = [c for c in (cfg.get("channels") or []) if str(c).strip()]
        if channels:
            jobs.append(("channels", "url",
                         [{"url": _handle_to_url(c, "https://www.youtube.com"), "num_of_posts": limit, "start_date": start}
                          for c in channels], limit))
    return jobs


def trigger_platform(
    client: BrightDataClient, platform: str, cfg: Dict, errors: List[BrightDataError]
) -> List[Tuple[PendingJob, str]]:
    """Trigger all jobs for a platform; returns [(job, description)]."""
    dataset_id = (cfg.get("dataset_id") or DEFAULT_DATASETS[platform]).strip()
    started = []
    for desc, discover_by, inputs, limit in plan_jobs(platform, cfg):
        left = client.budget.records_left()
        if left < len(inputs):
            errors.append(BrightDataError("budget", f"not enough record budget left for {platform} {desc}", None, platform))
            continue
        # shrink per-input limit so the job cannot exceed the remaining record budget
        limit = max(1, min(limit, left // len(inputs)))
        attempts = _input_variants(platform, inputs)
        for n, variant in enumerate(attempts, 1):
            try:
                job = client.trigger(dataset_id, variant, platform, discover_by=discover_by, limit_per_input=limit)
                print(f"[Bright Data] {platform} {desc}: job {job.snapshot_id} started ({len(variant)} inputs × ≤{limit})")
                started.append((job, desc))
                break
            except BrightDataError as e:
                e.source = e.source or platform
                if e.is_fatal or e.kind == "budget":
                    raise
                retry_field = _rejected_field(e)
                if e.kind == "bad_request" and retry_field and n < len(attempts) and _differs(attempts[n - 1], attempts[n], retry_field):
                    print(f"[Bright Data] {platform} {desc}: '{retry_field}' value rejected, trying the next accepted spelling")
                    continue
                print(f"[Bright Data] {platform} {desc} failed to start: {e.short()}")
                errors.append(e)
                break
    return started


def _rejected_field(e: BrightDataError) -> str:
    for field in ("date", "sort_by"):
        if f'["{field}"' in e.message or f"'{field}'" in e.message or f"{field}:" in e.message:
            return field
    return ""


def _differs(a: List[Dict], b: List[Dict], field: str) -> bool:
    return any(x.get(field) != y.get(field) for x, y in zip(a, b))


def _input_variants(platform: str, inputs: List[Dict]) -> List[List[Dict]]:
    """The planned inputs first, then alternative spellings of case-sensitive values."""
    variants = [inputs]
    if platform != "reddit" or not inputs:
        return variants
    first = inputs[0]
    if "date" in first:
        for options in REDDIT_DATES.values():
            if first["date"] in options:
                for alt in options[options.index(first["date"]) + 1:]:
                    variants.append([dict(i, date=alt) for i in inputs])
    if "sort_by" in first:
        for options in REDDIT_SORTS.values():
            if first["sort_by"] in options:
                for alt in options[options.index(first["sort_by"]) + 1:]:
                    variants.append([dict(i, sort_by=alt) for i in inputs])
    return variants


# --------------------------------------------------------------- normalizing

def parse_records(platform: str, records: List[Dict], query: str = "", now: Optional[datetime] = None) -> List[CollectedItem]:
    out = []
    for r in records:
        item = _parse_one(platform, r, query, now)
        if item and item.title and item.url:
            out.append(item)
    return out


def _parse_one(platform: str, r: Dict, query: str, now: Optional[datetime]) -> Optional[CollectedItem]:
    if platform == "reddit":
        up, com = _num(r.get("num_upvotes")), _num(r.get("num_comments"))
        title = r.get("title") or r.get("description") or ""
        community = r.get("community_name") or ""
        return CollectedItem(
            title=clean_title(title),
            url=r.get("url") or "",
            source="reddit",
            published_at=parse_time(r.get("date_posted"), now),
            engagement=up + 2 * com,
            metrics={"upvotes": up, "comments": com},
            query=query or (f"r/{community}" if community else ""),
            publisher=f"r/{community}" if community else "",
        )
    if platform == "x":
        likes, reposts, replies, views = (_num(r.get(k)) for k in ("likes", "reposts", "replies", "views"))
        text = r.get("description") or r.get("text") or ""
        author = r.get("user_posted") or r.get("name") or ""
        return CollectedItem(
            title=clean_title(text),
            url=r.get("url") or "",
            source="x",
            published_at=parse_time(r.get("date_posted"), now),
            engagement=likes + 2 * reposts + replies,
            metrics={"likes": likes, "reposts": reposts, "replies": replies, "views": views},
            query=query or (f"@{author}" if author else ""),
            publisher=f"@{author}" if author else "",
        )
    if platform == "youtube":
        views, likes, com = _num(r.get("views")), _num(r.get("likes")), _num(r.get("num_comments"))
        channel = r.get("youtuber") or r.get("handle_name") or ""
        return CollectedItem(
            title=clean_title(r.get("title") or ""),
            url=r.get("url") or r.get("video_url") or "",
            source="youtube",
            published_at=parse_time(r.get("date_posted"), now),
            engagement=views / 100.0 + likes + 2 * com,
            metrics={"views": views, "likes": likes, "comments": com},
            query=query,
            publisher=str(channel),
        )
    return None
