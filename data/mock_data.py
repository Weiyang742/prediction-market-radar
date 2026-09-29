"""Deterministic synthetic data for ``--mock`` mode.

Mock data is clearly synthetic and deterministic (fixed seed).  It exists only
to exercise the CLI / report / calibration pipeline without network or an LLM
key.  It is never used to produce ablation numbers (examples/ablation.md is
blocked on real resolved markets).
"""
from __future__ import annotations

import hashlib
import random
from datetime import timedelta

from data.config import Config
from data.models import (DebateRecord, Decision, Direction, GroundTruth, Market,
                         Platform, PricePoint, SampleOutput, Tier, utcnow)

_MARKET_TEMPLATES = [
    # (theme, question, yes_price, volume_24h, spread, has_jump, group_key)
    ("fed_rates", "Fed cuts rates in Sep 2026?", 0.42, 8_200_000, 0.01, True, "sep_fomc"),
    ("fed_rates", "Fed holds rates in Sep 2026?", 0.47, 7_100_000, 0.02, False, "sep_fomc"),
    ("fed_rates", "Fed cuts 50bp in Sep 2026?", 0.08, 3_400_000, 0.03, False, "sep_fomc"),
    ("us_elections", "Candidate A wins 2026 presidential election?", 0.55, 12_000_000, 0.01, True, "pres_2026"),
    ("us_elections", "Candidate B wins 2026 presidential election?", 0.44, 11_300_000, 0.01, False, "pres_2026"),
    ("us_elections", "Senate stays in majority's hands?", 0.62, 2_100_000, 0.02, False, "senate_2026"),
    ("btc_price", "Bitcoin above $100k on Sep 30?", 0.61, 9_500_000, 0.01, False, "btc_sep30"),
    ("btc_price", "Bitcoin below $90k on Sep 30?", 0.28, 4_900_000, 0.02, False, "btc_sep30"),
    ("weather", "Rain in NYC on Sep 10?", 0.30, 45_000, 0.08, False, "nyc_weather"),
    ("other", "Olympic gold for country X in 2028?", 0.12, 28_000, 0.15, False, "olympics_2028"),
]


def mock_markets(cfg: Config) -> list[Market]:
    now = utcnow()
    out: list[Market] = []
    for i, (theme, question, price, vol, spread, has_jump, group) in enumerate(_MARKET_TEMPLATES):
        platform = Platform.POLYMARKET if i % 2 == 0 else Platform.KALSHI
        close = now + timedelta(days=(i % 3) * 20 + 5)
        out.append(Market(
            market_id=f"mock-{i}", platform=platform, question=question,
            yes_price=price, volume_24h=vol, spread=spread, close_time=close,
            depth=vol * (0.02 if has_jump else 0.005), group_key=group,
            theme=theme, asof_ts=now, raw={},
        ))
    return out


def mock_series(market: Market, cfg: Config, minutes: int = 120) -> list[PricePoint]:
    seed = int(hashlib.md5(market.market_id.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    now = utcnow()
    base = market.yes_price
    has_jump = market.market_id in ("mock-0", "mock-3")
    jump_min = minutes - 15
    points: list[PricePoint] = []
    p = base
    for i in range(minutes):
        ts = now - timedelta(minutes=minutes - i)
        if has_jump and i == jump_min:
            p = min(0.99, max(0.01, p + 0.08))
        else:
            p = min(0.99, max(0.01, p + rng.gauss(0, 0.004)))
        vol = rng.lognormvariate(4.0, 0.25)
        spread = max(0.001, market.spread * rng.uniform(0.7, 1.3))
        if has_jump and i == jump_min:
            vol *= 8
            spread *= 3
        points.append(PricePoint(market_id=market.market_id, ts=ts, price=round(p, 4),
                                 volume=round(vol, 1), spread=round(spread, 4), asof_ts=now))
    return points


def seed_mock_resolved(store, cfg: Config) -> None:
    """Insert synthetic resolved markets + decisions so ``calibrate --mock`` can
    demonstrate the scoring pipeline.  Output is MOCK and must not be quoted as
    ablation."""
    rng = random.Random(7)
    now = utcnow()
    for i in range(30):
        theme = _MARKET_TEMPLATES[i % len(_MARKET_TEMPLATES)][0]
        outcome = float(rng.random() < 0.5)
        mid = f"mock-resolved-{i}"
        gt = GroundTruth(market_id=mid, platform=Platform.POLYMARKET, outcome=outcome,
                         resolved_at=now - timedelta(days=i % 14), source="mock")
        store.upsert_gt(gt)
        # Persist a market row so the local history library can find this
        # resolved event by theme (point-in-time analogies).
        store.upsert_market(Market(
            market_id=mid, platform=Platform.POLYMARKET,
            question=f"mock resolved market {i}", yes_price=outcome,
            volume_24h=0.0, spread=0.0,
            close_time=now - timedelta(days=i % 14), depth=None,
            group_key=None, theme=theme,
            asof_ts=now - timedelta(days=i % 14), raw={}))
        tier = Tier(rng.choice([0, 1, 1, 2]))
        p_yes = round(rng.uniform(0.1, 0.9), 3)
        conf = round(rng.uniform(50, 90), 1)
        abstain = rng.random() < 0.15
        store.insert_decision(Decision(
            decision_id=f"mock-dec-{i}", market_id=mid, tier=tier, theme=theme,
            snapshot_hash=f"mockhash{i:04d}",
            samples=[SampleOutput(role="bull", index=k, p_yes=p_yes) for k in range(3)]
                    + [SampleOutput(role="bear", index=k, p_yes=1 - p_yes) for k in range(3)],
            aggregate=p_yes, debate=None, direction=Direction.YES if p_yes > 0.5 else Direction.NO,
            p_yes=p_yes, confidence=conf, confidence_recalibrated=None, recalib_coef=None,
            abstain=abstain, abstain_reason="mock" if abstain else None, thesis="mock",
            key_risks=[], invalidated_if=None, model="mock",
            lessons_injected=[1, 2] if i % 3 == 0 else [],
            asof_ts=now - timedelta(days=i % 14),
        ))
    # Seed GT for the open mock markets (mock-0..mock-9) so a previous
    # `run --mock` prediction ledger can be joined by `calibrate --mock`.
    for i in range(len(_MARKET_TEMPLATES)):
        store.upsert_gt(GroundTruth(
            market_id=f"mock-{i}",
            platform=Platform.POLYMARKET if i % 2 == 0 else Platform.KALSHI,
            outcome=float(i % 2), resolved_at=now, source="mock"))
