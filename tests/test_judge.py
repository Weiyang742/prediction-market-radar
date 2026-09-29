"""CGAD judge normalisation tests — regression coverage for the real-LLM
integration findings (DeepSeek emits lowercase direction, 0-1 confidence, and
percent-scale probabilities)."""
from datetime import datetime, timezone

import pytest

from data.config import Config
from data.models import Direction, Lesson, Tier, snapshot_hash, utcnow
from data.store import Store
from agents.calibrate import calibration_group_key
from agents.judge import _prob, run_judge


class FakeLLM:
    def complete(self, system, user, schema):
        props = schema.get("properties", {})
        if "direction" in props:
            return {"direction": "no", "p_yes": 0.3, "confidence": 0.6,
                    "abstain": False, "abstain_reason": None, "thesis": "t",
                    "key_risks": [], "invalidated_if": None}
        return {"p_yes": 42}  # percent scale, like a model might emit


class FakeLLMHigh:
    def complete(self, system, user, schema):
        props = schema.get("properties", {})
        if "direction" in props:
            return {"direction": "yes", "p_yes": 0.9, "confidence": 0.8,
                    "abstain": False, "abstain_reason": None, "thesis": "t",
                    "key_risks": [], "invalidated_if": None}
        return {"p_yes": 90, "reasoning": "r"}


def _snap() -> dict:
    snap = {
        "market": {"market_id": "m", "question": "q", "yes_price": 0.5,
                   "volume_24h": 1.0, "spread": 0.01,
                   "close_time": None, "asof_ts": datetime.now(timezone.utc).isoformat()},
        "signals": [], "cross_platform": None,
        "related_group": {"sibling_markets": [], "group_sum_deviation": None},
    }
    snap["snapshot_hash"] = snapshot_hash(snap)
    return snap


def test_prob_normalises_strings_and_percent():
    assert _prob("0.42") == 0.42
    assert _prob(42) == 0.42
    assert _prob(0.42) == 0.42
    assert _prob("garbage") == 0.5


def test_run_judge_normalises_direction_and_confidence():
    cfg = Config()
    d = run_judge(_snap(), tier=Tier.T1, router_score=0, theme="x",
                  dominant_signal=None, cfg=cfg, llm=FakeLLM(), store=None)
    assert d.direction is Direction.NO  # lowercase "no" normalised
    assert d.confidence == 60.0          # 0.6 -> 60
    assert d.p_yes == 0.3


def test_run_judge_dual_model_divergence_abstains():
    cfg = Config()
    d = run_judge(_snap(), tier=Tier.T1, router_score=0, theme="x",
                  dominant_signal=None, cfg=cfg, llm=FakeLLM(), store=None,
                  second_llm=FakeLLMHigh())
    assert d.abstain is True
    assert "dual-model" in (d.abstain_reason or "")
    assert d.dual_p_yes == 0.9


def test_run_judge_records_injected_lessons(tmp_path):
    cfg = Config()
    store = Store(str(tmp_path / "t.db"))
    lesson = Lesson(theme="x", lesson="prior lesson text", source_snapshot_hash="h",
                    created_at=utcnow())
    lesson.id = store.insert_lesson(lesson)
    d = run_judge(_snap(), tier=Tier.T1, router_score=0, theme="x",
                  dominant_signal=None, cfg=cfg, llm=FakeLLM(), store=store)
    assert d.lessons_injected == [lesson.id]


def test_run_judge_applies_recalibration_bias(tmp_path):
    cfg = Config()
    store = Store(str(tmp_path / "t.db"))
    key = calibration_group_key("x", Tier.T1, None)
    store.upsert_calibration(key, n=5, mean_confidence=70.0, accuracy=0.7, bias=-0.20)
    d = run_judge(_snap(), tier=Tier.T1, router_score=0, theme="x",
                  dominant_signal=None, cfg=cfg, llm=FakeLLM(), store=store)
    # FakeLLM judge emits p_yes=0.3; bias −0.20 → 0.10, direction flips to NO.
    assert d.p_yes == pytest.approx(0.10)
    assert d.recalib_coef == pytest.approx(-0.20)
    assert d.direction is Direction.NO

