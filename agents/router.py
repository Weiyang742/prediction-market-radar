"""CGAD component 1: deterministic depth router (V3.1 §4.2).

A rule-based additive scorer (no LLM, <1ms) that maps structured signal
features to Tier 0/1/2.  Replaces the LLM complexity classifier used by
MDAgents / Agent Routing.  The tier is persisted on every decision as the
independent variable for R13 layered ablation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from data.config import Config
from data.models import Severity, Signal, Tier

_SEVERITY_WEIGHT = {Severity.LOW: 0, Severity.MED: 1, Severity.HIGH: 2}


@dataclass
class RouteResult:
    tier: Tier
    score: int
    reasons: list[str] = field(default_factory=list)


def route(signals: list[Signal], cfg: Config, divergence_pp: float | None = None,
          group_sum_deviation_pp: float | None = None) -> RouteResult:
    score = 0
    reasons: list[str] = []

    has_high = False
    has_med = False
    for sig in signals:
        weight = _SEVERITY_WEIGHT[sig.severity]
        score += weight
        if sig.severity is Severity.HIGH:
            has_high = True
        elif sig.severity is Severity.MED:
            has_med = True
        reasons.append(f"{sig.type.value}={sig.severity.value}")

    div_weight = 0
    if divergence_pp is not None:
        if divergence_pp >= cfg.divergence_high_pp:
            div_weight = 2
            has_high = True
            reasons.append(f"divergence={divergence_pp:.1f}pp (>=HIGH)")
        elif divergence_pp >= cfg.divergence_pp:
            div_weight = 1
            has_med = True
            reasons.append(f"divergence={divergence_pp:.1f}pp (MED)")
    score += div_weight

    if group_sum_deviation_pp is not None and group_sum_deviation_pp > cfg.group_sum_deviation_pp:
        score += 2
        has_high = True
        reasons.append(f"group_sum_deviation={group_sum_deviation_pp:.1f}pp")

    # Hard fallback: any HIGH severity forces Tier 2 (engineering conservatism).
    if has_high:
        return RouteResult(Tier.T2, score, reasons)
    if has_med or score > 0:
        return RouteResult(Tier.T1, score, reasons)
    return RouteResult(Tier.T0, score, reasons)
