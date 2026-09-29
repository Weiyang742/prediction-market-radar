from datetime import datetime, timedelta, timezone

from data.config import Config
from data.models import Market, Platform, PricePoint, SignalType
from analysis.signals import (detect_acceleration, detect_jump, detect_spread_shock,
                              detect_stale_quote, detect_volume_spike)


def _pts(prices, volumes=None, spreads=None, step=timedelta(minutes=1)):
    now = datetime.now(timezone.utc)
    vols = volumes or [100.0] * len(prices)
    sprs = spreads or [None] * len(prices)
    return [PricePoint(market_id="m", ts=now - step * (len(prices) - i),
                       price=p, volume=v, spread=s, asof_ts=now)
            for i, (p, v, s) in enumerate(zip(prices, vols, sprs))]


def _market(price=0.5, spread=0.01):
    return Market(market_id="m", platform=Platform.POLYMARKET, question="q",
                  yes_price=price, volume_24h=1e6, spread=spread,
                  asof_ts=datetime.now(timezone.utc))


def test_flat_series_produces_no_jump_accel_volume():
    cfg = Config()
    s = _pts([0.5] * 90)
    assert detect_jump(s, cfg) is None
    assert detect_acceleration(s, cfg) is None
    assert detect_volume_spike(s, cfg) is None


def test_jump_detected():
    cfg = Config()
    prices = [0.5] * 59 + [0.56]
    s = _pts(prices)
    sig = detect_jump(s, cfg)
    assert sig is not None
    assert sig.type is SignalType.JUMP


def test_acceleration_detected_on_ramp():
    cfg = Config()
    prices = [0.5 + 0.002 * i for i in range(16)] + [0.53 + 0.01 * i for i in range(16)]
    sig = detect_acceleration(_pts(prices), cfg)
    assert sig is not None
    assert sig.type is SignalType.ACCELERATION


def test_volume_spike_detected():
    cfg = Config()
    vols = [100.0 + (i % 7) for i in range(59)] + [900.0]
    sig = detect_volume_spike(_pts([0.5] * 60, volumes=vols), cfg)
    assert sig is not None
    assert sig.type is SignalType.VOLUME_SPIKE


def test_spread_shock_detected():
    cfg = Config()
    s = _pts([0.5] * 10, spreads=[0.01] * 9 + [0.04])
    sig = detect_spread_shock(s, _market(spread=0.04), cfg)
    assert sig is not None
    assert sig.type is SignalType.SPREAD_SHOCK


def test_spread_shock_returns_none_without_history():
    cfg = Config()
    s = _pts([0.5] * 10, spreads=[None] * 10)
    assert detect_spread_shock(s, _market(spread=0.04), cfg) is None


def test_stale_quote_detected():
    cfg = Config()
    old = datetime.now(timezone.utc) - timedelta(minutes=30)
    sig = detect_stale_quote(_market(), cfg, last_update=old)
    assert sig is not None
    assert sig.type is SignalType.STALE_QUOTE


def test_fresh_quote_not_stale():
    cfg = Config()
    fresh = datetime.now(timezone.utc)
    assert detect_stale_quote(_market(), cfg, last_update=fresh) is None
