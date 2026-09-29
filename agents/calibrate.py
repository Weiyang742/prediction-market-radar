"""CGAD components 3-4: confidence recalibration + selective abstain threshold.

Scoring uses the resolved-ground-truth join of decisions.  Recalibration is a
training-free, inference-time implementation inspired by (but NOT equivalent
to) RefGRPO's calibration bonus — V3.1 §4.4.
"""
from __future__ import annotations

import math
from typing import Sequence

from data.config import Config
from data.models import Decision, GroundTruth, Tier
from data.store import Store
from agents.aggregate import aggregate

_EPS = 1e-6


def brier_score(p: float, o: float) -> float:
    return (p - o) ** 2


def log_loss(p: float, o: float) -> float:
    p = max(_EPS, min(1.0 - _EPS, p))
    return -(o * math.log(p) + (1 - o) * math.log(1 - p))


def calibration_group_key(theme: str | None, tier: Tier, dominant_signal: str | None) -> str:
    return f"{theme or 'other'}|{tier.value}|{dominant_signal or 'none'}"


def decisions_with_outcomes(decisions: Sequence[Decision],
                            gt: dict[str, GroundTruth]) -> list[tuple[Decision, float]]:
    out: list[tuple[Decision, float]] = []
    for d in decisions:
        if d.market_id in gt:
            out.append((d, gt[d.market_id].outcome))
    return out


def decision_abstain_score(d: Decision, cfg: Config) -> float:
    try:
        agg = aggregate(d.samples)
        disagreement = agg.disagreement_pp
    except ValueError:
        disagreement = 0.0
    return disagreement + d.router_score * cfg.abstain_signal_weight


def compute_group_stats(pairs: Sequence[tuple[Decision, float]]) -> dict[str, dict]:
    groups: dict[str, list[tuple[float, float, float]]] = {}
    for d, o in pairs:
        key = calibration_group_key(d.theme, d.tier, d.dominant_signal)
        groups.setdefault(key, []).append((d.confidence, o, d.p_yes))
    out: dict[str, dict] = {}
    for key, rows in groups.items():
        n = len(rows)
        mean_conf = sum(r[0] for r in rows) / n
        accuracy = sum(1.0 for r in rows if (r[2] >= 0.5) == (r[1] >= 0.5)) / n
        mean_p_yes = sum(r[2] for r in rows) / n
        mean_outcome = sum(r[1] for r in rows) / n
        out[key] = {"n": n, "mean_confidence": mean_conf, "accuracy": accuracy,
                    "mean_p_yes": mean_p_yes, "mean_outcome": mean_outcome}
    return out


def _clamp_bias(bias: float) -> float:
    return max(-0.25, min(0.25, bias))


def recalibrate_groups(store: Store, pairs: Sequence[tuple[Decision, float]], cfg: Config) -> dict[str, float]:
    """Compute and persist a per-group P(YES) recalibration bias.

    bias = realized frequency − mean predicted probability, applied to future
    decisions in that group.  Produced only when n >= cfg.recalibration_min_samples
    (delivery contract: no bias from <5 samples)."""
    stats = compute_group_stats(pairs)
    produced: dict[str, float] = {}
    for key, s in stats.items():
        if s["n"] < cfg.recalibration_min_samples:
            continue
        bias = _clamp_bias(s["mean_outcome"] - s["mean_p_yes"])
        store.upsert_calibration(key, s["n"], s["mean_confidence"], s["accuracy"], bias)
        produced[key] = bias
    return produced


def bias_for(store: Store, key: str, cfg: Config) -> float | None:
    row = store.get_calibration(key)
    if row is None or row["n"] < cfg.recalibration_min_samples:
        return None
    return float(row["coef"])


def selective_brier(pairs: Sequence[tuple[Decision, float]], threshold: float,
                    cfg: Config) -> tuple[float, float, float] | None:
    """Return (selective_brier, coverage, abstain_rate) at a threshold, or None
    when every decision would be abstained."""
    kept: list[tuple[Decision, float]] = []
    for d, o in pairs:
        if decision_abstain_score(d, cfg) < threshold:
            kept.append((d, o))
    total = len(pairs)
    if total == 0 or not kept:
        return None
    coverage = len(kept) / total
    mean_brier = sum(brier_score(d.p_yes, o) for d, o in kept) / len(kept)
    return mean_brier / coverage, coverage, (total - len(kept)) / total


