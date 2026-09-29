"""CGAD orchestration: sample -> aggregate -> (debate) -> judge -> recalibrate
-> selective gate -> persist (V3.1 §4.6).
"""
from __future__ import annotations

import uuid
from typing import Any

from data.config import Config
from data.models import (Decision, Direction, Market, SampleOutput, Tier, utcnow)
from data.store import Store
from agents import prompts
from agents.aggregate import aggregate
from agents.calibrate import calibration_group_key, bias_for, decision_abstain_score
from agents.debate import run_debate, should_debate
from agents.prompts import JUDGE_SCHEMA, SAMPLE_SCHEMA


def _prob(value: Any) -> float:
    """Normalise a model-emitted probability (handles strings, 0-100 scale)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.5
    if f > 1.5:
        f /= 100.0
    return max(0.0, min(1.0, f))


def _samples(llm: Any, facts: str, base: float, k: int) -> list[SampleOutput]:
    out: list[SampleOutput] = []
    for role, prompt_fn in (("bull", prompts.bull_prompt), ("bear", prompts.bear_prompt)):
        for i in range(k):
            r = llm.complete(*prompt_fn(facts, base), schema=SAMPLE_SCHEMA)
            out.append(SampleOutput(role=role, index=i, p_yes=_prob(r.get("p_yes")),
                                    reasoning=str(r.get("reasoning") or "")))
    return out


def rule_fallback(market: Market, tier: Tier, theme: str | None,
                  dominant_signal: str | None, router_score: int,
                  snapshot_hash: str, model: str = "rule_fallback",
                  thesis: str | None = None) -> Decision:
    """Deterministic fallback used when the LLM path is unavailable or has
    failed too many times (V3.1 §7 degradation), and for Tier 0 rule labels."""
    p = market.yes_price
    confidence = min(95.0, 50.0 + abs(p - 0.5) * 100.0)
    return Decision(
        decision_id=uuid.uuid4().hex, market_id=market.market_id, tier=tier,
        theme=theme, dominant_signal=dominant_signal, router_score=router_score,
        snapshot_hash=snapshot_hash, samples=[], aggregate=p,
        debate=None, direction=Direction.YES if p >= 0.5 else Direction.NO,
        p_yes=p, confidence=round(confidence, 1), confidence_recalibrated=None,
        recalib_coef=None, abstain=False, abstain_reason=None,
        thesis=thesis or "rule_fallback (LLM unavailable)", key_risks=[], invalidated_if=None,
        model=model, asof_ts=utcnow(),
    )


def run_judge(snapshot: dict[str, Any], tier: Tier, router_score: int,
              theme: str | None, dominant_signal: str | None,
              cfg: Config, llm: Any, store: Store | None = None,
              second_llm: Any | None = None) -> Decision:
    market_data = snapshot["market"]
    facts = prompts.analyst_facts(snapshot)
    base = float(market_data["yes_price"])
    k = cfg.bull_bear_samples

    lessons_injected: list[int] = []
    if store is not None and theme:
        lessons = store.recent_lessons(theme, cfg.lessons_cap)
        if lessons:
            block = "\n".join(f"Prior lesson ({l.source_snapshot_hash or 'n/a'}): {l.lesson}"
                              for l in lessons)
            facts = facts + "\n" + block
            lessons_injected = [l.id for l in lessons if l.id is not None]

    samples = _samples(llm, facts, base, k)
    agg = aggregate(samples, method=cfg.aggregation_method)

    debate = None
    if should_debate(agg, cfg):
        debate = run_debate(facts, agg, llm)
        samples = _samples(llm, facts, base, k)  # re-aggregate once, then stop
        agg = aggregate(samples, method=cfg.aggregation_method)

    judge = llm.complete(*prompts.judge_prompt(facts, agg.p_yes, debate), schema=JUDGE_SCHEMA)

    group_key = calibration_group_key(theme, tier, dominant_signal)
    bias = bias_for(store, group_key, cfg) if store is not None else None
    p_yes = _prob(judge.get("p_yes"))
    direction_raw = str(judge.get("direction", "YES")).upper()
    if direction_raw not in ("YES", "NO"):
        direction_raw = "YES" if p_yes >= 0.5 else "NO"
    conf_raw: float
    try:
        conf_raw = float(judge.get("confidence") or 50.0)
    except (TypeError, ValueError):
        conf_raw = 50.0
    if conf_raw <= 1.5:
        conf_raw *= 100.0
    confidence = max(0.0, min(100.0, conf_raw))
    # Recalibration: shift P(YES) toward the group's realized frequency; the
    # direction follows the recalibrated probability (bias may cross 0.5).
    if bias is not None:
        p_yes = max(0.0, min(1.0, p_yes + bias))
        direction_raw = "YES" if p_yes >= 0.5 else "NO"

    # Dual-model external anchor: run the same judge input through a second
    # model; a large P(YES) divergence is a strong abstain signal (DeepFund
    # trading-personality finding, arXiv:2505.11065).
    dual_model: str | None = None
    dual_p_yes: float | None = None
    dual_divergence: float | None = None
    if second_llm is not None:
        dual_model = getattr(second_llm, "model_name", cfg.second_model)
        second = second_llm.complete(*prompts.judge_prompt(facts, agg.p_yes, debate),
                                     schema=JUDGE_SCHEMA)
        dual_p_yes = _prob(second.get("p_yes"))
        dual_divergence = abs(p_yes - dual_p_yes) * 100.0

    # Selective gate: abstain when the (recomputed) abstain score exceeds the
    # threshold.  The threshold is the learned value when available, else the
    # uncalibrated default (V3.1 §4.5).
    abstain_threshold = cfg.abstain_default_pp
    if store is not None:
        learned = store.get_meta("selective_threshold")
        if learned is not None:
            try:
                abstain_threshold = float(learned)
            except ValueError:
                pass
    gate_abstain = agg.disagreement_pp + router_score * cfg.abstain_signal_weight >= abstain_threshold
    dual_abstain = dual_divergence is not None and dual_divergence > cfg.dual_model_abstain_pp
    research_block = snapshot.get("research")
    research_abstain = bool(research_block and research_block.get("abstain"))
    final_abstain = (bool(judge.get("abstain")) or gate_abstain or dual_abstain
                     or research_abstain)
    abstain_reason = judge.get("abstain_reason")
    if gate_abstain and not judge.get("abstain"):
        abstain_reason = (abstain_reason or "") + f"; gate: disagreement {agg.disagreement_pp:.1f}pp"
        abstain_reason = abstain_reason.strip("; ")
    if dual_abstain:
        abstain_reason = (abstain_reason or "") + f"; dual-model: divergence {dual_divergence:.1f}pp"
        abstain_reason = abstain_reason.strip("; ")
    if research_abstain:
        reason = research_block.get("abstain_reason") or "research abstained"
        abstain_reason = (abstain_reason or "") + f"; research: {reason}"
        abstain_reason = abstain_reason.strip("; ")

    decision = Decision(
        decision_id=uuid.uuid4().hex, market_id=market_data["market_id"], tier=tier,
        theme=theme, dominant_signal=dominant_signal, router_score=router_score,
        snapshot_hash=snapshot["snapshot_hash"], samples=samples,
        aggregate=agg.p_yes, debate=debate,
        direction=Direction(direction_raw), p_yes=p_yes,
        confidence=confidence, confidence_recalibrated=None,
        recalib_coef=bias, abstain=final_abstain, abstain_reason=abstain_reason,
        thesis=str(judge.get("thesis", "")), key_risks=list(judge.get("key_risks", [])),
        invalidated_if=judge.get("invalidated_if"), model=getattr(llm, "model_name", cfg.model),
        lessons_injected=lessons_injected, dual_model=dual_model, dual_p_yes=dual_p_yes,
        asof_ts=utcnow(),
    )

    if store is not None:
        store.insert_decision(decision)
        store.log_ops("cost_usd", str(cfg.tier2_cost_usd if tier is Tier.T2 else cfg.tier1_cost_usd))
    return decision
