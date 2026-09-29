import pytest

from data.models import SampleOutput
from agents.aggregate import (aggregate, semantic_similarity, sod_weights,
                              trimmed_mean)


def test_trimmed_mean_drops_outliers():
    assert trimmed_mean([0.0, 0.5, 0.6, 1.0]) == pytest.approx(0.55)


def test_trimmed_mean_two_values_is_plain_mean():
    assert trimmed_mean([0.1, 0.9]) == pytest.approx(0.5)


def test_aggregate_disagreement():
    samples = [SampleOutput(role="bull", index=i, p_yes=0.7) for i in range(3)]
    samples += [SampleOutput(role="bear", index=i, p_yes=0.3) for i in range(3)]
    agg = aggregate(samples)
    assert agg.disagreement_pp == pytest.approx(40.0)
    assert agg.median_bull == pytest.approx(0.7)
    assert agg.median_bear == pytest.approx(0.3)


def test_aggregate_requires_both_roles():
    with pytest.raises(ValueError):
        aggregate([SampleOutput(role="bull", index=0, p_yes=0.5)])


def test_semantic_similarity_identical_and_disjoint():
    assert semantic_similarity("rates cut because inflation fell",
                               "rates cut because inflation fell") == pytest.approx(1.0)
    assert semantic_similarity("fed will cut rates", "asteroid mining boom") < 0.3


def test_sod_weights_downweights_semantic_outlier():
    reasonings = [
        "rates will be cut because inflation fell",
        "rates will be cut because inflation fell sharply",
        "a completely unrelated asteroid event happened",
    ]
    w = sod_weights(reasonings)
    assert w[2] < w[0] and w[2] < w[1]


def test_aggregate_uses_sod_when_reasoning_present_else_trimmed():
    sod_samples = [
        SampleOutput(role="bull", index=0, p_yes=0.55, reasoning="cut: inflation fell"),
        SampleOutput(role="bull", index=1, p_yes=0.56, reasoning="cut: inflation fell"),
        SampleOutput(role="bull", index=2, p_yes=0.54, reasoning="asteroid mining boom"),
        SampleOutput(role="bear", index=0, p_yes=0.45, reasoning="no cut: jobs strong"),
        SampleOutput(role="bear", index=1, p_yes=0.46, reasoning="no cut: jobs strong"),
        SampleOutput(role="bear", index=2, p_yes=0.44, reasoning="no cut: jobs strong"),
    ]
    sod = aggregate(sod_samples, method="sod")
    trimmed = aggregate(sod_samples, method="trimmed")
    assert sod.method == "sod"
    assert trimmed.method == "trimmed"
    # The semantic outlier (0.54) is down-weighted out of the pool, so SOD and
    # trimmed means differ even though all numeric values are close.
    assert sod.p_yes != trimmed.p_yes


def test_aggregate_falls_back_to_trimmed_without_reasoning():
    samples = [SampleOutput(role="bull", index=i, p_yes=0.7) for i in range(3)]
    samples += [SampleOutput(role="bear", index=i, p_yes=0.3) for i in range(3)]
    agg = aggregate(samples, method="sod")
    assert agg.method == "trimmed"
    assert agg.p_yes == pytest.approx(0.5)