def scan_selective_threshold(pairs: Sequence[tuple[Decision, float]],
                             cfg: Config) -> tuple[float | None, float | None, float | None]:
    """Scan thresholds over the observed abstain-score range and pick the one
    minimising selective Brier (Chow error-reject tradeoff, V3.1 §4.5)."""
    if not pairs:
        return None, None, None
    scores = [decision_abstain_score(d, cfg) for d, _ in pairs]
    candidates = sorted(set(round(s, 1) for s in scores))
    best: tuple[float, float, float, float] | None = None
    for t in candidates:
        res = selective_brier(pairs, t, cfg)
        if res is None:
            continue
        sb, coverage, abstain = res
        if best is None or sb < best[0]:
            best = (sb, t, coverage, abstain)
    if best is None:
        return None, None, None
    return best[1], best[0], best[3]


def reliability_buckets(pairs: Sequence[tuple[Decision, float]],
                        n_buckets: int = 5) -> list[dict]:
    """Bucket resolved predictions by self-reported confidence and report the
    predicted-vs-actual reliability (confidence calibration).

    Reliability diagram (V3.1 §4.4 / DeepFund evaluation protocol): each bucket
    holds equal-width confidence intervals; ``pred`` is the mean self-reported
    confidence (0-100) and ``actual`` is the mean outcome frequency (0-1).  A
    perfectly calibrated agent sits on the diagonal ``pred/100 == actual``.
    """
    if n_buckets <= 0:
        raise ValueError("n_buckets must be positive")
    buckets: list[list[tuple[Decision, float]]] = [[] for _ in range(n_buckets)]
    width = 100.0 / n_buckets
    for d, o in pairs:
        idx = min(n_buckets - 1, int(d.confidence / width))
        if d.confidence >= 100.0:
            idx = n_buckets - 1
        buckets[idx].append((d, o))
    out: list[dict] = []
    for i, rows in enumerate(buckets):
        if not rows:
            continue
        n = len(rows)
        mean_conf = sum(d.confidence for d, _ in rows) / n
        actual = sum(o for _, o in rows) / n
        out.append({
            "bucket": f"{i * width:.0f}-{min(100.0, (i + 1) * width):.0f}",
            "n": n,
            "pred_confidence": round(mean_conf, 1),
            "actual_frequency": round(actual, 3),
            "deviation": round(mean_conf / 100.0 - actual, 3),
        })
    return out


def lesson_dashboard(pairs: Sequence[tuple[Decision, float]],
                     min_samples: int = 30) -> dict | None:
    """Memory-injection dashboard (Pattern B → C decision data, per the Agent
    Memory survey arXiv:2603.07670).

    Compares resolved decisions that had at least one lesson injected against
    those that had none: ``hit_rate`` (direction accuracy of the injected
    group) and ``net_effect`` (injected Brier − control Brier; negative means
    injection improved calibration).  Returns ``None`` when the sample is below
    ``min_samples`` — the caller must print "数据不足", never a shaky number.
    """
    if len(pairs) < min_samples:
        return None
    injected = [(d, o) for d, o in pairs if d.lessons_injected]
    control = [(d, o) for d, o in pairs if not d.lessons_injected]
    if not injected or not control:
        return {"total": len(pairs), "injected_n": len(injected),
                "control_n": len(control), "hit_rate": None, "net_effect": None,
                "note": "one comparison group is empty"}
    hit_rate = sum(1.0 for d, o in injected if (d.p_yes >= 0.5) == (o >= 0.5)) / len(injected)
    injected_brier = sum(brier_score(d.p_yes, o) for d, o in injected) / len(injected)
    control_brier = sum(brier_score(d.p_yes, o) for d, o in control) / len(control)
    return {
        "total": len(pairs),
        "injected_n": len(injected),
        "control_n": len(control),
        "hit_rate": round(hit_rate, 3),
        "injected_brier": round(injected_brier, 4),
        "control_brier": round(control_brier, 4),
        "net_effect": round(injected_brier - control_brier, 4),
    }
