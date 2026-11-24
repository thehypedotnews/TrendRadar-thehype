# coding=utf-8
"""
Bright Data error types.

Part of the Bright Data source layer added in this fork (thehypedotnews/TrendRadar-thehype),
licensed GPL-3.0 like the original TrendRadar by sansan0.
"""

from typing import Optional


class BrightDataError(Exception):
    """An error talking to Bright Data, classified so the caller can react to it.

    kind is one of:
      auth        - API key missing, wrong or without permission
      balance     - account has no money / credits left
      budget      - our own per-run or per-day request limit was reached
      rate_limit  - Bright Data returned 429
      bad_request - invalid input (zone name, dataset id, query)
      server      - 5xx from Bright Data
      timeout     - request or snapshot took too long
      network     - connection problem
    """

    FATAL_KINDS = {"auth", "balance"}

    def __init__(
        self,
        kind: str,
        message: str,
        status_code: Optional[int] = None,
        source: str = "",
    ):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status_code = status_code
        self.source = source

    @property
    def is_fatal(self) -> bool:
        """Fatal errors make every following request fail too, so collection stops."""
        return self.kind in self.FATAL_KINDS

    def short(self) -> str:
        code = f" (HTTP {self.status_code})" if self.status_code else ""
        where = f"{self.source}: " if self.source else ""
        return f"{where}{self.kind}{code} — {self.message}"

    def __str__(self) -> str:
        return self.short()


class BudgetExceeded(BrightDataError):
    """Raised before a request would go over the configured limits."""

    def __init__(self, message: str, source: str = ""):
        super().__init__("budget", message, None, source)


_BALANCE_HINTS = (
    "balance",
    "insufficient",
    "funds",
    "payment",
    "billing",
    "credit",
    "recharge",
    "suspended",
)


def classify_http_error(status_code: int, body: str, source: str = "") -> BrightDataError:
    """Turn an HTTP error response from Bright Data into a BrightDataError."""
    text = (body or "").strip()
    snippet = text[:300]
    lower = text.lower()

    if status_code == 401:
        return BrightDataError("auth", f"API key rejected: {snippet or 'invalid credentials'}", status_code, source)
    looks_like_balance = any(h in lower for h in _BALANCE_HINTS)
    if status_code == 402 or (looks_like_balance and status_code in (400, 403, 407)):
        return BrightDataError("balance", f"Bright Data account cannot pay for requests: {snippet}", status_code, source)
    if status_code == 403:
        return BrightDataError("auth", f"access denied (check API key permissions and zone): {snippet}", status_code, source)
    if status_code == 404:
        return BrightDataError("bad_request", f"not found (check zone name / dataset id): {snippet}", status_code, source)
    if status_code == 429:
        return BrightDataError("rate_limit", f"rate limited: {snippet}", status_code, source)
    if status_code >= 500:
        return BrightDataError("server", f"Bright Data server error: {snippet}", status_code, source)
    return BrightDataError("bad_request", f"request rejected: {snippet}", status_code, source)
