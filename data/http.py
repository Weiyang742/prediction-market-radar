"""Shared HTTP plumbing for platform clients.

The two platform clients (Polymarket / Kalshi) differ only in endpoint paths and
response parsing.  Everything else — the httpx session, token-bucket rate
limiter, exponential backoff with retry/429 counters, and context-manager
lifecycle — lives here in one place so the two clients do not reimplement it.
"""
from __future__ import annotations

from typing import Any

import httpx

from data.config import Config
from data.limiter import RateLimiter, run_with_backoff

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


def as_float(value: Any) -> float:
    return float(value)


class BaseClient:
    """Rate-limited HTTP client with backoff and per-call observability counters.

    Subclasses provide the endpoint paths and call ``self.get_json``.  Optional
    ``_after_response`` hook lets a platform react to response headers (e.g.
    Polymarket's ``Poly-RateLimit-Remaining``).
    """

    def __init__(self, config: Config, rate_limit_rps: float) -> None:
        self.config = config
        self._http = httpx.Client(timeout=config.request_timeout_s)
        self._limiter = RateLimiter(rate_limit_rps)
        self.retry_count = 0
        self.http_429_count = 0

    # ---- lifecycle ----
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "BaseClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- request plumbing ----
    def _on_retry(self, exc: Exception) -> None:
        self.retry_count += 1
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
            self.http_429_count += 1

    def _request(self, url: str, params: dict[str, Any] | None = None) -> Any:
        self._limiter.wait()
        resp = self._http.get(url, params=params)
        resp.raise_for_status()
        self._after_response(resp)
        return resp.json()

    def _after_response(self, resp: httpx.Response) -> None:
        """Hook for platform-specific header handling.  Default: no-op."""
        return None

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return run_with_backoff(
            lambda: self._request(url, params),
            attempts=self.config.backoff_max_attempts,
            base=self.config.backoff_base_s,
            cap=self.config.backoff_cap_s,
            jitter=self.config.backoff_jitter_s,
            is_retryable=is_retryable,
            on_retry=self._on_retry,
        )
