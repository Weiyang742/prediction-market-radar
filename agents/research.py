"""CGAD extension: proactive research agent v2 (dev doc §四).

Fixed recipe loop built from hand-crafted skill chunks (no SPACE training):
triggered for Tier 2 signals, at most 3 rounds, each round = one tool call +
at most one LLM call.

  round 1: news retrieval  -> distill "what happened"
  round 2: local history library analogy (same-theme past price paths)
  round 3: cross-validation (support/overturn the original probability)

Hallucination contract (铁律 4-6, written before any loop code):

  铁律 4: tool-returned原文 must be persisted with the evidence (quote + source
          + asof_ts); evidence without quote/source never enters Judge context.
  铁律 5: no retrieved evidence = empty evidence, go abstain branch; never
          complete from model impressions.
  铁律 6: historical replay must filter news ``published_at < asof_ts``
          (Lopez-Lira training-data contamination + MR-3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from data.config import Config
from data.models import Evidence, Market
from data.news import NewsClient, NewsError
from data.store import Store
from agents import prompts
from agents.prompts import CROSS_VALIDATE_SCHEMA, DISTILL_SCHEMA


@dataclass
class ResearchResult:
    evidence: list[Evidence] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    news_summary: str | None = None
    history_summary: str | None = None
    verdict: str | None = None  # support | overturn | insufficient
    p_yes_updated: float | None = None
    abstain: bool = False
    abstain_reason: str | None = None
    rounds_used: int = 0
    llm_calls: int = 0


def _evidence_line(e: Evidence) -> str:
    pub = e.published_at.isoformat() if e.published_at else "unknown"
    return (f"[freshness={e.freshness.value}] source={e.source} "
            f"published_at={pub} quote={e.quote}")


def _history_line(row: dict[str, Any]) -> str:
    tail = row.get("price_path", [])[-6:]
    path = ", ".join(f"{p:.2f}" for p in tail) if tail else "n/a"
    return (f"{row['market_id']}: {row['question']} -> outcome={row['outcome']:.0f} "
            f"(resolved {row['resolved_at']}); price tail: {path}")


def run_research_agent(market: Market, cfg: Config, llm: Any,
                       news: NewsClient, store: Store | None = None) -> ResearchResult:
    """Run the fixed recipe loop for one Tier 2 signal.

    Returns a ``ResearchResult``; its ``abstain`` flag must be folded into the
    Judge's selective gate (铁律 5)."""
    result = ResearchResult()
    question = market.question
    theme = market.theme
    asof = market.asof_ts
    base = market.yes_price

    # Round 1: news retrieval (tool).  铁律 6 point-in-time + 铁律 4 quote/source.
    try:
        found = news.search(question, asof, cfg.research_news_limit)
    except NewsError as exc:
        result.abstain = True
        result.abstain_reason = f"news retrieval failed: {exc}"
        return result
    evidence = [
        e for e in found
        if e.quote.strip() and e.source.strip()
        and (e.published_at is None or e.published_at < asof)  # 铁律 6
    ][: cfg.research_news_limit]
    result.evidence = evidence
    if store is not None:
        for e in evidence:
            e.market_id = market.market_id
            store.insert_evidence(e)

    if evidence:
        result.rounds_used += 1
        result.llm_calls += 1
        r = llm.complete(*prompts.distill_news_prompt(
            question, [_evidence_line(e) for e in evidence]), schema=DISTILL_SCHEMA)
        result.news_summary = str(r.get("summary") or "")

    # Round 2: local history library analogy (tool) -> distill.
    history = (store.history_analogies(theme, asof, cfg.research_history_cap)
               if store is not None else [])
    result.history = history
    if history:
        result.rounds_used += 1
        result.llm_calls += 1
        r = llm.complete(*prompts.distill_history_prompt(
            theme or "other", [_history_line(h) for h in history]), schema=DISTILL_SCHEMA)
        result.history_summary = str(r.get("summary") or "")

    # 铁律 5: no evidence at all -> abstain, never补全 from impressions.
    if not result.news_summary and not result.history_summary:
        result.abstain = True
        result.abstain_reason = "no retrieved evidence (news empty, no local analogies)"
        return result

    # Round 3: cross-validation -> updated P(YES).
    result.rounds_used += 1
    result.llm_calls += 1
    r = llm.complete(*prompts.cross_validate_prompt(
        question, base, result.news_summary, result.history_summary),
        schema=CROSS_VALIDATE_SCHEMA)
    result.verdict = str(r.get("verdict") or "insufficient")
    try:
        result.p_yes_updated = max(0.0, min(1.0, float(r.get("p_yes"))))
    except (TypeError, ValueError):
        result.p_yes_updated = base
    if result.verdict == "insufficient":
        result.abstain = True
        result.abstain_reason = "evidence insufficient to move the probability"
    return result


def research_snapshot_block(result: ResearchResult) -> dict[str, Any]:
    """The point-in-time block attached to the decision snapshot (铁律 4)."""
    return {
        "news_summary": result.news_summary,
        "history_summary": result.history_summary,
        "verdict": result.verdict,
        "p_yes_updated": result.p_yes_updated,
        "abstain": result.abstain,
        "abstain_reason": result.abstain_reason,
        "rounds_used": result.rounds_used,
        "llm_calls": result.llm_calls,
        "evidence": [
            {"quote": e.quote, "source": e.source,
             "published_at": e.published_at.isoformat() if e.published_at else None,
             "freshness": e.freshness.value}
            for e in result.evidence
        ],
    }
