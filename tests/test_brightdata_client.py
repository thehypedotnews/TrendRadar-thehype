# coding=utf-8
"""Tests for the Bright Data client and request budget (no network)."""

import json
import unittest

from trendradar.crawler.brightdata.budget import RequestBudget
from trendradar.crawler.brightdata.client import BrightDataClient
from trendradar.crawler.brightdata.errors import BrightDataError, BudgetExceeded, classify_http_error


class FakeResponse:
    def __init__(self, status_code=200, body="", headers=None):
        self.status_code = status_code
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """Returns queued responses per (method, path-prefix)."""

    def __init__(self, routes):
        self.routes = routes  # list of (method, path_contains, response or list)
        self.headers = {}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for m, frag, resp in self.routes:
            if m == method and frag in url:
                if isinstance(resp, list):
                    return resp.pop(0) if len(resp) > 1 else resp[0]
                return resp
        raise AssertionError(f"unexpected call {method} {url}")


def make_client(routes, **budget_kwargs):
    budget = RequestBudget(**budget_kwargs)
    session = FakeSession(routes)
    client = BrightDataClient("test-key", budget, session=session, sleep=lambda s: None)
    return client, budget, session


class ClassifyTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(classify_http_error(401, "Invalid credentials").kind, "auth")
        self.assertEqual(classify_http_error(402, "").kind, "balance")
        self.assertEqual(classify_http_error(403, "Insufficient balance").kind, "balance")
        self.assertEqual(classify_http_error(403, "zone forbidden").kind, "auth")
        self.assertEqual(classify_http_error(429, "slow down").kind, "rate_limit")
        self.assertEqual(classify_http_error(503, "").kind, "server")
        self.assertEqual(classify_http_error(400, "bad zone").kind, "bad_request")
        self.assertTrue(classify_http_error(401, "").is_fatal)
        self.assertFalse(classify_http_error(429, "").is_fatal)


class BudgetTests(unittest.TestCase):
    def test_per_run_limit(self):
        b = RequestBudget(max_requests_per_run=2)
        for _ in range(2):
            b.reserve_request("serp")
            b.record_request("serp")
        with self.assertRaises(BudgetExceeded):
            b.reserve_request("serp")

    def test_daily_limit_counts_previous_runs(self):
        b = RequestBudget(max_requests_per_day=10, used_requests_today_before=10)
        with self.assertRaises(BudgetExceeded):
            b.reserve_request("serp")

    def test_record_limit(self):
        b = RequestBudget(max_records_per_day=100, used_records_today_before=100)
        with self.assertRaises(BudgetExceeded):
            b.reserve_request("reddit", expected_records=10)
        # a zone request (no records) is still allowed
        b.reserve_request("serp")

    def test_summary(self):
        b = RequestBudget(max_requests_per_day=50)
        b.record_request("serp")
        b.add_records("reddit", 7)
        text = "\n".join(b.summary_lines())
        self.assertIn("1 requests, 7 records", text)
        self.assertIn("/ 50", text)


class ClientTests(unittest.TestCase):
    def test_zone_request_counts_and_sends_zone(self):
        client, budget, session = make_client([("POST", "/request", FakeResponse(200, '{"organic": []}'))])
        body = client.zone_request("serp_zone", "https://www.google.com/search?q=x", "serp")
        self.assertIn("organic", body)
        self.assertEqual(budget.requests, 1)
        sent = json.loads(session.calls[0][2]["data"])
        self.assertEqual(sent["zone"], "serp_zone")
        self.assertEqual(sent["format"], "raw")
        self.assertEqual(session.headers["Authorization"], "Bearer test-key")

    def test_auth_error_is_not_retried(self):
        client, budget, session = make_client([("POST", "/request", FakeResponse(401, "Invalid credentials"))])
        with self.assertRaises(BrightDataError) as ctx:
            client.zone_request("z", "https://example.com", "web")
        self.assertEqual(ctx.exception.kind, "auth")
        self.assertEqual(len(session.calls), 1)

    def test_server_error_retried_then_ok(self):
        client, _, session = make_client(
            [("POST", "/request", [FakeResponse(503, "busy"), FakeResponse(200, "ok")])]
        )
        self.assertEqual(client.zone_request("z", "https://example.com", "web"), "ok")
        self.assertEqual(len(session.calls), 2)

    def test_budget_blocks_before_sending(self):
        client, _, session = make_client([], max_requests_per_run=0, max_requests_per_day=1, used_requests_today_before=1)
        with self.assertRaises(BudgetExceeded):
            client.zone_request("z", "https://example.com", "web")
        self.assertEqual(session.calls, [])

    def test_trigger_poll_download(self):
        records = [{"title": "a", "url": "https://reddit.com/1"}, {"error": "bad input"}]
        client, budget, session = make_client(
            [
                ("POST", "/datasets/v3/trigger", FakeResponse(200, {"snapshot_id": "s_1"})),
                ("GET", "/progress/s_1", [FakeResponse(200, {"status": "running"}), FakeResponse(200, {"status": "ready"})]),
                ("GET", "/snapshot/s_1", FakeResponse(200, records)),
            ]
        )
        job = client.trigger("gd_x", [{"keyword": "ai"}], "reddit", discover_by="keyword", limit_per_input=5)
        params = session.calls[0][2]["params"]
        self.assertEqual(params["type"], "discover_new")
        self.assertEqual(params["discover_by"], "keyword")
        self.assertEqual(json.loads(session.calls[0][2]["data"])["limit_per_input"], 5)
        out = client.wait_for_jobs([job], max_wait=60, poll_interval=0)
        self.assertEqual(len(out["s_1"]), 1)
        self.assertEqual(budget.requests, 1)
        self.assertEqual(budget.records, 1)

    def test_failed_job(self):
        client, _, _ = make_client(
            [
                ("POST", "/datasets/v3/trigger", FakeResponse(200, {"snapshot_id": "s_2"})),
                ("GET", "/progress/s_2", FakeResponse(200, {"status": "failed"})),
            ]
        )
        job = client.trigger("gd_x", [{"url": "u"}], "x")
        out = client.wait_for_jobs([job], max_wait=10, poll_interval=0)
        self.assertIsInstance(out["s_2"], BrightDataError)

    def test_job_timeout(self):
        client, _, _ = make_client(
            [
                ("POST", "/datasets/v3/trigger", FakeResponse(200, {"snapshot_id": "s_3"})),
                ("GET", "/progress/s_3", FakeResponse(200, {"status": "running"})),
            ]
        )
        job = client.trigger("gd_x", [{"url": "u"}], "x")
        out = client.wait_for_jobs([job], max_wait=0, poll_interval=0)
        self.assertEqual(out["s_3"].kind, "timeout")


if __name__ == "__main__":
    unittest.main()
