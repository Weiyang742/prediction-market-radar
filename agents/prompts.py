"""Prompts and JSON schemas for the CGAD agent layer (V3.1 §6.3, appendix A)."""
from __future__ import annotations

from typing import Any

from data.models import DebateRecord, Direction

P_YES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"p_yes": {"type": "number", "minimum": 0, "maximum": 1}},
    "required": ["p_yes"],
    "additionalProperties": False,
}

SAMPLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "p_yes": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["reasoning", "p_yes"],
    "additionalProperties": False,
}

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "direction": {"type": "string", "enum": ["YES", "NO"]},
        "p_yes": {"type": "number", "minimum": 0, "maximum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 100},
        "abstain": {"type": "boolean"},
        "abstain_reason": {"type": ["string", "null"]},
        "thesis": {"type": "string"},
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "invalidated_if": {"type": ["string", "null"]},
    },
    "required": ["direction", "p_yes", "confidence", "abstain", "thesis", "key_risks"],
    "additionalProperties": False,
}

PAIR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"same_event": {"type": "boolean"}},
    "required": ["same_event"],
    "additionalProperties": False,
}

REFLECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"lesson": {"type": "string"}},
    "required": ["lesson"],
    "additionalProperties": False,
}

SYSTEM_BULL = ("You are the Bull case. Argue from the facts given; never invent data. "
               "Output JSON with a one-sentence reasoning and your P(YES) as a number.")

SYSTEM_BEAR = ("You are the Bear case. Argue from the facts given; never invent data. "
               "Output JSON with a one-sentence reasoning and your P(YES) as a number.")

SYSTEM_JUDGE = ("You are the Judge. Summarise bull/bear into a single calibrated decision. "
                "Output the requested JSON. Set abstain=true only if the evidence is genuinely insufficient.")


def analyst_facts(snapshot: dict[str, Any]) -> str:
    """Deterministic fact recap (no LLM) to prevent hallucination."""
    m = snapshot["market"]
    lines = [
        f"market.question = {m['question']}",
        f"market.yes_price = {m['yes_price']}",
        f"market.volume_24h = {m['volume_24h']}",
        f"market.spread = {m['spread']}",
        f"market.close_time = {m['close_time']}",
        f"market.asof_ts = {m['asof_ts']}",
    ]
    for s in snapshot.get("signals", []):
        lines.append(f"signal: {s['type']} severity={s['severity']} detail={s['detail']}")
    cp = snapshot.get("cross_platform")
    if cp:
        lines.append(f"cross_platform: kalshi_same_topic_price={cp.get('kalshi_same_topic_price')} "
                     f"divergence_pp={cp.get('divergence_pp')}")
    research = snapshot.get("research")
    if research:
        if research.get("news_summary"):
            lines.append(f"research.news (what happened): {research['news_summary']}")
        if research.get("history_summary"):
            lines.append(f"research.history (analogies): {research['history_summary']}")
        if research.get("p_yes_updated") is not None:
            lines.append(f"research.cross_validation_p_yes: {research['p_yes_updated']:.4f}")
        lines.append(f"research.verdict: {research.get('verdict')}")
        for ev in research.get("evidence", []):
            pub = ev.get("published_at") or "unknown"
            lines.append(f"research.evidence [freshness={ev.get('freshness')}] "
                         f"source={ev.get('source')} published_at={pub} quote={ev.get('quote')}")
    return "\n".join(lines)


def bull_prompt(facts: str, base: float) -> tuple[str, str]:
    user = (f"{facts}\nBASE_P_YES={base}\n"
            f"Give your one-sentence Bull-side argument (reasoning) and P(YES).")
    return SYSTEM_BULL, user


def bear_prompt(facts: str, base: float) -> tuple[str, str]:
    user = (f"{facts}\nBASE_P_YES={base}\n"
            f"Give your one-sentence Bear-side argument (reasoning) and P(YES).")
    return SYSTEM_BEAR, user


def judge_prompt(facts: str, agg_p_yes: float, debate: DebateRecord | None) -> tuple[str, str]:
    debate_block = ""
    if debate is not None and debate.triggered:
        debate_block = (f"\nDebate summary: bull_core={debate.bull_core} "
                        f"bear_core={debate.bear_core} decisive_point={debate.decisive_point}")
    user = (f"{facts}\nSampled aggregate P(YES)={agg_p_yes:.4f}{debate_block}\n"
            f"Produce the final decision JSON. confidence is an integer 0-100.")
    return SYSTEM_JUDGE, user


def reflect_prompt(facts: str, direction: Direction, p_yes: float, outcome: float) -> tuple[str, str]:
    user = (f"Decision facts:\n{facts}\nOur decision: direction={direction.value} p_yes={p_yes:.4f}\n"
            f"True outcome: {outcome}\n"
            f"Write one concise, reusable lesson about what this episode teaches.")
    return ("You are a Reflector reviewing a resolved market against the agent's own decision. "
            "Only reflect on the environment feedback (the true outcome); do not self-critique without it.",
            user)


def pair_prompt(q1: str, q2: str) -> tuple[str, str]:
    user = (f"Market A: {q1}\nMarket B: {q2}\n"
            f"Are these two markets about the same underlying event? Answer same_event=true/false only.")
    return ("You classify whether two prediction-market questions refer to the same event.", user)


# ---- v2 proactive research agent prompts (recipe loop) ----
DISTILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

CROSS_VALIDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["support", "overturn", "insufficient"]},
        "p_yes": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
    },
    "required": ["verdict", "p_yes"],
    "additionalProperties": False,
}


def distill_news_prompt(question: str, evidence_lines: list[str]) -> tuple[str, str]:
    user = (f"Question: {question}\n\nRetrieved evidence (verbatim quotes only):\n"
            + "\n".join(evidence_lines)
            + "\n\nSummarise *only* what the quotes say happened. Do not add anything "
              "not present in a quote; if you cannot, say 'no information'.")
    return ("You distill retrieved news into a factual 'what happened' summary. "
            "铁律 4: only quoted原文 may be used; never补全 from impressions.", user)


def distill_history_prompt(theme: str, history_lines: list[str]) -> tuple[str, str]:
    user = (f"Theme: {theme}\n\nPast resolved events (local library):\n"
            + "\n".join(history_lines)
            + "\n\nSummarise what these past price paths and outcomes suggest for this theme.")
    return ("You summarise local history-library analogies. Use only the given rows.", user)


def cross_validate_prompt(question: str, original_p_yes: float,
                          news_summary: str | None,
                          history_summary: str | None) -> tuple[str, str]:
    user = (f"Question: {question}\nOriginal market P(YES)={original_p_yes:.4f}\n\n"
            f"News (what happened): {news_summary or 'no retrieved news'}\n"
            f"History analogies: {history_summary or 'no local analogies'}\n\n"
            f"Does the evidence support or overturn the original probability? "
            f"Return verdict support/overturn/insufficient and an updated P(YES).")
    return ("You cross-validate a probability against retrieved evidence. "
            "If the evidence is empty or irrelevant, verdict=insufficient.", user)
