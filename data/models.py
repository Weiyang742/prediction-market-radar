"""Unified data model for prediction-market data (polymarket + kalshi).

Field names follow the platform-normalised contract, not either platform's raw
schema.  Raw payloads are preserved in ``Market.raw`` so nothing is lost.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Platform(str, Enum):
    POLYMARKET = "polymarket"
    KALSHI = "kalshi"


class Direction(str, Enum):
    YES = "YES"
    NO = "NO"


class Freshness(str, Enum):
    """Evidence recency tier (FinAgent).  Tagged by the tool layer and shown
    explicitly in the Judge prompt so the model never mistakes a stale quote
    for live information."""
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"


class SignalType(str, Enum):
    JUMP = "jump"
    ACCELERATION = "acceleration"
    VOLUME_SPIKE = "volume_spike"
    SPREAD_SHOCK = "spread_shock"
    STALE_QUOTE = "stale_quote"
    CROSS_PLATFORM_DIVERGENCE = "cross_platform_divergence"
    NEGATION_PAIR_DEVIATION = "negation_pair_deviation"


class Severity(str, Enum):
    LOW = "LOW"
    MED = "MED"
    HIGH = "HIGH"


class Tier(int, Enum):
    T0 = 0
    T1 = 1
    T2 = 2


class Market(BaseModel):
    market_id: str
    platform: Platform
    question: str
    yes_price: float = Field(ge=0.0, le=1.0)
    volume_24h: float = 0.0
    spread: float = Field(ge=0.0, le=1.0, description="bid/ask spread as a fraction")
    close_time: datetime | None = None
    depth: float | None = None
    group_key: str | None = None
    theme: str | None = None
    series_ticker: str | None = None
    asof_ts: datetime
    raw: dict[str, Any] = Field(default_factory=dict)


class PricePoint(BaseModel):
    market_id: str
    ts: datetime
    price: float
    volume: float = 0.0
    spread: float | None = None
    asof_ts: datetime


class Signal(BaseModel):
    type: SignalType
    severity: Severity
    detail: str
    value: float | None = None
    asof_ts: datetime


class SampleOutput(BaseModel):
    role: str
    index: int
    p_yes: float
    reasoning: str | None = None
    raw: str | None = None


class DebateRecord(BaseModel):
    triggered: bool
    bull_core: str | None = None
    bear_core: str | None = None
    decisive_point: str | None = None


class Decision(BaseModel):
    decision_id: str
    market_id: str
    tier: Tier
    theme: str | None = None
    dominant_signal: str | None = None
    router_score: int = 0
    snapshot_hash: str
    samples: list[SampleOutput] = Field(default_factory=list)
    aggregate: float | None = None
    debate: DebateRecord | None = None
    direction: Direction
    p_yes: float
    confidence: float
    confidence_recalibrated: float | None = None
    recalib_coef: float | None = None
    abstain: bool
    abstain_reason: str | None = None
    thesis: str | None = None
    key_risks: list[str] = Field(default_factory=list)
    invalidated_if: str | None = None
    model: str
    lessons_injected: list[int] = Field(default_factory=list)
    dual_model: str | None = None
    dual_p_yes: float | None = None
    asof_ts: datetime


class GroundTruth(BaseModel):
    market_id: str
    platform: Platform
    outcome: float
    resolved_at: datetime
    source: str


class Lesson(BaseModel):
    id: int | None = None
    theme: str | None = None
    lesson: str
    source_snapshot_hash: str | None = None
    created_at: datetime


class Evidence(BaseModel):
    """A tool-returned piece of evidence (v2 research agent).

    铁律 4: only evidence with a non-empty ``quote`` + ``source`` may enter the
    Judge context — no原文, no admission.
    """
    quote: str
    source: str
    published_at: datetime | None = None
    asof_ts: datetime
    freshness: Freshness
    market_id: str | None = None


def snapshot_hash(data: dict[str, Any]) -> str:
    """Deterministic hash of the agent's input snapshot (point-in-time contract)."""
    canonical = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
