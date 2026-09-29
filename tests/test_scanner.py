from datetime import datetime, timedelta, timezone

from data.config import Config
from data.models import Market, Platform, PricePoint
from analysis.scanner import flag_wash, scan


def _market(mid, theme, vol=1e6, spread=0.01, depth=1e4, close_days=5, price=0.5):
    now = datetime.now(timezone.utc)
    return Market(market_id=mid, platform=Platform.POLYMARKET, question=f"q {mid}",
                  yes_price=price, volume_24h=vol, spread=spread, depth=depth,
                  close_time=now + timedelta(days=close_days), theme=theme,
                  asof_ts=now)


def test_scan_filters_low_volume():
    cfg = Config()
    m = [_market("a", "x", vol=1_000, depth=10),
         _market("b", "x", vol=1_000_000, depth=10_000)]
    sel, _ = scan(m, {}, cfg)
    assert [x.market_id for x in sel] == ["b"]


def test_scan_filters_wide_spread():
    cfg = Config()
    m = [_market("a", "x", spread=0.10), _market("b", "x", spread=0.01)]
    sel, _ = scan(m, {}, cfg)
    assert [x.market_id for x in sel] == ["b"]


def test_scan_sorts_by_volume_desc():
    cfg = Config()
    m = [_market("a", "x", vol=1_000_000), _market("b", "x", vol=5_000_000)]
    sel, _ = scan(m, {}, cfg)
    assert [x.market_id for x in sel] == ["b", "a"]


def test_wash_criterion1_low_depth_ratio():
    cfg = Config()
    # same theme median depth/volume ratio = 0.01; low one is 0.001 < 0.3*0.01
    a = _market("a", "x", vol=1_000_000, depth=10_000)
    b = _market("b", "x", vol=1_000_000, depth=1_000)
    flagged, reasons = flag_wash(b, [], 0.01, cfg)
    assert flagged
    assert reasons


def test_wash_criterion2_lone_peak_tiny_move():
    cfg = Config()
    now = datetime.now(timezone.utc)
    series = [PricePoint(market_id="b", ts=now - timedelta(minutes=10 - i),
                         price=0.5, volume=100.0, asof_ts=now) for i in range(10)]
    series[-1] = PricePoint(market_id="b", ts=now, price=0.5001, volume=900.0, asof_ts=now)
    b = _market("b", "x")
    flagged, reasons = flag_wash(b, series, None, cfg)
    assert flagged
    assert reasons


def test_wash_not_flagged_when_price_moves():
    cfg = Config()
    now = datetime.now(timezone.utc)
    series = [PricePoint(market_id="b", ts=now - timedelta(minutes=10 - i),
                         price=0.5, volume=100.0, asof_ts=now) for i in range(10)]
    series[-1] = PricePoint(market_id="b", ts=now, price=0.55, volume=900.0, asof_ts=now)
    flagged, _ = flag_wash(_market("b", "x"), series, None, cfg)
    assert not flagged
