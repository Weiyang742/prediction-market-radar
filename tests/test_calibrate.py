from datetime import datetime, timezone

from data.config import Config
from data.models import Decision, Direction, GroundTruth, Platform, SampleOutput, Tier
from data.store import Store
from agents import calibrate as cal


def _decision(mid, tier, theme, dom, p_yes, conf, abstain=False):
    return Decision(
        decision_id=f"d-{mid}", market_id=mid, tier=tier, theme=theme,
        dominant_signal=dom, router_score=1, snapshot_hash=f"h{mid}",
        samples=[SampleOutput(role="bull", index=i, p_yes=p_yes) for i in range(3)]
                + [SampleOutput(role="bear", index=i, p_yes=1 - p_yes) for i in range(3)],
        aggregate=p_yes, debate=None,
        direction=Direction.YES if p_yes >= 0.5 else Direction.NO,
        p_yes=p_yes, confidence=conf, abstain=abstain, thesis="t",
        key_risks=[], model="m", asof_ts=datetime.now(timezone.utc),
    )


def test_brier_and_log_loss_bounds():
    assert cal.brier_score(0.5, 0.0) == 0.25
    assert cal.log_loss(0.5, 0.0) > 0


def test_bias_not_produced_below_min_samples(tmp_path):
    cfg = Config()
    store = Store(str(tmp_path / "t.db"))
    pairs = [(_decision("a", Tier.T1, "x", "jump", 0.6, 80.0), 1.0),
             (_decision("b", Tier.T1, "x", "jump", 0.4, 70.0), 0.0)]
    produced = cal.recalibrate_groups(store, pairs, cfg)
    assert produced == {}  # n=2 < recalibration_min_samples=5
    key = cal.calibration_group_key("x", Tier.T1, "jump")
    assert cal.bias_for(store, key, cfg) is None


def test_bias_produced_at_min_samples(tmp_path):
    cfg = Config()
    store = Store(str(tmp_path / "t.db"))
    pairs = [(_decision(f"m{i}", Tier.T1, "x", "jump", 0.6, 80.0), 1.0) for i in range(5)]
    produced = cal.recalibrate_groups(store, pairs, cfg)
    key = cal.calibration_group_key("x", Tier.T1, "jump")
    assert key in produced
    assert cal.bias_for(store, key, cfg) is not None


def test_recalibration_bias_is_realized_minus_mean_prediction(tmp_path):
    cfg = Config()
    store = Store(str(tmp_path / "t.db"))
    pairs = [(_decision(f"m{i}", Tier.T1, "x", "jump", 0.6, 80.0), 1.0) for i in range(5)]
    produced = cal.recalibrate_groups(store, pairs, cfg)
    key = cal.calibration_group_key("x", Tier.T1, "jump")
    # realized frequency 1.0 − mean prediction 0.6 = +0.4, clamped to +0.25
    assert produced[key] == 0.25


def test_selective_threshold_scan_empty():
    cfg = Config()
    assert cal.scan_selective_threshold([], cfg) == (None, None, None)


def test_decisions_with_outcomes_joins_only_resolved():
    gt = {"a": GroundTruth(market_id="a", platform=Platform.POLYMARKET,
                           outcome=1.0, resolved_at=datetime.now(timezone.utc),
                           source="s")}
    decs = [_decision("a", Tier.T1, "x", "jump", 0.6, 80.0),
            _decision("b", Tier.T1, "x", "jump", 0.4, 70.0)]
    pairs = cal.decisions_with_outcomes(decs, gt)
    assert len(pairs) == 1
    assert pairs[0][0].market_id == "a"


def test_reliability_buckets_bins_by_confidence():
    pairs = [(_decision("a", Tier.T1, "x", "jump", 0.6, 55.0), 1.0),
             (_decision("b", Tier.T1, "x", "jump", 0.6, 75.0), 0.0),
             (_decision("c", Tier.T1, "x", "jump", 0.6, 95.0), 1.0)]
    buckets = cal.reliability_buckets(pairs)
    assert [b["bucket"] for b in buckets] == ["40-60", "60-80", "80-100"]
    assert buckets[0]["n"] == 1
    assert buckets[0]["actual_frequency"] == 1.0
    assert buckets[1]["actual_frequency"] == 0.0


def test_lesson_dashboard_requires_30_samples():
    pairs = [(_decision(f"m{i}", Tier.T1, "x", "jump", 0.6, 80.0), 1.0) for i in range(29)]
    assert cal.lesson_dashboard(pairs) is None


def test_lesson_dashboard_net_effect_and_hit_rate():
    pairs = []
    for i in range(30):
        d = _decision(f"m{i}", Tier.T1, "x", "jump", 0.6, 80.0)
        if i < 10:
            d.lessons_injected = [1]
        pairs.append((d, 1.0))
    dash = cal.lesson_dashboard(pairs)
    assert dash is not None
    assert dash["injected_n"] == 10
    assert dash["control_n"] == 20
    assert dash["hit_rate"] == 1.0
    assert dash["net_effect"] == 0.0  # identical p_yes -> equal Brier in both groups

