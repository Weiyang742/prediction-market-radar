"""Debate is a single premise-attack round, not a fresh independent premise."""
from data.config import Config
from data.models import DebateRecord
from agents.aggregate import AggResult
from agents.debate import run_debate, should_debate


class FakeDebateLLM:
    def __init__(self):
        self.calls = []

    def complete(self, system, user, schema):
        self.calls.append(user)
        if "State your single core premise" in user:
            return {"premise": "bull premise"}
        return {"premise": "bear attack on the bull premise"}


def _agg(disagreement_pp):
    return AggResult(p_yes=0.5, median_bull=0.6, median_bear=0.4,
                     disagreement_pp=disagreement_pp, n=6)


def test_should_debate_threshold():
    cfg = Config(debate_trigger_pp=10.0)
    assert should_debate(_agg(20.0), cfg) is True
    assert should_debate(_agg(5.0), cfg) is False


def test_run_debate_attacks_the_bull_premise():
    llm = FakeDebateLLM()
    rec = run_debate("facts", _agg(20.0), llm)
    assert isinstance(rec, DebateRecord)
    assert rec.bull_core == "bull premise"
    assert rec.bear_core == "bear attack on the bull premise"
    assert rec.decisive_point == "bear attack on the bull premise"
    # The second LLM call must target the bull's premise, not re-state its own.
    assert "bull premise" in llm.calls[1]
