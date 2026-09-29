"""Negation-pair deviation is wired for 2-market groups; group-sum handles 3+."""
from datetime import datetime, timezone

import main
from data.config import Config
from data.models import Market, Platform, SignalType

NOW = datetime.now(timezone.utc)


def _market(mid, group, price):
    return Market(market_id=mid, platform=Platform.KALSHI, question=f"q{mid}",
                  yes_price=price, volume_24h=1.0, spread=0.01, close_time=None,
                  group_key=group, theme=None, asof_ts=NOW)


def test_negation_signal_for_two_market_group():
    cfg = Config()
    ms = [_market("a", "g1", 0.60), _market("b", "g1", 0.60)]  # sum 1.20 -> 20pp dev
    sigs = main._negation_signals(ms, cfg)
    assert set(sigs) == {"a", "b"}
    assert sigs["a"].type is SignalType.NEGATION_PAIR_DEVIATION
    assert sigs["a"].severity.value == "HIGH"


def test_negation_signal_suppressed_when_below_threshold():
    cfg = Config()
    ms = [_market("a", "g1", 0.51), _market("b", "g1", 0.49)]  # sum 1.00 -> 0pp
    assert main._negation_signals(ms, cfg) == {}


def test_negation_ignores_three_plus_groups():
    cfg = Config()
    ms = [_market("a", "g1", 0.60), _market("b", "g1", 0.60), _market("c", "g1", 0.30)]
    assert main._negation_signals(ms, cfg) == {}


def test_group_dev_only_three_plus_groups():
    ms = [_market("a", "g1", 0.60), _market("b", "g1", 0.60),           # 2-market
          _market("c", "g2", 0.40), _market("d", "g2", 0.30),           # 3-market
          _market("e", "g2", 0.20)]
    gd = main._group_dev(ms)
    assert "a" not in gd and "b" not in gd
    assert "c" in gd and "d" in gd and "e" in gd
