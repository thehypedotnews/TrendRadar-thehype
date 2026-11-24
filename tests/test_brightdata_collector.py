# coding=utf-8
"""Collector, state and ranking tests with a fake Bright Data API (no network)."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from trendradar.core import ranking
from trendradar.core.analyzer import calculate_news_weight
from trendradar.core.ranking import ItemSignals, RankingConfig, percentile_ranks, score
from trendradar.crawler.brightdata.budget import RequestBudget
from trendradar.crawler.brightdata.client import BrightDataClient
from trendradar.crawler.brightdata.collector import BrightDataCollector, enabled_sources, platform_entries
from trendradar.crawler.brightdata.state import BrightDataState

from tests.test_brightdata_client import FakeResponse, FakeSession

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def serp_body():
    return json.dumps({"news": [
        {"title": "Chip maker unveils new AI accelerator", "link": "https://news-a.com/chip", "source": "A", "date": "1 hour ago"},
        {"title": "Old story about chips", "link": "https://news-b.com/old", "source": "B", "date": "5 days ago"},
        {"title": "Regulators publish AI rules", "link": "https://news-c.com/rules?utm_source=g", "source": "C", "date": "6 hours ago"},
    ]})


PAGE = """<html><head><meta property="og:title" content="Regulators publish AI rules">
<link rel="canonical" href="https://news-c.com/rules"></head></html>"""

REDDIT = [
    {"url": "https://www.reddit.com/r/t/comments/1/a/", "title": "Reddit hot thread", "num_upvotes": 900,
     "num_comments": 300, "date_posted": "2026-11-24T10:00:00Z", "community_name": "t"},
    {"url": "https://www.reddit.com/r/t/comments/2/b/", "title": "Reddit quiet thread", "num_upvotes": 3,
     "num_comments": 0, "date_posted": "2026-11-24T10:00:00Z", "community_name": "t"},
]

CFG = {
    "enabled": True,
    "limits": {"max_requests_per_run": 20, "max_requests_per_day": 100},
    "max_age_hours": 48,
    "serp": {"enabled": True, "zone": "serp_z", "queries": ["ai"], "search_type": "news"},
    "reddit": {"enabled": True, "keywords": ["ai"], "posts_per_input": 5},
    "web": {"enabled": True, "zone": "unl_z", "pages": [{"url": "https://news-c.com/rules", "name": "C"}]},
    "x": {"enabled": False, "accounts": ["@a"]},
}


def routes(auth_fail=False):
    if auth_fail:
        return [("POST", "/request", FakeResponse(401, "Invalid credentials")),
                ("POST", "/datasets/v3/trigger", FakeResponse(401, "Invalid credentials")),
                ("GET", "/customer/balance", FakeResponse(200, {"balance": 0}))]
    return [
        ("POST", "/datasets/v3/trigger", FakeResponse(200, {"snapshot_id": "s_r"})),
        ("GET", "/progress/s_r", FakeResponse(200, {"status": "ready"})),
        ("GET", "/snapshot/s_r", FakeResponse(200, REDDIT)),
        ("POST", "/request", [FakeResponse(200, serp_body()), FakeResponse(200, PAGE)]),
    ]


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = BrightDataState(os.path.join(self.tmp, "s.db"))

    def tearDown(self):
        self.state.close()
        ranking.clear_signals()

    def run_collect(self, now=NOW, cfg=CFG, auth_fail=False):
        client = BrightDataClient("k", RequestBudget(), session=FakeSession(routes(auth_fail)), sleep=lambda s: None)
        return BrightDataCollector(cfg, self.state, "UTC", api_key="k", client=client).collect(now)

    def test_enabled_sources(self):
        self.assertEqual(enabled_sources(CFG), ["google_news", "reddit", "web"])
        self.assertEqual([p["id"] for p in platform_entries(CFG)], ["bd-google-news", "bd-reddit", "bd-web"])

    def test_collect_happy_path(self):
        res = self.run_collect()
        self.assertIsNone(res.fatal)
        self.assertFalse(res.all_failed)
        # old SERP item dropped; web page duplicates the SERP "rules" link -> merged
        self.assertEqual(res.skipped_old, 1)
        self.assertEqual(set(res.results), {"bd-google-news", "bd-reddit", "bd-web"})
        self.assertEqual(res.results["bd-web"], {})  # its only item was merged into Google News
        self.assertEqual(len(res.results["bd-google-news"]), 2)
        reddit = res.results["bd-reddit"]
        self.assertEqual(reddit["Reddit hot thread"]["ranks"], [1])
        self.assertEqual(reddit["Reddit quiet thread"]["ranks"], [2])
        self.assertEqual(res.budget.requests, 3)   # 1 trigger + 1 serp + 1 web
        self.assertEqual(res.budget.records, 2)
        self.assertEqual(self.state.usage("2026-11-24"), (3, 2))

    def test_cross_source_recurrence_raises_score(self):
        self.run_collect()
        sig_rules = ranking._SIGNALS["https://news-c.com/rules"]
        self.assertEqual(sig_rules.n_sources, 2)  # google_news + web
        # the analyzer uses the Bright Data score for these URLs
        w = calculate_news_weight({"url": "https://news-c.com/rules", "ranks": [9]}, 5, {"MODE": "brightdata"})
        self.assertGreater(w, 0)
        legacy = calculate_news_weight({"url": "https://news-c.com/rules", "ranks": [9], "count": 1}, 5,
                                       {"MODE": "legacy", "RANK_WEIGHT": .6, "FREQUENCY_WEIGHT": .3, "HOTNESS_WEIGHT": .1})
        self.assertNotAlmostEqual(w, legacy)

    def test_second_run_same_day_counts_recurrence(self):
        self.run_collect()
        self.run_collect(now=NOW + timedelta(hours=2))
        self.assertEqual(ranking._SIGNALS["https://news-a.com/chip"].runs_seen, 2)
        self.assertEqual(self.state.usage("2026-11-24"), (6, 4))

    def test_next_day_skips_already_seen(self):
        self.run_collect()
        res = self.run_collect(now=NOW + timedelta(days=1))
        self.assertGreater(res.skipped_seen, 0)
        self.assertEqual(sum(len(v) for v in res.results.values()), 0)

    def test_auth_failure_is_fatal_and_reported(self):
        res = self.run_collect(auth_fail=True)
        self.assertIsNotNone(res.fatal)
        self.assertEqual(res.fatal.kind, "auth")
        self.assertTrue(res.all_failed)
        self.assertIn("bd-reddit", res.failed_ids)
        self.assertTrue(any("auth" in line for line in res.error_summary()))

    def test_daily_limit_stops_requests(self):
        self.state.add_usage("2026-11-24", 100, 0)
        res = self.run_collect()
        self.assertEqual(res.budget.requests, 0)
        self.assertTrue(any(e.kind == "budget" for e in res.errors))
        self.assertTrue(res.all_failed)


class RedditFallbackTests(unittest.TestCase):
    def test_rejected_date_retries_next_spelling(self):
        from trendradar.crawler.brightdata.sources.social import trigger_platform

        bad = FakeResponse(400, json.dumps({"error": "Invalid input provided", "code": "validation_error",
                                             "errors": [["date", "This value is not allowed"]]}))
        session = FakeSession([("POST", "/datasets/v3/trigger", [bad, FakeResponse(200, {"snapshot_id": "s_ok"})])])
        client = BrightDataClient("k", RequestBudget(), session=session, sleep=lambda s: None)
        errors = []
        jobs = trigger_platform(client, "reddit", {"keywords": ["ai"], "recency": "day"}, errors)
        self.assertEqual(errors, [])
        self.assertEqual(jobs[0][0].snapshot_id, "s_ok")
        dates = [json.loads(c[2]["data"])["input"][0]["date"] for c in session.calls]
        self.assertEqual(dates, ["Past 24 hours", "Today"])


class RankingTests(unittest.TestCase):
    def test_example_from_plan(self):
        now = 1_000_000.0
        sig = ItemSignals(published_ts=now - 3 * 3600, first_seen_ts=now, engagement_pct=0.85, runs_seen=2, n_sources=1)
        self.assertAlmostEqual(score(sig, now), 69.6, delta=0.1)

    def test_unknown_time_penalized(self):
        now = 1_000_000.0
        known = ItemSignals(now, now, 0.5, 1, 1)
        unknown = ItemSignals(None, now, 0.5, 1, 1)
        self.assertGreater(score(known, now), score(unknown, now))

    def test_config_override(self):
        cfg = RankingConfig.from_dict({"half_life_hours": 6, "freshness_weight": 1, "engagement_weight": 0, "recurrence_weight": 0})
        now = 1_000_000.0
        self.assertAlmostEqual(score(ItemSignals(now - 6 * 3600, now, 0, 1, 1), now, cfg), 50.0)

    def test_percentiles(self):
        self.assertEqual(percentile_ranks([5]), [0.5])
        p = percentile_ranks([1, 100, 10])
        self.assertEqual(p, [0.0, 1.0, 0.5])
        self.assertEqual(percentile_ranks([3, 3]), [0.5, 0.5])


if __name__ == "__main__":
    unittest.main()
