"""News/evidence retrieval tests: recency tiers, mock determinism, no-silent-failure."""
from datetime import datetime, timedelta, timezone

import pytest

from data.config import Config
from data.models import Freshness
from data.news import (NewsError, SearchAPIClient, freshness_of, mock_news,
                       make_news_client)


def _dt(hours_ago: float = 0.0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours_ago)


def test_freshness_tiers():
    asof = _dt()
    assert freshness_of(asof - timedelta(minutes=30), asof) is Freshness.MINUTE
    assert freshness_of(asof - timedelta(hours=5), asof) is Freshness.HOUR
    assert freshness_of(asof - timedelta(days=3), asof) is Freshness.DAY
    assert freshness_of(None, asof) is Freshness.DAY


def test_mock_news_deterministic_and_point_in_time():
    asof = _dt()
    a = mock_news("Will X happen?", asof, limit=3)
    b = mock_news("Will X happen?", asof, limit=3)
    assert [e.quote for e in a] == [e.quote for e in b]
    assert all(e.published_at < asof for e in a)
    assert all(e.quote and e.source for e in a)  # 铁律 4 shape


def test_search_client_raises_without_key():
    cfg = Config(search_api_key=None)
    client = SearchAPIClient(cfg)
    assert not client.is_available()
    with pytest.raises(NewsError):
        client.search("q", _dt(), limit=3)
    client.close()


def test_make_news_client_mock_mode():
    from data.news import MockNewsClient
    assert isinstance(make_news_client(Config(mock=True)), MockNewsClient)
