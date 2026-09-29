"""CLI report/scan/history renderers produce expected content."""
from datetime import datetime, timezone

from data.config import Config
from data.models import (Decision, Direction, Market, Platform, PricePoint,
                         Severity, Signal, SignalType, Tier)
from report.render import render_history, render_report, render_scan

NOW = datetime.now(timezone.utc)


def _decision():
    return Decision(decision_id="d1", market_id="m1", tier=Tier.T1, theme="x",
                    dominant_signal="jump", router_score=1, snapshot_hash="h",
                    samples=[], aggregate=0.5, debate=None,
                    direction=Direction.YES, p_yes=0.55, confidence=70.0,
                    abstain=False, thesis="t", key_risks=[], model="mock",
                    asof_ts=NOW)


def test_render_report_includes_decision_row():
    out = render_report([_decision()], {"degraded": 1, "cost_usd": 0.015}, Config(), "mock")
    assert "daily report" in out
    assert "m1" in out
    assert "degraded: 1" in out


def test_render_scan_lists_markets_and_signals():
    m = Market(market_id="m1", platform=Platform.KALSHI, question="q",
               yes_price=0.42, volume_24h=100000.0, spread=0.01, close_time=None,
               theme="fed_rates", asof_ts=NOW)
    sig = Signal(type=SignalType.JUMP, severity=Severity.MED, detail="d",
                 value=3.0, asof_ts=NOW)
    out = render_scan([m], {"m1": [sig]}, {"m1": ["wash"]}, Config())
    assert "# scan" in out
    assert "m1" in out
    assert "jump:MED" in out


def test_render_history_empty_and_filled():
    assert "no price points" in render_history("m1", [])
    p = PricePoint(market_id="m1", ts=NOW, price=0.5, volume=1.0, spread=0.01,
                   asof_ts=NOW)
    out = render_history("m1", [p])
    assert "m1" in out
    assert "0.5000" in out
