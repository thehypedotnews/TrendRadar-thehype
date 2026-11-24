# coding=utf-8
"""
Bright Data source layer.

Added in the thehypedotnews/TrendRadar-thehype fork (2026-11) of TrendRadar by sansan0.
Collects items through Bright Data's SERP API, Web Scraper API (Reddit, X, YouTube)
and Web Unlocker, and hands them to TrendRadar in its usual hot-list format so the
existing filters, storage, reports and notifications work unchanged.

License: GPL-3.0 (same as the original project).
"""

from .budget import RequestBudget
from .client import BrightDataClient
from .errors import BrightDataError, BudgetExceeded

__all__ = ["RequestBudget", "BrightDataClient", "BrightDataError", "BudgetExceeded"]
