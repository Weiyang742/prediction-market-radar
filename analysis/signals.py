"""Six signal rules (V3.1 §6.2).  Pure functions over synthetic series; no IO.

Local rules (this module): jump, acceleration, volume_spike, spread_shock,
stale_quote.  cross_platform_divergence and negation_pair_deviation live in
``divergence.py``.

Severity scaling is deterministic: MED when the magnitude is >= the threshold
and < 2x the threshold, HIGH at >= 2x (internal scaling; the base thresholds
are the uncalibrated config values, the 2x escalation is a simple convention).
"""
from __future__ import annotations

import statistics
from datetime import datetime
from typing import Sequence

from data.config import Config
from data.models import Market, PricePoint, Severity, Signal, SignalType, utcnow


def _severity(value: float, threshold: float) -> Severity:
    if value >= 2 * threshold:
        return Severity.HIGH
    return Severity.MED


def _rolling(values: Sequence[float], window: int) -> tuple[float, float] | None:
    win = values[-window:]
    if len(win) < 2:
        return None
    mean = statistics.fmean(win)
    stdev = statistics.pstdev(win) if len(win) >= 2 else 0.0
    return mean, stdev


def detect_jump(series: Sequence[PricePoint], cfg: Config) -> Signal | None:
    prices = [p.price for p in series]
    if len(prices) < cfg.jump_window_min // 2:
        return None
    roll = _rolling(prices, cfg.jump_window_min)
    if roll is None or roll[1] == 0:
        return None
    mean, stdev = roll
    z = abs(prices[-1] - mean) / stdev
    if z < cfg.jump_sigma:
        return None
    return Signal(type=SignalType.JUMP, severity=_severity(z, cfg.jump_sigma),
                  detail=f"|p - rolling mean| = {z:.1f}σ (window {cfg.jump_window_min}m)",
                  value=round(z, 3), asof_ts=utcnow())


def detect_acceleration(series: Sequence[PricePoint], cfg: Config) -> Signal | None:
    w = cfg.acceleration_window_min
    prices = [p.price for p in series]
    if len(prices) < 2 * w + 1:
        return None
    recent = [b - a for a, b in zip(prices[-w - 1:-1], prices[-w:])]
    prior = [b - a for a, b in zip(prices[-2 * w - 1:-w - 1], prices[-2 * w:-w])]
    recent_abs = sum(abs(x) for x in recent)
    prior_abs = sum(abs(x) for x in prior)
    if prior_abs == 0:
        return None
    ratio = recent_abs / prior_abs
    if ratio < cfg.acceleration_ratio:
        return None
    return Signal(type=SignalType.ACCELERATION,
                  severity=_severity(ratio, cfg.acceleration_ratio),
                  detail=f"15m |Δ| / prior 15m |Δ| = {ratio:.1f}x",
                  value=round(ratio, 3), asof_ts=utcnow())


def detect_volume_spike(series: Sequence[PricePoint], cfg: Config) -> Signal | None:
    vols = [p.volume for p in series]
    if len(vols) < 10:
        return None
    roll = _rolling(vols[:-1], max(10, cfg.jump_window_min))
    if roll is None or roll[1] == 0:
        return None
    mean, stdev = roll
    z = (vols[-1] - mean) / stdev
    if z < cfg.volume_zscore:
        return None
    return Signal(type=SignalType.VOLUME_SPIKE, severity=_severity(z, cfg.volume_zscore),
                  detail=f"minute volume z-score = {z:.1f}",
                  value=round(z, 3), asof_ts=utcnow())


def detect_spread_shock(series: Sequence[PricePoint], market: Market, cfg: Config) -> Signal | None:
    spreads = [p.spread for p in series if p.spread is not None]
    if len(spreads) < 2:
        # No historical spread series available: cannot compute a rolling mean.
        return None
    roll = _rolling(spreads, max(2, len(spreads)))
    if roll is None or roll[0] == 0:
        return None
    ratio = market.spread / roll[0]
    if ratio < cfg.spread_ratio:
        return None
    return Signal(type=SignalType.SPREAD_SHOCK, severity=_severity(ratio, cfg.spread_ratio),
                  detail=f"spread = {ratio:.1f}x rolling mean",
                  value=round(ratio, 3), asof_ts=utcnow())


def detect_stale_quote(market: Market, cfg: Config, last_update: datetime | None = None,
                       now: datetime | None = None) -> Signal | None:
    now = now or utcnow()
    reference = last_update or market.asof_ts
    age_min = (now - reference).total_seconds() / 60.0
    if age_min < cfg.stale_minutes:
        return None
    return Signal(type=SignalType.STALE_QUOTE, severity=Severity.MED,
                  detail=f"quote {age_min:.0f}min old (> {cfg.stale_minutes}m)",
                  value=round(age_min, 1), asof_ts=utcnow())


def compute_local_signals(market: Market, series: Sequence[PricePoint],
                          cfg: Config, now: datetime | None = None) -> list[Signal]:
    """``now`` lets callers replay a historical time point (backfill) instead of
    wall-clock time for the stale-quote rule."""
    out: list[Signal] = []
    for fn, args in (
        (detect_jump, (series,)),
        (detect_acceleration, (series,)),
        (detect_volume_spike, (series,)),
        (detect_spread_shock, (series, market)),
    ):
        sig = fn(*args, cfg)
        if sig is not None:
            out.append(sig)
    stale = detect_stale_quote(market, cfg, now=now)
    if stale is not None:
        out.append(stale)
    return out
