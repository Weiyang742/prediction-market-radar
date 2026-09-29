"""Real-response parsing tests, gated on 阶段 0 fixtures.

When a fixture is absent (its API host was unreachable in the build
environment), the test is marked xfail with the reason — never skipped, never
fabricated.  Run ``python -m data.probe`` on a network-enabled machine, then
re-run pytest to exercise the real parsing path.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from data.kalshi_client import (parse_kalshi_candle, parse_kalshi_market,
                                parse_kalshi_settled)
from data.polymarket_client import parse_polymarket_closed, parse_polymarket_market

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str):
    path = FIXTURES / name
    if not path.exists():
        pytest.xfail(
            f"fixture '{name}' missing: 阶段 0 (data/probe.py) needs network access to "
            f"gamma-api.polymarket.com, which is SNI-blocked in this build environment; "
            f"real responses cannot be recorded here and are not fabricated. "
            f"Run `python -m data.probe` on a network-enabled host, then re-run pytest.")
    return json.loads(path.read_text())


def test_parse_polymarket_markets_fixture():
    data = _load("polymarket_markets.json")
    assert isinstance(data, list)
    parsed = [parse_polymarket_market(item, datetime.now(timezone.utc)) for item in data]
    assert all(m.yes_price >= 0 for m in parsed)


def test_parse_kalshi_events_fixture():
    data = _load("kalshi_events.json")
    now = datetime.now(timezone.utc)
    parsed = []
    for event in data.get("events", []):
        for item in event.get("markets", []):
            parsed.append(parse_kalshi_market(item, event, now))
    assert all(m.yes_price >= 0 for m in parsed)


def test_parse_polymarket_closed_fixture():
    data = _load("polymarket_closed.json")
    assert isinstance(data, list)
    parsed = [parse_polymarket_closed(item, datetime.now(timezone.utc)) for item in data]
    assert all(p["outcome"] in (0.0, 1.0) for p in parsed)


def test_parse_kalshi_settled_fixture():
    data = _load("kalshi_settled.json")
    now = datetime.now(timezone.utc)
    parsed = []
    for event in data.get("events", []):
        for item in event.get("markets", []):
            if item.get("status") in ("settled", "finalized") and item.get("market_type") == "binary":
                parsed.append(parse_kalshi_settled(item, event, now))
    assert parsed
    # outcome is 0.0 (NO), 1.0 (YES), or None (void/refund, e.g. 0.5)
    assert all(p["outcome"] in (0.0, 1.0, None) for p in parsed)


def test_parse_kalshi_candles_fixture():
    data = _load("kalshi_candles.json")
    now = datetime.now(timezone.utc)
    ticker = data.get("ticker", "?")
    parsed = [parse_kalshi_candle(c, ticker, now) for c in data.get("candlesticks", [])]
    assert parsed
    assert all(p.price >= 0 for p in parsed)
