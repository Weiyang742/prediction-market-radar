"""Cross-platform divergence + negation-pair deviation + theme pairing.

Theme normalisation (V3.1 §6.2 five steps): the pairing helper here implements
steps 3-4 (similarity + LLM discriminative confirmation); steps 1-2 (platform
aggregation / theme bucket) are the caller's responsibility via group_key and
``analysis.themes.classify_theme``.
"""
from __future__ import annotations

from typing import Callable, Sequence

from data.config import Config
from data.models import Market, Severity, Signal, SignalType, utcnow

from analysis.themes import similarity

PairConfirmer = Callable[[str, str], bool]


def cross_platform_divergence(p_pm: float, p_ks: float, cfg: Config) -> Signal | None:
    div_pp = abs(p_pm - p_ks) * 100.0
    if div_pp < cfg.divergence_pp:
        return None
    sev = Severity.HIGH if div_pp >= cfg.divergence_high_pp else Severity.MED
    return Signal(type=SignalType.CROSS_PLATFORM_DIVERGENCE, severity=sev,
                  detail=f"|p_PM - p_KS| = {div_pp:.1f}pp",
                  value=round(div_pp, 3), asof_ts=utcnow())


def negation_pair_deviation(p_e: float, p_not_e: float, cfg: Config) -> Signal | None:
    dev_pp = abs(p_e + p_not_e - 1.0) * 100.0
    if dev_pp < cfg.negation_pair_threshold_pp:
        return None
    return Signal(type=SignalType.NEGATION_PAIR_DEVIATION, severity=Severity.HIGH,
                  detail=f"|P(E)+P(¬E)-1| = {dev_pp:.1f}pp",
                  value=round(dev_pp, 3), asof_ts=utcnow())


def group_sum_deviation(prices: Sequence[float]) -> float:
    return abs(sum(prices) - 1.0) * 100.0


def find_pairs(pm_markets: Sequence[Market], ks_markets: Sequence[Market],
               cfg: Config, confirmer: PairConfirmer) -> list[tuple[Market, Market, float]]:
    """Pair markets across platforms by theme bucket + question similarity.

    ``confirmer`` is the LLM discriminative binary classifier ("are these two
    markets the same event?").  In --mock mode it is a deterministic rule; in
    real mode it calls the LLM and its result is cached by the caller.
    """
    pairs: list[tuple[Market, Market, float]] = []
    used_ks: set[str] = set()
    for pm in pm_markets:
        best: tuple[float, Market] | None = None
        for ks in ks_markets:
            if ks.market_id in used_ks or pm.theme != ks.theme:
                continue
            score = similarity(pm.question, ks.question)
            if score <= 0.0:
                continue
            if best is None or score > best[0]:
                best = (score, ks)
        if best is None:
            continue
        score, ks = best
        if confirmer(pm.question, ks.question):
            used_ks.add(ks.market_id)
            pairs.append((pm, ks, score))
    return pairs
