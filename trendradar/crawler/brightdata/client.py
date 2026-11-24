# coding=utf-8
"""
Thin HTTP client for the Bright Data REST API.

Endpoints used (checked against docs.brightdata.com, November 2026):
  POST /request                          SERP API and Web Unlocker (zone + url)
  POST /datasets/v3/trigger              Web Scraper API job (returns snapshot_id)
  GET  /datasets/v3/progress/{id}        job status: starting | running | ready | failed | canceled
  GET  /datasets/v3/snapshot/{id}        download results (format=json)
  GET  /customer/balance                 account balance (used for clearer error messages)

Auth: "Authorization: Bearer <BRIGHTDATA_API_KEY>".
"""

import json
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from .budget import RequestBudget
from .errors import BrightDataError, classify_http_error

API_BASE = "https://api.brightdata.com"


@dataclass
class PendingJob:
    """A Web Scraper API job that was triggered and is not downloaded yet."""

    source: str
    dataset_id: str
    snapshot_id: str
    started_at: float


class BrightDataClient:
    def __init__(
        self,
        api_key: str,
        budget: RequestBudget,
        timeout: int = 60,
        max_retries: int = 2,
        api_base: str = API_BASE,
        session: Optional[requests.Session] = None,
        sleep=time.sleep,
    ):
        if not api_key:
            raise BrightDataError("auth", "BRIGHTDATA_API_KEY is not set")
        self.api_base = api_base.rstrip("/")
        self.budget = budget
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "TrendRadar-BrightData/1.0",
            }
        )
        self._sleep = sleep

    # ------------------------------------------------------------------ http

    def _send(self, method: str, path: str, source: str, **kwargs) -> requests.Response:
        """Send one HTTP call with retries on 429 / 5xx / network errors."""
        url = f"{self.api_base}{path}"
        kwargs.setdefault("timeout", self.timeout)
        attempt = 0
        while True:
            wait: Optional[float] = None
            try:
                resp = self.session.request(method, url, **kwargs)
            except requests.Timeout as e:
                err = BrightDataError("timeout", f"no answer within {kwargs['timeout']}s ({e.__class__.__name__})", None, source)
            except requests.RequestException as e:
                err = BrightDataError("network", f"{e.__class__.__name__}: {e}", None, source)
            else:
                if resp.status_code < 400:
                    return resp
                err = classify_http_error(resp.status_code, resp.text, source)
                if err.kind not in ("rate_limit", "server"):
                    raise err
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait = float(min(int(retry_after), 60))

            attempt += 1
            if attempt > self.max_retries:
                raise err
            if method == "POST" and err.kind in ("timeout", "network"):
                # the call may have reached Bright Data and been billed; don't repeat it
                raise err
            delay = wait if wait is not None else (2 ** attempt + random.uniform(0, 1))
            print(f"[Bright Data] {source}: {err.kind}, retry {attempt}/{self.max_retries} in {delay:.0f}s")
            self._sleep(delay)

    # ------------------------------------------------- SERP API / Web Unlocker

    def zone_request(self, zone: str, target_url: str, source: str, extra: Optional[Dict] = None) -> str:
        """POST /request with a zone (SERP API or Web Unlocker). Returns the raw body text."""
        if not zone:
            raise BrightDataError("bad_request", "zone name is not configured", None, source)
        self.budget.reserve_request(source)
        payload = {"zone": zone, "url": target_url, "format": "raw"}
        if extra:
            payload.update(extra)
        self.budget.record_request(source)
        resp = self._send("POST", "/request", source, data=json.dumps(payload))
        return resp.text

    # ------------------------------------------------------- Web Scraper API

    def trigger(
        self,
        dataset_id: str,
        inputs: List[Dict[str, Any]],
        source: str,
        discover_by: Optional[str] = None,
        limit_per_input: Optional[int] = None,
    ) -> PendingJob:
        """Start a scraper job. Counts one request; records are counted on download."""
        if not inputs:
            raise BrightDataError("bad_request", "no inputs", None, source)
        expected = (limit_per_input or 0) * len(inputs)
        self.budget.reserve_request(source, expected_records=expected or 1)
        params = {"dataset_id": dataset_id, "format": "json", "include_errors": "true"}
        if discover_by:
            params["type"] = "discover_new"
            params["discover_by"] = discover_by
        body: Dict[str, Any] = {"input": inputs}
        if limit_per_input:
            body["limit_per_input"] = limit_per_input
        self.budget.record_request(source)
        resp = self._send("POST", "/datasets/v3/trigger", source, params=params, data=json.dumps(body))
        try:
            snapshot_id = resp.json().get("snapshot_id")
        except ValueError:
            snapshot_id = None
        if not snapshot_id:
            raise BrightDataError("server", f"trigger returned no snapshot_id: {resp.text[:200]}", resp.status_code, source)
        return PendingJob(source, dataset_id, snapshot_id, time.time())

    def wait_for_jobs(
        self, jobs: List[PendingJob], max_wait: int = 420, poll_interval: int = 10,
        optional_ids: Optional[set] = None,
    ) -> Dict[str, Any]:
        """Poll all jobs until ready, failed or out of time.

        Jobs in optional_ids (left over from earlier runs) are collected if they are ready,
        but the run does not keep waiting once only such jobs are left.
        Returns {snapshot_id: list_of_records | BrightDataError}.
        """
        optional_ids = optional_ids or set()
        results: Dict[str, Any] = {}
        pending = {j.snapshot_id: j for j in jobs}
        deadline = time.time() + max_wait
        last_progress: Dict[str, Dict] = {}
        next_report = time.time() + 300
        while pending:
            for sid, job in list(pending.items()):
                try:
                    resp = self._send("GET", f"/datasets/v3/progress/{sid}", job.source)
                    progress = resp.json() or {}
                    last_progress[sid] = progress if isinstance(progress, dict) else {}
                    status = last_progress[sid].get("status", "")
                except BrightDataError as e:
                    results[sid] = e
                    del pending[sid]
                    continue
                except ValueError:
                    status = ""
                if status == "ready":
                    try:
                        results[sid] = self._download(job)
                    except BrightDataError as e:
                        results[sid] = e
                    del pending[sid]
                elif status in ("failed", "canceled"):
                    results[sid] = BrightDataError("server", f"scraper job {status} (snapshot {sid})", None, job.source)
                    del pending[sid]
            if not pending:
                break
            only_optional = all(sid in optional_ids for sid in pending)
            if time.time() >= deadline or only_optional:
                for sid, job in pending.items():
                    age_min = (time.time() - job.started_at) / 60
                    results[sid] = BrightDataError(
                        "timeout",
                        f"snapshot {sid} not ready yet (started {age_min:.0f} min ago; "
                        f"Bright Data says: {_progress_summary(last_progress.get(sid))}); it keeps running at Bright Data",
                        None,
                        job.source,
                    )
                break
            if time.time() >= next_report:
                for sid, job in pending.items():
                    print(f"[Bright Data] waiting for {job.source} job {sid}: {_progress_summary(last_progress.get(sid))}")
                next_report = time.time() + 300
            self._sleep(poll_interval)
        return results

    def _download(self, job: PendingJob) -> List[Dict[str, Any]]:
        resp = self._send("GET", f"/datasets/v3/snapshot/{job.snapshot_id}", job.source, params={"format": "json"})
        if resp.status_code == 202:
            raise BrightDataError("timeout", "snapshot reported ready but is still being built", 202, job.source)
        data = _parse_records(resp.text)
        good = [r for r in data if isinstance(r, dict) and not r.get("error") and not r.get("error_code")]
        bad = len(data) - len(good)
        if bad:
            print(f"[Bright Data] {job.source}: {bad} input(s) returned errors, {len(good)} records OK")
        self.budget.add_records(job.source, len(good))
        return good

    # ----------------------------------------------------------- account

    def balance(self) -> Optional[Dict[str, Any]]:
        """Account balance, or None when the key may not read billing data."""
        try:
            resp = self._send("GET", "/customer/balance", "account")
            return resp.json()
        except (BrightDataError, ValueError):
            return None


def _progress_summary(progress: Optional[Dict]) -> str:
    """Short, secret-free description of a /progress answer for the log."""
    if not progress:
        return "no status received"
    parts = [f"status={progress.get('status', '?')}"]
    for key in ("records", "rows", "errors", "collection_duration", "dataset_size"):
        if key in progress and progress[key] not in (None, ""):
            parts.append(f"{key}={progress[key]}")
    return ", ".join(parts)


def _parse_records(text: str) -> List[Any]:
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        # NDJSON fallback
        out = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out
    if isinstance(data, dict):
        for key in ("data", "results", "records"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return data if isinstance(data, list) else []
