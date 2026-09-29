"""Rate limiting and retry/backoff for the two platform clients.

The clock and sleep function are injectable so tests can assert the exact
backoff sequence with a fake clock (delivery contract: limiter tests must not
hit the network and must verify the backoff schedule).
"""
from __future__ import annotations

import logging
import random
import time
from typing import Callable, Generic, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


class RateLimiter:
    """Token bucket.  ``wait()`` blocks (via the injected sleep) until a token
    is available, then consumes it."""

    def __init__(
        self,
        rate_per_sec: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate = float(rate_per_sec)
        self._clock = clock
        self._sleep = sleep
        self._tokens = self.rate
        self._last = self._clock()

    def wait(self) -> None:
        now = self._clock()
        self._tokens = min(self.rate, self._tokens + (now - self._last) * self.rate)
        self._last = now
        if self._tokens < 1.0:
            self._sleep((1.0 - self._tokens) / self.rate)
            self._tokens = 0.0
        else:
            self._tokens -= 1.0


def backoff_delay(
    attempt: int,
    base: float = 0.25,
    cap: float = 2.0,
    jitter: float = 0.1,
    rng: random.Random | None = None,
) -> float:
    """Exponential backoff: min(base * 2**attempt, cap) + uniform(0, jitter)."""
    rng = rng or random
    return min(base * (2.0**attempt), cap) + rng.uniform(0.0, jitter)


def run_with_backoff(
    fn: Callable[[], T],
    attempts: int = 6,
    base: float = 0.25,
    cap: float = 2.0,
    jitter: float = 0.1,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    is_retryable: Callable[[Exception], bool] | None = None,
    on_retry: Callable[[Exception], None] | None = None,
) -> T:
    """Call ``fn`` up to ``attempts`` times with exponential backoff.

    The error is never swallowed: if all attempts are exhausted, the last
    exception is re-raised.  Each retry is logged (delivery contract: no silent
    error absorption) and reported through ``on_retry`` if provided.
    """
    is_retryable = is_retryable or (lambda _e: True)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # re-raised on final attempt; see below
            last_exc = exc
            if attempt == attempts - 1 or not is_retryable(exc):
                break
            delay = backoff_delay(attempt, base, cap, jitter, rng)
            log.warning("retryable failure (attempt %d/%d): %s; backoff %.2fs",
                        attempt + 1, attempts, exc, delay)
            if on_retry is not None:
                on_retry(exc)
            sleep(delay)
    assert last_exc is not None
    raise last_exc
