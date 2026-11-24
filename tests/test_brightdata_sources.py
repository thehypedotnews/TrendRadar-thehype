# coding=utf-8
"""Tests for Bright Data source parsing and normalization (no network)."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from trendradar.crawler.brightdata.models import title_key, url_key
from trendradar.crawler.brightdata.sources.extract import extract_article, extract_listing
from trendradar.crawler.brightdata.sources.serp import build_google_url, is_google_redirect, parse_serp, resolve_redirect
from trendradar.crawler.brightdata.sources.social import parse_records, plan_jobs
from trendradar.crawler.brightdata.timeparse import parse_time

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


class TimeParseTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_time("2026-03-31T23:43:21.000Z"), datetime(2026, 3, 31, 23, 43, 21, tzinfo=timezone.utc))
        self.assertEqual(parse_time("3 hours ago", NOW), NOW - timedelta(hours=3))
        self.assertEqual(parse_time("1 day ago", NOW), NOW - timedelta(days=1))
        self.assertEqual(parse_time("an hour ago", NOW), NOW - timedelta(hours=1))
        self.assertEqual(parse_time("45 mins ago", NOW), NOW - timedelta(minutes=45))
        self.assertEqual(parse_time("Nov 23, 2026").date().isoformat(), "2026-11-23")
        self.assertEqual(parse_time("Tue, 24 Nov 2026 10:00:00 +0200").hour, 8)
        self.assertEqual(parse_time(1764000000).year, 2025)
        self.assertIsNone(parse_time("not a date"))
        self.assertIsNone(parse_time(""))


class UrlKeyTests(unittest.TestCase):
    def test_canonical(self):
        self.assertEqual(url_key("http://www.Example.com/a/?utm_source=x"), "https://example.com/a")
        self.assertEqual(url_key("https://twitter.com/u/status/1"), url_key("https://x.com/u/status/1"))
        self.assertEqual(url_key("https://youtu.be/abc"), "https://youtube.com/watch?v=abc")
        self.assertEqual(url_key("https://www.youtube.com/watch?v=abc&t=10"), "https://youtube.com/watch?v=abc")
        self.assertEqual(url_key("https://old.reddit.com/r/x/comments/1/"), "https://reddit.com/r/x/comments/1")

    def test_title_key(self):
        self.assertEqual(title_key("The OpenAI launches a new model!"), title_key("openai launches new model"))


class SerpTests(unittest.TestCase):
    def test_url(self):
        u = build_google_url("open source ai", True, {"recency": "day", "country": "de", "language": "de"})
        q = parse_qs(urlparse(u).query)
        self.assertEqual(q["tbm"], ["nws"])
        self.assertEqual(q["tbs"], ["qdr:d"])
        self.assertEqual(q["gl"], ["de"])
        self.assertEqual(q["brd_json"], ["1"])
        self.assertNotIn("tbm", parse_qs(urlparse(build_google_url("x", False, {})).query))

    def test_parse_news(self):
        body = json.dumps({"news": [
            {"global_rank": 1, "title": "A happened", "link": "https://a.com/1", "source": "A News", "date": "2 hours ago"},
            {"global_rank": 2, "title": "B happened", "link": "https://b.com/2", "source": "B", "date": "1 day ago"},
            {"global_rank": 3, "title": "", "link": "https://c.com"},
        ]})
        items = parse_serp(body, True, "q", NOW)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].source, "google_news")
        self.assertEqual(items[0].position, 1)
        self.assertEqual(items[0].position_total, 2)
        self.assertEqual(items[0].published_at, NOW - timedelta(hours=2))
        self.assertEqual(items[0].publisher, "A News")

    def test_parse_organic(self):
        body = json.dumps({"organic": [{"global_rank": 1, "title": "T", "link": "https://t.com"}]})
        items = parse_serp(body, False, "q", NOW)
        self.assertEqual(items[0].source, "google")
        self.assertIsNone(items[0].published_at)

    def test_redirects(self):
        self.assertTrue(is_google_redirect("https://www.google.com/goto?url=CAES"))
        self.assertTrue(is_google_redirect("https://news.google.com/url?q=https://a.com/x"))
        self.assertFalse(is_google_redirect("https://news.mit.edu/2026/x"))
        self.assertEqual(resolve_redirect("https://www.google.com/url?q=https://a.com/x&sa=U"), "https://a.com/x")
        self.assertIsNone(resolve_redirect("https://a.com/x"))

        class Resp:
            status_code = 302
            headers = {"Location": "https://real.example/article"}

        class Sess:
            def get(self, url, **kw):
                assert kw["allow_redirects"] is False
                return Resp()

        self.assertEqual(resolve_redirect("https://www.google.com/goto?url=CAES", session=Sess()), "https://real.example/article")

        class Page:
            status_code = 200
            headers = {}
            text = '<html><head><meta http-equiv="refresh" content="0;url=https://real.example/b"></head></html>'

        class PageSess:
            def get(self, url, **kw):
                return Page()

        self.assertEqual(resolve_redirect("https://www.google.com/goto?url=CAES", session=PageSess()), "https://real.example/b")

        class Consent:
            status_code = 200
            headers = {}
            text = '<a href="https://www.gstatic.com/x.js">x</a><a href="https://policies.google.com/">p</a>'

        class ConsentSess:
            def get(self, url, **kw):
                return Consent()

        self.assertIsNone(resolve_redirect("https://www.google.com/goto?url=CAES", session=ConsentSess()))

    def test_non_json(self):
        with self.assertRaises(Exception):
            parse_serp("<html>", True, "q")


class SocialTests(unittest.TestCase):
    def test_plan_reddit(self):
        jobs = plan_jobs("reddit", {"keywords": ["llm"], "subreddits": ["r/technology", "MachineLearning"],
                                    "posts_per_input": 5, "recency": "week"})
        self.assertEqual(jobs[0][1], "keyword")
        self.assertEqual(jobs[0][2][0], {"keyword": "llm", "date": "Past week", "num_of_posts": 5})
        self.assertEqual(jobs[1][2][0]["sort_by"], "Hot")  # case-sensitive, Reddit's own label
        self.assertEqual(jobs[1][1], "subreddit_url")
        self.assertEqual(jobs[1][2][0]["url"], "https://www.reddit.com/r/technology/")
        self.assertEqual(jobs[1][2][1]["url"], "https://www.reddit.com/r/MachineLearning/")

    def test_plan_x_and_youtube(self):
        x = plan_jobs("x", {"accounts": ["@OpenAI", "https://x.com/AnthropicAI"]})
        self.assertEqual(x[0][1], "profile_url")
        self.assertEqual(x[0][2][0]["url"], "https://x.com/OpenAI")
        yt = plan_jobs("youtube", {"keywords": ["ai news"], "channels": ["@veritasium"], "max_age_days": 3})
        self.assertEqual(yt[0][1], "keyword")
        self.assertRegex(yt[0][2][0]["start_date"], r"^\d\d-\d\d-\d{4}$")
        self.assertEqual(yt[1][2][0]["url"], "https://www.youtube.com/veritasium")
        self.assertEqual(plan_jobs("x", {}), [])

    def test_parse_reddit(self):
        items = parse_records("reddit", [{
            "url": "https://www.reddit.com/r/t/comments/1/x/", "title": "Big news", "num_upvotes": 120,
            "num_comments": 30, "date_posted": "2026-11-24T09:00:00.000Z", "community_name": "technology"}])
        self.assertEqual(items[0].engagement, 180)
        self.assertEqual(items[0].publisher, "r/technology")
        self.assertEqual(items[0].platform_id, "bd-reddit")

    def test_parse_x(self):
        items = parse_records("x", [{
            "url": "https://x.com/a/status/1", "description": "Hello\nworld " + "x" * 300, "likes": "1.2K",
            "reposts": 10, "replies": 5, "views": 9000, "user_posted": "a", "date_posted": "2026-11-24T09:00:00Z"}])
        self.assertEqual(items[0].engagement, 1200 + 20 + 5)
        self.assertNotIn("\n", items[0].title)
        self.assertLessEqual(len(items[0].title), 220)

    def test_parse_youtube(self):
        items = parse_records("youtube", [{
            "url": "https://www.youtube.com/watch?v=1", "title": "Video", "views": 10000, "likes": 50,
            "num_comments": 5, "youtuber": "@ch", "date_posted": "2025-04-18T05:26:16.000Z"}])
        self.assertEqual(items[0].engagement, 100 + 50 + 10)

    def test_skip_empty(self):
        self.assertEqual(parse_records("reddit", [{"url": "", "title": "x"}]), [])


ARTICLE = """<html><head><title>Fallback</title>
<meta property="og:title" content="Real headline">
<link rel="canonical" href="/news/real">
<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-11-24T08:00:00Z"}</script>
</head><body><h1>Real headline</h1></body></html>"""

LISTING = """<html><body>
<article><h2><a href="/blog/one">First post title here</a></h2><time datetime="2026-11-23T10:00:00Z">Nov 23</time></article>
<article><a href="/blog/two"><h3>Second post title here</h3></a></article>
<article><p>no link</p></article>
</body></html>"""

LINKS = """<html><body><a href="/press/2026/a">Company announces quarterly results</a>
<a href="/about">About us page link</a></body></html>"""


class ExtractTests(unittest.TestCase):
    def test_article(self):
        r = extract_article(ARTICLE, "https://site.com/news/real?x=1")
        self.assertEqual(r["title"], "Real headline")
        self.assertEqual(r["url"], "https://site.com/news/real")
        self.assertEqual(r["time"], "2026-11-24T08:00:00Z")

    def test_listing_articles(self):
        rows = extract_listing(LISTING, "https://site.com/blog")
        self.assertEqual([r["url"] for r in rows], ["https://site.com/blog/one", "https://site.com/blog/two"])
        self.assertEqual(rows[0]["time"], "2026-11-23T10:00:00Z")
        self.assertEqual(rows[1]["title"], "Second post title here")

    def test_listing_by_pattern(self):
        rows = extract_listing(LINKS, "https://co.com/", link_pattern=r"/press/")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://co.com/press/2026/a")


if __name__ == "__main__":
    unittest.main()
