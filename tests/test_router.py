from datetime import datetime, timezone

from data.config import Config
from data.models import Severity, Signal, SignalType, Tier
from agents.router import route


def _sig(t: SignalType, sev: Severity) -> Signal:
    return Signal(type=t, severity=sev, detail="x", asof_ts=datetime.now(timezone.utc))


def test_tier0_no_signals():
    r = route([], Config())
    assert r.tier is Tier.T0


def test_tier0_single_low():
    r = route([_sig(SignalType.STALE_QUOTE, Severity.LOW)], Config())
    assert r.tier is Tier.T0


def test_tier1_med_signal():
    r = route([_sig(SignalType.JUMP, Severity.MED)], Config())
    assert r.tier is Tier.T1


def test_tier2_high_signal_hard_fallback():
    r = route([_sig(SignalType.JUMP, Severity.HIGH)], Config())
    assert r.tier is Tier.T2


def test_tier2_divergence_high_escalation():
    cfg = Config()
    r = route([], cfg, divergence_pp=cfg.divergence_high_pp + 1.0)
    assert r.tier is Tier.T2


def test_tier1_divergence_med():
    cfg = Config()
    r = route([], cfg, divergence_pp=cfg.divergence_pp + 0.5)
    assert r.tier is Tier.T1


def test_tier2_group_sum_deviation():
    cfg = Config()
    r = route([], cfg, group_sum_deviation_pp=cfg.group_sum_deviation_pp + 1.0)
    assert r.tier is Tier.T2
