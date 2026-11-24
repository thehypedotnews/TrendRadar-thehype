# coding=utf-8
"""
Runs all enabled Bright Data sources and returns TrendRadar hot-list data.

Order of work in one run:
  1. trigger Reddit / X / YouTube scraper jobs (they run at Bright Data meanwhile)
  2. SERP queries and Web Unlocker pages
  3. wait for the scraper jobs
  4. drop too-old items, merge duplicates across sources, score engagement
  5. update the cross-day state, optionally drop items already seen on earlier days
  6. rank and convert to {platform_id: {title: {"ranks": [n], "url": ..., "mobileUrl": ""}}}

Errors never disappear silently: every failed source is listed in the result,
and auth / balance errors stop collection and are reported as fatal.
"""

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pytz

from trendradar.core.ranking import RankingConfig, percentile_ranks, score, set_signals

from .budget import RequestBudget
from .client import BrightDataClient, PendingJob
from .errors import BrightDataError
from .models import PLATFORMS, SOURCE_PRIORITY, CollectedItem
from .sources.serp import collect_serp
from .sources.social import parse_records, trigger_platform
from .sources.web import collect_web
from .state import BrightDataState

SOCIAL = ("reddit", "x", "youtube")


@dataclass
class CollectionResult:
    results: Dict[str, Dict] = field(default_factory=dict)
    id_to_name: Dict[str, str] = field(default_factory=dict)
    failed_ids: List[str] = field(default_factory=list)
    items: List[CollectedItem] = field(default_factory=list)
    errors: List[BrightDataError] = field(default_factory=list)
    fatal: Optional[BrightDataError] = None
    budget: Optional[RequestBudget] = None
    enabled_sources: List[str] = field(default_factory=list)
    collected_before_filters: int = 0
    skipped_seen: int = 0
    skipped_old: int = 0
    balance: Optional[Dict] = None
    carried_over: int = 0

    @property
    def all_failed(self) -> bool:
        """True when Bright Data produced nothing because of errors (not because there was no news)."""
        if self.fatal is not None:
            return True
        return (bool(self.enabled_sources) and self.collected_before_filters == 0 and bool(self.errors)
                and self.carried_over == 0)

    def error_summary(self) -> List[str]:
        lines = []
        if self.fatal:
            lines.append(self.fatal.short())
        seen = set()
        for e in self.errors:
            s = e.short()
            if s not in seen:
                seen.add(s)
                lines.append(s)
        return lines


def enabled_sources(cfg: Dict) -> List[str]:
    """Source keys (PLATFORMS) that this config will produce."""
    out = []
    serp = cfg.get("serp", {}) or {}
    if serp.get("enabled") and serp.get("queries"):
        st = str(serp.get("search_type", "news")).lower()
        if st in ("news", "both"):
            out.append("google_news")
        if st in ("web", "both"):
            out.append("google")
    for p in SOCIAL:
        pc = cfg.get(p, {}) or {}
        if pc.get("enabled") and any(pc.get(k) for k in ("keywords", "subreddits", "accounts", "channels")):
            out.append(p)
    web = cfg.get("web", {}) or {}
    if web.get("enabled") and web.get("pages"):
        out.append("web")
    return out


def platform_entries(cfg: Dict) -> List[Dict]:
    """TrendRadar platform dicts for the enabled Bright Data sources."""
    return [{"id": PLATFORMS[s][0], "name": PLATFORMS[s][1], "provider": "brightdata"} for s in enabled_sources(cfg)]


