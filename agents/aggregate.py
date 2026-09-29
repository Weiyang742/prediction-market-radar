"""CGAD component 2: sampling aggregation over debate (V3.1 §4.3 + SOD).

Bull/Bear each produce K independent samples of P(YES).  The default
aggregation is now **semantic-outlier-aware (SOD)** — samples whose argument
text semantically contradicts their own role's majority cluster are
down-weighted out of the aggregation pool even when their numeric P(YES) is
close (ARMOR-MAD SOD, arXiv:2606.13197).  When the samples carry no argument
text, it falls back to the trimmed mean (drop min/max, average the rest).

Quantitative disagreement triggers at most one round of debate.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Callable, Sequence

from data.models import SampleOutput

Similarity = Callable[[str, str], float]


@dataclass
class AggResult:
    p_yes: float
    median_bull: float
    median_bear: float
    disagreement_pp: float
    n: int
    method: str = "sod"


def trimmed_mean(values: list[float]) -> float:
    if not values:
        return 0.5
    if len(values) <= 2:
        return statistics.fmean(values)
    ordered = sorted(values)
    return statistics.fmean(ordered[1:-1])


def char_ngrams(text: str, n: int = 3) -> set[str]:
    text = (text or "").strip().lower()
    if not text:
        return set()
    if len(text) <= n:
        return {text}
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def semantic_similarity(a: str, b: str) -> float:
    """Deterministic lexical similarity (char 3-gram cosine).

    A stand-in for a real embedding backend — swap in an embedding call here
    when one is wired.  Deterministic and offline so ``--mock`` and tests stay
    reproducible (ARMOR-MAD SOD).
    """
    ga, gb = char_ngrams(a), char_ngrams(b)
    if not ga or not gb:
        return 0.5
    inter = len(ga & gb)
    denom = math.sqrt(len(ga) * len(gb))
    if denom == 0:
        return 0.5
    return inter / denom


def _pairwise_similarity(reasonings: list[str], sim: Similarity) -> list[list[float]]:
    n = len(reasonings)
    matrix = [[1.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            s = sim(reasonings[i], reasonings[j])
            matrix[i][j] = s
            matrix[j][i] = s
    return matrix


def sod_weights(reasonings: list[str],
                sim: Similarity = semantic_similarity) -> list[float]:
    """Weight each sample by semantic centrality within its role.

    Centrality = mean pairwise similarity to the role's other samples; the
    weights are normalised to [0, 1] relative to the role's most-central
    sample, so a semantic outlier (contradicting the majority cluster) is
    reduced toward zero — exactly the ARMOR-MAD SOD intuition.
    """
    n = len(reasonings)
    if n <= 2:
        return [1.0] * n
    matrix = _pairwise_similarity(reasonings, sim)
    centrality = [statistics.fmean(matrix[i][j] for j in range(n) if j != i)
                  for i in range(n)]
    lo, hi = min(centrality), max(centrality)
    span = hi - lo
    if span <= 0:
        return [1.0] * n
    return [round((c - lo) / span, 4) for c in centrality]


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    total = sum(weights)
    if total <= 0:
        return statistics.fmean(values) if values else 0.5
    return sum(v * w for v, w in zip(values, weights)) / total


def aggregate(samples: Sequence[SampleOutput], method: str = "sod",
              sim: Similarity = semantic_similarity) -> AggResult:
    bull = [s for s in samples if s.role == "bull"]
    bear = [s for s in samples if s.role == "bear"]
    if not bull or not bear:
        raise ValueError("aggregation requires at least one bull and one bear sample")
    median_bull = statistics.median([s.p_yes for s in bull])
    median_bear = statistics.median([s.p_yes for s in bear])
    disagreement_pp = abs(median_bull - median_bear) * 100.0

    can_sod = method == "sod" and all((s.reasoning or "").strip() for s in samples)
    if can_sod:
        bull_ws = sod_weights([s.reasoning or "" for s in bull], sim)
        bear_ws = sod_weights([s.reasoning or "" for s in bear], sim)
        bi = bri = 0
        weights: list[float] = []
        for s in samples:
            if s.role == "bull":
                weights.append(bull_ws[bi]); bi += 1
            else:
                weights.append(bear_ws[bri]); bri += 1
        p_yes = _weighted_mean([s.p_yes for s in samples], weights)
        used = "sod"
    else:
        p_yes = trimmed_mean([s.p_yes for s in samples])
        used = "trimmed"

    return AggResult(
        p_yes=round(p_yes, 4),
        median_bull=median_bull,
        median_bear=median_bear,
        disagreement_pp=round(disagreement_pp, 2),
        n=len(samples),
        method=used,
    )
