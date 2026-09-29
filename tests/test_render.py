"""Reliability + memory dashboard rendering: 数据不足 gating and table output."""
from report.render import render_reliability_md


def test_reliability_md_reports_data_insufficient_below_30():
    out = render_reliability_md(buckets=None, lesson_dash=None, total=5)
    assert "数据不足" in out
    assert "5" in out


def test_reliability_md_renders_buckets_and_lesson_dashboard():
    buckets = [{"bucket": "60-80", "n": 30, "pred_confidence": 70.0,
                "actual_frequency": 0.6, "deviation": 0.1}]
    lesson = {"total": 30, "injected_n": 10, "control_n": 20,
              "hit_rate": 0.8, "injected_brier": 0.16,
              "control_brier": 0.25, "net_effect": -0.09}
    out = render_reliability_md(buckets, lesson, total=30)
    assert "confidence vs actual frequency" in out
    assert "60-80" in out
    assert "net effect" in out
    assert "-0.0900" in out
