"""Selective-gate scoring helpers."""
from datetime import datetime, timezone

import pytest

from data.config import Config
from data.models import Decision, Direction, SampleOutput, Tier
from agents import calibrate as cal

NOW = datetime.now(timezone.utc)


def _decision(mid, p_yes):
    samples = [SampleOutput(role="bull", index=0, p_yes=0.6),
               SampleOutput(role="bear", index=0, p_yes=0.4)]
    return Decision(decision_id=f"d-{mid}", market_id=mid, tier=Tier.T1, theme="x",
                    dominant_signal="jump", router_score=1, snapshot_hash="h",
                    samples=samples, aggregate=p_yes, debate=None,
                    direction=Direction.YES if p_yes >= 0.5 else Direction.NO,
                    p_yes=p_yes, confidence=70.0, abstain=False, thesis="t",
                    key_risks=[], model="m", asof_ts=NOW)


def test_decision_abstain_score_combines_disagreement_and_router():
    cfg = Config(abstain_signal_weight=1.0)
    # disagreement = |0.6 - 0.4| * 100 = 20; + router_score(1) * weight(1.0)
    assert cal.decision_abstain_score(_decision("a", 0.5), cfg) == 21.0


def test_selective_brier_keeps_low_score_decisions():
    cfg = Config(abstain_signal_weight=1.0)
    pairs = [(_decision("a", 0.5), 0.0)]
    res = cal.selective_brier(pairs, threshold=25.0, cfg=cfg)
    assert res is not None
    sb, coverage, abstain = res
    assert coverage == 1.0
    assert abstain == 0.0
    assert sb == pytest.approx(0.25)  # Brier(0.5, 0) / coverage 1.0


def test_selective_brier_abstains_high_score_decisions():
    cfg = Config(abstain_signal_weight=1.0)
    pairs = [(_decision("a", 0.5), 0.0)]  # score 21.0
    res = cal.selective_brier(pairs, threshold=10.0, cfg=cfg)
    assert res is None  # everything abstained -> no kept decisions
