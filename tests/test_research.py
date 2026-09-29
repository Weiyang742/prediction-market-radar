"""Proactive research agent v2 tests: recipe loop + 铁律 4-6 + budget."""
from datetime import datetime, timedelta, timezone

from data.config import Config
from data.models import (Evidence, Freshness, GroundTruth, Market, Platform,
                         Tier, utcnow)
from data.store import Store
from agents.research import run_research_agent


class FakeNews:
    def __init__(self, evidence=None):
        self.evidence = evidence if evidence is not None else []
        self.queries = []

    def search(self, query, asof_ts, limit=5):
        self.queries.append(query)
        return self.evidence

    def is_available(self):
        return True

    def close(self):
        pass


class FakeResearchLLM:
    def complete(self, system, user, schema):
        props = schema.get("properties", {})
        if "summary" in props:
            return {"summary": "quotes show bullish development"}
        if "verdict" in props:
            return {"verdict": "support", "p_yes": 0.7, "rationale": "r"}
        raise AssertionError("unexpected schema")


def _market(asof=None, theme="x", question="Will X happen?"):
    return Market(market_id="m1", platform=Platform.POLYMARKET, question=question,
                  yes_price=0.5, volume_24h=1.0, spread=0.01, close_time=None,
                  theme=theme, asof_ts=asof or datetime.now(timezone.utc), raw={})


def _evidence(quote="development happened", source="news", hours_ago=2.0,
              asof=None):
    asof = asof or datetime.now(timezone.utc)
    return Evidence(quote=quote, source=source,
                    published_at=asof - timedelta(hours=hours_ago),
                    asof_ts=asof, freshness=Freshness.HOUR)


def test_recipe_loop_with_evidence_and_history(tmp_path):
    cfg = Config()
    store = Store(str(tmp_path / "t.db"))
    asof = datetime.now(timezone.utc)
    # Seed a past resolved market of the same theme (point-in-time history).
    store.upsert_market(Market(market_id="past", platform=Platform.KALSHI,
                               question="past X?", yes_price=0.4, volume_24h=0.0,
                               spread=0.0, theme="x", asof_ts=asof - timedelta(days=2),
                               raw={}))
    store.upsert_gt(GroundTruth(market_id="past", platform=Platform.KALSHI,
                                outcome=1.0, resolved_at=asof - timedelta(days=1),
                                source="s"))
    news = FakeNews([_evidence(asof=asof)])
    result = run_research_agent(_market(asof=asof), cfg, FakeResearchLLM(), news, store)
    assert result.news_summary is not None
    assert result.history_summary is not None
    assert result.verdict == "support"
    assert result.p_yes_updated == 0.7
    assert result.abstain is False
    assert result.llm_calls <= 3  # budget: 3 rounds x 1 LLM call
    assert len(result.evidence) == 1
    assert store.evidence_count("m1") == 1  # 铁律 4: evidence persisted


def test_tielu5_empty_evidence_abstains(tmp_path):
    cfg = Config()
    store = Store(str(tmp_path / "t.db"))
    result = run_research_agent(_market(), cfg, FakeResearchLLM(), FakeNews([]), store)
    assert result.abstain is True
    assert result.news_summary is None  # never补全 from impressions
    assert result.verdict is None


def test_tielu6_future_news_is_filtered(tmp_path):
    cfg = Config()
    store = Store(str(tmp_path / "t.db"))
    asof = datetime.now(timezone.utc)
    future = Evidence(quote="future info", source="news",
                      published_at=asof + timedelta(hours=1), asof_ts=asof,
                      freshness=Freshness.MINUTE)
    valid = _evidence(asof=asof)
    result = run_research_agent(_market(asof=asof), cfg, FakeResearchLLM(),
                                FakeNews([future, valid]), store)
    assert [e.quote for e in result.evidence] == ["development happened"]


def test_tielu4_evidence_without_quote_is_dropped(tmp_path):
    cfg = Config()
    store = Store(str(tmp_path / "t.db"))
    asof = datetime.now(timezone.utc)
    no_quote = Evidence(quote="", source="news", published_at=asof - timedelta(hours=1),
                        asof_ts=asof, freshness=Freshness.HOUR)
    result = run_research_agent(_market(asof=asof), cfg, FakeResearchLLM(),
                                FakeNews([no_quote]), store)
    assert result.evidence == []
    assert result.abstain is True  # no valid evidence -> 铁律 5 abstain
