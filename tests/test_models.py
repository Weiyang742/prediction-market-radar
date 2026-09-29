from datetime import datetime, timezone

import pytest

from data.models import Market, Platform, snapshot_hash


def test_snapshot_hash_deterministic_and_order_independent():
    a = {"market": {"id": 1}, "signals": []}
    b = {"signals": [], "market": {"id": 1}}
    assert snapshot_hash(a) == snapshot_hash(b)
    assert snapshot_hash(a) == snapshot_hash(a)


def test_market_rejects_out_of_range_price():
    with pytest.raises(Exception):
        Market(market_id="x", platform=Platform.POLYMARKET, question="q",
               yes_price=1.5, volume_24h=0, spread=0.0,
               asof_ts=datetime.now(timezone.utc))
