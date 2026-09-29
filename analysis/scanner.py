"""Market-value filter pipeline (R3), including the wash-trading (对倒量) check.

Order of checks (V3.1 §6.2): volume -> spread -> days-to-close -> wash-trading
-> sort by volume -> top N.  Wash criteria 1 (depth/volume ratio below theme
median) and 2 (lone-peak minute volume with tiny price move) are implemented;
criterion 3 (buyer/seller concentration) needs a trade-level feed that the
current data layer does not ingest, and is therefore reported as unavailable
rather than silently passed.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Sequence

from data.config import Config
from data.models import Market, PricePoint


def depth_volume_ratio(m: Market) -> float:
    if m.volume_24h <= 0:
        return 0.0
    return (m.depth or 0.0) / m.volume_24h


def theme_median_ratios(markets: Sequence[Market]) -> dict[str, float]:
    by_theme: dict[str, list[float]] = defaultdict(list)
    for m in markets:
        if m.theme:
            by_theme[m.theme].append(depth_volume_ratio(m))
    return {t: statistics.median(v) for t, v in by_theme.items()}


def flag_wash(market: Market, series: Sequence[PricePoint],
              median_ratio: float | None, cfg: Config) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    # Criterion 1: depth/volume ratio far below the theme median.
    ratio = depth_volume_ratio(market)
    if median_ratio is not None and median_ratio > 0 and ratio < cfg.wash_depth_ratio_mult * median_ratio:
        reasons.append(
            f"depth/volume ratio {ratio:.4f} below {cfg.wash_depth_ratio_mult}x theme median {median_ratio:.4f}")

    # Criterion 2: lone-peak minute volume with near-zero price move.
    vols = [p.volume for p in series]
    if len(vols) >= 10:
        median_vol = statistics.median(vols[:-1]) if len(vols) >= 2 else 1.0
        peak_idx = max(range(len(vols)), key=lambda i: vols[i])
        peak_move = abs(series[peak_idx].price - series[peak_idx - 1].price) if peak_idx > 0 else 0.0
        if median_vol > 0 and vols[peak_idx] / median_vol > cfg.wash_peak_volume_ratio \
                and peak_move < cfg.wash_peak_price_move:
            reasons.append(
                f"lone-peak minute volume {vols[peak_idx]:.0f} vs median {median_vol:.0f} "
                f"with price move {peak_move:.4f}")

    # Criterion 3 requires trade-level data (not ingested); never asserted.
    return bool(reasons), reasons


def scan(markets: Sequence[Market], series_by_id: dict[str, list[PricePoint]],
         cfg: Config) -> tuple[list[Market], dict[str, list[str]]]:
    """Return (selected_markets, wash_flags).  Preserves point-in-time inputs."""
    medians = theme_median_ratios(markets)
    wash_flags: dict[str, list[str]] = {}
    selected: list[Market] = []
    for m in markets:
        if m.volume_24h < cfg.min_volume_24h:
            continue
        if m.spread * 100.0 > cfg.max_spread_pp:
            continue
        if m.close_time is not None:
            days = (m.close_time - m.asof_ts).total_seconds() / 86400.0
            if days > cfg.max_days_to_close:
                continue
        flagged, reasons = flag_wash(m, series_by_id.get(m.market_id, []),
                                     medians.get(m.theme), cfg)
        if flagged:
            wash_flags[m.market_id] = reasons
            continue
        selected.append(m)
    selected.sort(key=lambda m: m.volume_24h, reverse=True)
    return selected[: cfg.top_n], wash_flags
