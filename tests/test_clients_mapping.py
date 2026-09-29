"""Synthetic field-mapping tests for the two platform parsers.

These exercise OUR mapping code against documented-shape dicts.  They are NOT
API verification — real-response verification is in test_clients.py against
fixtures produced by 阶段 0 (data/probe.py).
"""
from datetime import datetime, timezone

import pytest

from data.models import Platform
from data.kalshi_client import parse_kalshi_market, parse_kalshi_settled
from data.polymarket_client import parse_polymarket_closed, parse_polymarket_market

NOW = datetime.now(timezone.utc)


def test_parse_polymarket_market_mapping():
    item = {
        "id": 123,
        "question": "Fed cuts rates?",
        "outcomePrices": '["0.42", "0.58"]',
        "volume": "8200000",
        "spread": "0.01",
        "liquidity": "100000",
        "endDate": "2026-09-17T18:00:00Z",
        "groupItemTitle": "sep_fomc",
    }
    m = parse_polymarket_market(item, NOW)
    assert m.platform is Platform.POLYMARKET
    assert m.yes_price == 0.42
    assert m.volume_24h == 8200000.0
    assert m.spread == 0.01
    assert m.depth == 100000.0
    assert m.group_key == "sep_fomc"


def test_parse_kalshi_market_mapping():
    event = {"title": "Fed rates", "event_ticker": "KXSEPFED", "series_ticker": "KXSEPFED"}
    item = {
        "ticker": "KXSEPFED-25",
        "yes_sub_title": "cut",
        "yes_bid_dollars": "0.42",
        "yes_ask_dollars": "0.44",
        "volume_24h": 8200000,
        "liquidity": 100000,
        "close_time": "2026-09-17T18:00:00Z",
    }
    m = parse_kalshi_market(item, event, NOW)
    assert m.platform is Platform.KALSHI
    assert m.yes_price == 0.42
    assert m.spread == pytest.approx(0.02)
    assert m.group_key == "KXSEPFED"
    assert m.series_ticker == "KXSEPFED"


def test_parse_polymarket_closed_gt_mapping():
    item = {"id": 1, "outcomePrices": '["1", "0"]', "closedTime": "2026-09-01T00:00:00Z"}
    gt = parse_polymarket_closed(item, NOW)
    assert gt["outcome"] == 1.0


def test_parse_kalshi_settled_gt_mapping():
    item = {"ticker": "KXSEPFED-25", "settlement_value_dollars": "0",
            "settle_time": "2026-09-01T00:00:00Z"}
    event = {"title": "Fed rates", "event_ticker": "KXSEPFED", "series_ticker": "KXSEPFED"}
    gt = parse_kalshi_settled(item, event, NOW)
    assert gt["outcome"] == 0.0
    assert gt["series_ticker"] == "KXSEPFED"