class BrightDataCollector:
    def __init__(self, cfg: Dict, state: BrightDataState, timezone_name: str = "UTC",
                 api_key: Optional[str] = None, client: Optional[BrightDataClient] = None):
        self.cfg = cfg or {}
        self.state = state
        self.tz = pytz.timezone(timezone_name or "UTC")
        self.api_key = api_key if api_key is not None else os.environ.get("BRIGHTDATA_API_KEY", "")
        self._client = client
        self.ranking_cfg = RankingConfig.from_dict(self.cfg.get("ranking"))

    # ------------------------------------------------------------ helpers

    def _today(self, now: datetime) -> str:
        return now.astimezone(self.tz).strftime("%Y-%m-%d")

    def _day_start_ts(self, now: datetime) -> float:
        local = now.astimezone(self.tz)
        start = self.tz.localize(datetime(local.year, local.month, local.day))
        return start.timestamp()

    def _make_budget(self, now: datetime) -> RequestBudget:
        lim = self.cfg.get("limits", {}) or {}
        req_today, rec_today = self.state.usage(self._today(now))
        return RequestBudget(
            max_requests_per_run=int(lim.get("max_requests_per_run", 0) or 0),
            max_requests_per_day=int(lim.get("max_requests_per_day", 0) or 0),
            max_records_per_run=int(lim.get("max_records_per_run", 0) or 0),
            max_records_per_day=int(lim.get("max_records_per_day", 0) or 0),
            used_requests_today_before=req_today,
            used_records_today_before=rec_today,
        )

    # ---------------------------------------------------------------- run

    def collect(self, now: Optional[datetime] = None) -> CollectionResult:
        now = now or datetime.now(timezone.utc)
        res = CollectionResult(enabled_sources=enabled_sources(self.cfg))
        for s in res.enabled_sources:
            pid, name = PLATFORMS[s]
            res.id_to_name[pid] = name
        if not res.enabled_sources:
            print("[Bright Data] no sources enabled in config (brightdata.*.enabled + queries/keywords/pages)")
            return res

        budget = self._make_budget(now)
        res.budget = budget
        if self._client is not None:
            client = self._client
            client.budget = budget
        else:
            try:
                client = BrightDataClient(
                    self.api_key, budget,
                    timeout=int(self.cfg.get("request_timeout", 60)),
                    max_retries=int(self.cfg.get("max_retries", 2)),
                )
            except BrightDataError as e:
                res.fatal = e
                res.failed_ids = [PLATFORMS[s][0] for s in res.enabled_sources]
                return res

        raw: List[CollectedItem] = []
        errors: List[BrightDataError] = []
        jobs = []
        try:
            # 1. scraper jobs first
            for p in SOCIAL:
                if p in res.enabled_sources:
                    jobs.extend(trigger_platform(client, p, self.cfg.get(p, {}) or {}, errors))
            # 2. SERP + web
            if "google_news" in res.enabled_sources or "google" in res.enabled_sources:
                serp_cfg = self.cfg.get("serp", {}) or {}
                zone = serp_cfg.get("zone") or os.environ.get("BRIGHTDATA_SERP_ZONE", "")
                raw.extend(collect_serp(client, serp_cfg, zone, errors))
            if "web" in res.enabled_sources:
                web_cfg = self.cfg.get("web", {}) or {}
                zone = web_cfg.get("zone") or os.environ.get("BRIGHTDATA_UNLOCKER_ZONE", "")
                raw.extend(collect_web(client, web_cfg, zone, errors))
        except BrightDataError as e:
            if e.kind == "budget":
                print(f"[Bright Data] stopping new requests: {e.short()}")
                errors.append(e)
            else:
                res.fatal = e
                print(f"[Bright Data] FATAL: {e.short()}")

        # 3. scraper results (also after a budget stop: those jobs are already paid for).
        #    Jobs left unfinished by earlier runs are collected here too.
        carried = []
        if res.fatal is None:
            for sid, source, desc, started in self.state.pending_jobs():
                if source in res.enabled_sources:
                    carried.append((PendingJob(source, "", sid, started), f"{desc} (from an earlier run)"))
            if carried:
                print(f"[Bright Data] picking up {len(carried)} scraper job(s) left running by an earlier run")
        all_jobs = carried + jobs
        if all_jobs and res.fatal is None:
            outcome = client.wait_for_jobs([j for j, _ in all_jobs], max_wait=int(self.cfg.get("scraper_max_wait", 420)))
            for job, desc in all_jobs:
                got = outcome.get(job.snapshot_id)
                is_carried = (job, desc) in carried
                if isinstance(got, BrightDataError):
                    if got.kind == "timeout" and self.cfg.get("carry_over_unfinished_jobs", True):
                        # still running at Bright Data and already paid for: collect it next run
                        self.state.add_pending_job(job.snapshot_id, job.source, desc.replace(" (from an earlier run)", ""), job.started_at)
                        print(f"[Bright Data] {job.source} {desc}: still running at Bright Data, results will be "
                              f"collected by the next run")
                        res.carried_over += 1
                        continue
                    if is_carried:
                        self.state.remove_pending_job(job.snapshot_id)
                    print(f"[Bright Data] {job.source} {desc} failed: {got.short()}")
                    errors.append(got)
                    if got.is_fatal:
                        res.fatal = got
                else:
                    self.state.remove_pending_job(job.snapshot_id)
                    items = parse_records(job.source, got or [])
                    print(f"[Bright Data] {job.source} {desc}: {len(items)} items")
                    raw.extend(items)

        res.errors = errors
        res.collected_before_filters = len(raw)

        if res.fatal is not None and res.fatal.kind in ("auth", "balance"):
            res.balance = client.balance()

        # sources that produced nothing and had errors count as failed
        produced = {i.source for i in raw}
        errored = {e.source for e in errors} | ({res.fatal.source} if res.fatal else set())
        for s in res.enabled_sources:
            if s not in produced and (s in errored or res.fatal is not None):
                res.failed_ids.append(PLATFORMS[s][0])

        self._finish(res, raw, budget, now)
        return res

    # ----------------------------------------------------- post-processing

    def _finish(self, res: CollectionResult, raw: List[CollectedItem], budget: RequestBudget, now: datetime) -> None:
        now_ts = now.timestamp()
        max_age_h = float(self.cfg.get("max_age_hours", 48) or 0)

        # too old
        fresh = []
        for it in raw:
            if max_age_h and it.published_at and (now_ts - it.published_at.timestamp()) > max_age_h * 3600:
                res.skipped_old += 1
                continue
            fresh.append(it)

        # duplicates across sources: keep the higher-priority source, remember all sources
        prio = {s: i for i, s in enumerate(SOURCE_PRIORITY)}
        fresh.sort(key=lambda it: prio.get(it.source, 99))
        merged: Dict[str, Tuple[CollectedItem, set]] = {}
        for it in fresh:
            k = it.key
            if not k:
                continue
            if k in merged:
                kept, srcs = merged[k]
                srcs.add(it.source)
                if kept.published_at is None and it.published_at is not None:
                    kept.published_at = it.published_at
                continue
            merged[k] = (it, {it.source})

        items = [it for it, _ in merged.values()]
        eng = self._engagement(items)

        rows = [
            {
                "url_key": it.key,
                "url": it.url,
                "title": it.title,
                "title_key": it.title_key,
                "sources": sorted(srcs),
                "published_at": it.published_at.timestamp() if it.published_at else None,
                "engagement_pct": eng[id(it)],
            }
            for it, srcs in merged.values()
        ]
        first_seen_before = self.state.first_seen(r["url_key"] for r in rows)
        self.state.observe(rows, now_ts)
        self.state.add_usage(self._today(now), budget.requests, budget.records)
        self.state.prune(int(self.cfg.get("state_keep_days", 14)))

        signals = self.state.signals_since(now_ts - 3 * 86400)
        set_signals(signals, self.ranking_cfg, now_ts)

        # items first seen on an earlier day were already offered in an earlier digest
        if self.cfg.get("skip_seen_before_today", True):
            day_start = self._day_start_ts(now)
            kept = []
            for it in items:
                fs = first_seen_before.get(it.key)
                if fs is not None and fs < day_start:
                    res.skipped_seen += 1
                    continue
                kept.append(it)
            items = kept

        # rank within each platform and build TrendRadar results
        def item_score(it: CollectedItem) -> float:
            sig = signals.get(it.key)
            return score(sig, now_ts, self.ranking_cfg) if sig else 0.0

        items.sort(key=item_score, reverse=True)
        res.items = items
        for s in res.enabled_sources:
            res.results.setdefault(PLATFORMS[s][0], {})
        for it in items:
            bucket = res.results.setdefault(it.platform_id, {})
            if it.title in bucket:
                continue
            bucket[it.title] = {"ranks": [len(bucket) + 1], "url": it.url, "mobileUrl": ""}
        # platforms with no items and no errors simply stay empty
        res.results = {pid: v for pid, v in res.results.items() if v or pid not in res.failed_ids}

        print(
            f"[Bright Data] collected {res.collected_before_filters} items → {len(items)} after "
            f"dedup/filters (too old: {res.skipped_old}, seen on earlier days: {res.skipped_seen})"
        )
        for line in budget.summary_lines():
            print(f"[Bright Data] {line}")

    def _engagement(self, items: List[CollectedItem]) -> Dict[int, float]:
        """E in 0..1 per item (keyed by id())."""
        out: Dict[int, float] = {}
        default_web = float(self.cfg.get("ranking", {}).get("web_default_engagement", 0.3)
                            if isinstance(self.cfg.get("ranking"), dict) else 0.3)
        by_source: Dict[str, List[CollectedItem]] = {}
        for it in items:
            if it.engagement is not None:
                by_source.setdefault(it.source, []).append(it)
            elif it.position and it.position_total and it.position_total > 1:
                out[id(it)] = (it.position_total - it.position + 1) / it.position_total
            elif it.position and it.position_total == 1 and it.source != "web":
                out[id(it)] = 1.0
            else:
                out[id(it)] = default_web
        for group in by_source.values():
            for it, p in zip(group, percentile_ranks([g.engagement for g in group])):
                out[id(it)] = p
        return out
