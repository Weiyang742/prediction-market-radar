"""CGAD component 3: environment-feedback reflection (V3.1 §4.4).

Runs only after resolution: the Reflector reads the agent's own decision plus
the true outcome and produces a reusable lesson.  No self-critique without
environment feedback (Huang et al. 2024 finding).
"""
from __future__ import annotations

from typing import Any

from data.config import Config
from data.models import Decision, Lesson, utcnow
from data.store import Store
from agents import prompts
from agents.prompts import REFLECT_SCHEMA


def reflect(decision: Decision, outcome: float, snapshot: dict[str, Any],
            store: Store, llm: Any, cfg: Config) -> Lesson:
    facts = prompts.analyst_facts(snapshot)
    result = llm.complete(*prompts.reflect_prompt(facts, decision.direction,
                                                 decision.p_yes, outcome),
                          schema=REFLECT_SCHEMA)
    lesson = Lesson(theme=decision.theme, lesson=str(result["lesson"]),
                    source_snapshot_hash=decision.snapshot_hash,
                    created_at=utcnow())
    lesson.id = store.insert_lesson(lesson)
    return lesson
