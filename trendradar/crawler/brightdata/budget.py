# coding=utf-8
"""
Request budget for Bright Data.

Bright Data bills SERP API and Web Unlocker per successful request, and the
Web Scraper API (Reddit, X, YouTube) per delivered record. We therefore count
both, and stop *before* a call would exceed a configured limit.

Limits of 0 mean "no limit".
"""

from dataclasses import dataclass, field
from typing import Dict

from .errors import BudgetExceeded


@dataclass
class RequestBudget:
    max_requests_per_run: int = 0
    max_requests_per_day: int = 0
    max_records_per_run: int = 0
    max_records_per_day: int = 0

    # usage already recorded today before this run started (loaded from state)
    used_requests_today_before: int = 0
    used_records_today_before: int = 0

    # usage in this run
    requests: int = 0
    records: int = 0
    by_source: Dict[str, Dict[str, int]] = field(default_factory=dict)

    # --- queries -----------------------------------------------------------

    @property
    def requests_today(self) -> int:
        return self.used_requests_today_before + self.requests

    @property
    def records_today(self) -> int:
        return self.used_records_today_before + self.records

    def records_left(self) -> int:
        """How many more records this run may request (a large number if unlimited)."""
        left = []
        if self.max_records_per_run:
            left.append(self.max_records_per_run - self.records)
        if self.max_records_per_day:
            left.append(self.max_records_per_day - self.records_today)
        return max(0, min(left)) if left else 10**9

    # --- accounting --------------------------------------------------------

    def reserve_request(self, source: str, expected_records: int = 0) -> None:
        """Check that one more request (with up to expected_records records) fits.

        Raises BudgetExceeded instead of letting the request go out.
        """
        if self.max_requests_per_run and self.requests + 1 > self.max_requests_per_run:
            raise BudgetExceeded(
                f"per-run request limit reached ({self.max_requests_per_run})", source
            )
        if self.max_requests_per_day and self.requests_today + 1 > self.max_requests_per_day:
            raise BudgetExceeded(
                f"daily request limit reached ({self.max_requests_per_day})", source
            )
        if expected_records and self.records_left() <= 0:
            raise BudgetExceeded("record limit reached for this run/day", source)

    def record_request(self, source: str, records: int = 0) -> None:
        self.requests += 1
        self.records += max(0, records)
        s = self.by_source.setdefault(source, {"requests": 0, "records": 0})
        s["requests"] += 1
        s["records"] += max(0, records)

    def add_records(self, source: str, records: int) -> None:
        """Add records delivered by a request that was already counted."""
        self.records += max(0, records)
        s = self.by_source.setdefault(source, {"requests": 0, "records": 0})
        s["records"] += max(0, records)

    # --- reporting ---------------------------------------------------------

    def summary_lines(self):
        lines = [
            f"usage this run: {self.requests} requests, {self.records} records",
            f"usage today:    {self.requests_today} requests"
            + (f" / {self.max_requests_per_day}" if self.max_requests_per_day else "")
            + f", {self.records_today} records"
            + (f" / {self.max_records_per_day}" if self.max_records_per_day else ""),
        ]
        for src, s in sorted(self.by_source.items()):
            lines.append(f"  - {src}: {s['requests']} requests, {s['records']} records")
        return lines
