"""CGAD component 2b: conditional debate (V3.1 §4.3).

Debate is demoted to a disagreement resolver: at most one round, only attacks
the opponent's core premise, triggered only when the quantified Bull/Bear
disagreement exceeds the (uncalibrated) threshold.
"""
from __future__ import annotations

from typing import Any

from data.config import Config
from data.models import DebateRecord
from agents.aggregate import AggResult
from agents.prompts import SYSTEM_BEAR, SYSTEM_BULL

PREMISE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"premise": {"type": "string"}},
    "required": ["premise"],
    "additionalProperties": False,
}


def should_debate(agg: AggResult, cfg: Config) -> bool:
    return agg.disagreement_pp > cfg.debate_trigger_pp


def run_debate(facts: str, agg: AggResult, llm: Any) -> DebateRecord:
    """One round of premise-attack debate; the caller re-aggregates once after.

    The Bull states its single core premise, then the Bear attacks *only that
    premise*; the Bear's attack is the decisive point handed to the Judge.
    """
    bull = llm.complete(SYSTEM_BULL, f"{facts}\nState your single core premise.", PREMISE_SCHEMA)
    premise = str(bull.get("premise", "")).strip()
    bear = llm.complete(
        SYSTEM_BEAR,
        f"{facts}\nThe Bull's core premise is: {premise}\n"
        f"Attack ONLY this premise. State the single decisive point that decides "
        f"whether the Bull's premise survives.",
        PREMISE_SCHEMA,
    )
    decisive = str(bear.get("premise", "")).strip()
    return DebateRecord(
        triggered=True,
        bull_core=premise,
        bear_core=decisive,
        decisive_point=decisive or "no decisive point produced",
    )
