"""News/evidence retrieval for the proactive research agent (v2).

Two backends share one interface:

- ``MockNewsClient`` — deterministic, key-free, offline; used by ``--mock``.
- ``SearchAPIClient`` — a Serper-compatible web-search endpoint (ready to be
  unlocked by a proxy); raises ``NewsError`` when no key is set (never a silent
  empty result — 铁律 5).

Every returned ``Evidence`` is tagged with a recency tier (minute/hour/day,
FinAgent) so the Judge prompt can render it explicitly.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from data.config import Config
from data.models import Evidence, Freshness

_SEARCH_KEY_HEADER = "X-API-KEY"


class NewsError(RuntimeError):
    pass


def freshness_of(published_at: datetime | None, asof_ts: datetime) -> Freshness:
    """Tag evidence recency: <1h -> minute, <24h -> hour, else/unknown -> day."""
    if published_at is None:
        return Freshness.DAY
    age_s = (asof_ts - published_at).total_seconds()
    if age_s < 3600:
        return Freshness.MINUTE
    if age_s < 86400:
        return Freshness.HOUR
    return Freshness.DAY


class NewsClient:
    def search(self, query: str, asof_ts: datetime, limit: int = 5) -> list[Evidence]:
        raise NotImplementedError

    def is_available(self) -> bool:
        return True

    def close(self) -> None:
        pass


# Deterministic synthetic corpus for --mock.  These are placeholders, not real
# news: they exist only to exercise the recipe loop and evidence pipeline.
_MOCK_TEMPLATES = [
    "developments around {q} led participants to reprice odds",
    "ahead of the deadline for {q}, volume concentrated on one side",
    "the latest data point related to {q} surprised some traders",
    "liquidity thinned for {q} as the event neared",
    "headline risk around {q} briefly widened the spread",
    "counterparties disagreed on the base rate implied by {q}",
]


def _mock_seed(query: str) -> int:
    return int(hashlib.md5(query.encode()).hexdigest()[:8], 16)


def mock_news(query: str, asof_ts: datetime, limit: int = 5) -> list[Evidence]:
    """Deterministic synthetic evidence for offline runs (clearly not real)."""
    rng = random.Random(_mock_seed(query))
    n = min(limit, len(_MOCK_TEMPLATES))
    out: list[Evidence] = []
    for i in range(n):
        age_min = rng.choice([25, 90, 360, 720, 2880])
        published = asof_ts - timedelta(minutes=age_min)
        out.append(Evidence(
            quote=_MOCK_TEMPLATES[i].format(q=query),
            source=f"mock-news:{i}",
            published_at=published,
            asof_ts=asof_ts,
            freshness=freshness_of(published, asof_ts),
        ))
    return out


class MockNewsClient(NewsClient):
    def search(self, query: str, asof_ts: datetime, limit: int = 5) -> list[Evidence]:
        return mock_news(query, asof_ts, limit)


def _parse_date(value: Any, asof_ts: datetime) -> datetime | None:
    """Parse the loose date strings a search API may return; None on failure."""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


class SearchAPIClient(NewsClient):
    """Serper-compatible web search (default https://google.serper.dev/search).

    Requires ``SEARCH_API_KEY``.  Works behind ``HTTPS_PROXY`` once a proxy is
    purchased; until then it simply reports unavailable.
    """

    def __init__(self, config: Config) -> None:
        self.api_key = config.search_api_key
        self.url = config.search_api_url.rstrip("/")
        self._client = httpx.Client(timeout=config.request_timeout_s)

    def close(self) -> None:
        self._client.close()

    def is_available(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, asof_ts: datetime, limit: int = 5) -> list[Evidence]:
        if not self.api_key:
            raise NewsError("SEARCH_API_KEY is not set — evidence empty (铁律 5)")
        try:
            resp = self._client.post(
                self.url, json={"q": query, "num": limit},
                headers={_SEARCH_KEY_HEADER: self.api_key})
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPError as exc:
            raise NewsError(f"search request failed: {exc}") from exc
        out: list[Evidence] = []
        for item in body.get("organic", [])[:limit]:
            quote = str(item.get("snippet") or item.get("title") or "").strip()
            source = str(item.get("link") or item.get("source") or "serper")
            published = _parse_date(item.get("date"), asof_ts)
            out.append(Evidence(
                quote=quote, source=source, published_at=published,
                asof_ts=asof_ts, freshness=freshness_of(published, asof_ts),
            ))
        return out


def make_news_client(config: Config) -> NewsClient:
    if config.mock:
        return MockNewsClient()
    return SearchAPIClient(config)
