"""Real-mode price-series wiring: Kalshi + Polymarket candles are fetched per
market; missing tickers/tokens degrade to a note, never a crash."""
from datetime import datetime, timezone

import main
from data.config import Config
from data.models import Market, Platform, PricePoint

NOW = datetime.now(timezone.utc)


def _points(ticker, n=2):
    return [PricePoint(market_id=ticker, ts=NOW, price=0.5 + i * 0.01,
                       volume=1.0, spread=0.01, asof_ts=NOW) for i in range(n)]


class FakeKalshiClient:
    def __init__(self, config):
        self.config = config
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def fetch_candles(self, series_ticker, ticker, start_ts, end_ts, period_interval=60):
        self.calls.append((series_ticker, ticker))
        return _points(ticker)


class FakePolymarketClient:
    def __init__(self, config, token=None, points=None):
        self.config = config
        self.token = token
        self.points = points or []
        self.resolve_calls = []
        self.candle_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def resolve_clob_token_id(self, market_id):
        self.resolve_calls.append(market_id)
        return self.token

    def fetch_candles(self, clob_token_id):
        self.candle_calls.append(clob_token_id)
        return self.points


def _market(mid, platform, series_ticker=None):
    return Market(market_id=mid, platform=platform, question="q", yes_price=0.5,
                  volume_24h=1.0, spread=0.01, close_time=None, group_key=None,
                  theme=None, series_ticker=series_ticker, asof_ts=NOW)


def test_build_series_fetches_kalshi_candles(monkeypatch):
    kalshi = FakeKalshiClient(None)
    poly = FakePolymarketClient(None, token=None)
    monkeypatch.setattr(main, "KalshiClient", lambda cfg: kalshi)
    monkeypatch.setattr(main, "PolymarketClient", lambda cfg: poly)
    markets = [_market("ks1", Platform.KALSHI, series_ticker="SER1"),
               _market("pm1", Platform.POLYMARKET)]
    notes = []
    series = main._build_series(markets, Config(), notes)
    assert "ks1" in series
    assert len(series["ks1"]) == 2
    assert kalshi.calls == [("SER1", "ks1")]
    assert "pm1" not in series  # no token -> skipped
    assert poly.resolve_calls == ["pm1"]


def test_build_series_fetches_polymarket_series(monkeypatch):
    poly = FakePolymarketClient(None, token="0xabc", points=_points("pm1"))
    monkeypatch.setattr(main, "PolymarketClient", lambda cfg: poly)
    markets = [_market("pm1", Platform.POLYMARKET)]
    notes = []
    series = main._build_series(markets, Config(), notes)
    assert "pm1" in series
    assert poly.candle_calls == ["0xabc"]


def test_build_series_skips_markets_without_series_ticker(monkeypatch):
    kalshi = FakeKalshiClient(None)
    monkeypatch.setattr(main, "KalshiClient", lambda cfg: kalshi)
    markets = [_market("ks1", Platform.KALSHI)]  # no series_ticker
    notes = []
    series = main._build_series(markets, Config(), notes)
    assert series == {}
    assert kalshi.calls == []
    assert any("no real price series fetched" in n for n in notes)
