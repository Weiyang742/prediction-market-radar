"""Cross-platform divergence + negation pair + theme pairing."""
from datetime import datetime, timezone

import pytest

from data.config import Config
from data.models import Market, Platform, Severity
from analysis.divergence import (cross_platform_divergence, find_pairs,
                                 group_sum_deviation, negation_pair_deviation)

NOW = datetime.now(timezone.utc)


def _market(mid, platform, question, price, theme, group_key):
    return Market(market_id=mid, platform=platform, question=question,
                  yes_price=price, volume_24h=1.0, spread=0.01, close_time=None,
                  group_key=group_key, theme=theme, asof_ts=NOW)


def test_cross_platform_divergence_thresholds():
    cfg = Config()
    assert cross_platform_divergence(0.50, 0.51, cfg) is None   # 1pp < 3pp
    sig = cross_platform_divergence(0.50, 0.54, cfg)            # 4pp
    assert sig is not None and sig.severity is Severity.MED
    sig2 = cross_platform_divergence(0.50, 0.56, cfg)           # 6pp
    assert sig2.severity is Severity.HIGH


def test_negation_pair_deviation_thresholds():
    cfg = Config()
    assert negation_pair_deviation(0.50, 0.50, cfg) is None     # 0pp
    sig = negation_pair_deviation(0.55, 0.55, cfg)              # 10pp
    assert sig is not None and sig.severity is Severity.HIGH


def test_group_sum_deviation_pp():
    assert group_sum_deviation([0.4, 0.4, 0.4]) == pytest.approx(20.0)  # |1.2-1| * 100


def test_find_pairs_pairs_same_theme_and_confirms():
    cfg = Config()
    pm = [_market("pm1", Platform.POLYMARKET, "Fed cuts rates?", 0.42, "fed_rates", "g1")]
    ks = [_market("ks1", Platform.KALSHI, "Fed cuts rates?", 0.44, "fed_rates", "g1"),
          _market("ks2", Platform.KALSHI, "Fed holds rates?", 0.47, "fed_rates", "g1")]
    pairs = find_pairs(pm, ks, cfg, confirmer=lambda q1, q2: True)
    assert len(pairs) == 1
    assert pairs[0][0].market_id == "pm1"
    assert pairs[0][1].market_id == "ks1"


def test_find_pairs_skips_unconfirmed():
    cfg = Config()
    pm = [_market("pm1", Platform.POLYMARKET, "Fed cuts rates?", 0.42, "fed_rates", "g1")]
    ks = [_market("ks1", Platform.KALSHI, "Fed cuts rates?", 0.44, "fed_rates", "g1")]
    assert find_pairs(pm, ks, cfg, confirmer=lambda q1, q2: False) == []
