"""Runtime configuration.

Every threshold marked 【假设】 in the technical spec V3.1 lives here as a
dataclass field (and is exposed as a CLI flag by ``main.py``).  ``ASSUMPTIONS``
maps each such field to its note; the README and the tests both read it so a
threshold cannot silently become "calibrated".
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


def _load_dotenv() -> None:
    """Minimal .env loader (stdlib only, no python-dotenv dependency).

    Does not override variables already present in the environment."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Config:
    # ---- API / rate limit ----
    kalshi_rate_limit_rps: float = 15.0
    polymarket_rate_limit_rps: float = 30.0
    backoff_base_s: float = 0.25
    backoff_cap_s: float = 2.0
    backoff_jitter_s: float = 0.1
    backoff_max_attempts: int = 6
    request_timeout_s: float = 10.0

    # ---- scanner (assumption) ----
    min_volume_24h: float = 50_000.0
    max_spread_pp: float = 5.0
    max_days_to_close: int = 90
    top_n: int = 20
    wash_depth_ratio_mult: float = 0.3
    wash_peak_volume_ratio: float = 5.0
    wash_peak_price_move: float = 0.005

    # ---- signals (assumption) ----
    jump_sigma: float = 3.0
    jump_window_min: int = 60
    acceleration_window_min: int = 15
    acceleration_ratio: float = 2.0
    volume_zscore: float = 2.0
    spread_ratio: float = 2.0
    stale_minutes: int = 10

    # ---- divergence (assumption) ----
    divergence_pp: float = 3.0
    divergence_high_pp: float = 5.0
    negation_pair_threshold_pp: float = 5.0
    group_sum_deviation_pp: float = 5.0

    # ---- CGAD (assumption) ----
    bull_bear_samples: int = 3
    debate_trigger_pp: float = 10.0
    abstain_default_pp: float = 15.0
    abstain_signal_weight: float = 1.0
    recalibration_min_samples: int = 5
    lessons_cap: int = 3
    # SOD semantic-outlier aggregation (ARMOR-MAD) replaces naive trimmed mean.
    aggregation_method: str = "sod"  # "sod" | "trimmed"
    # Dual-model abstention anchor (DeepFund trading-personality finding).
    second_model: str | None = "deepseek-chat"
    second_model_base_url: str | None = None
    second_model_api_key: str | None = None
    dual_model_abstain_pp: float = 15.0

    # ---- proactive research agent v2 (recipe loop + evidence) ----
    research_enabled: bool = True
    research_news_limit: int = 5
    research_history_cap: int = 3
    search_api_key: str | None = None
    search_api_url: str = "https://google.serper.dev/search"

    # ---- cost (estimate, not measured) ----
    tier1_cost_usd: float = 0.015
    tier2_cost_usd: float = 0.06
    daily_decision_cap: int = 20
    daily_cost_cap_usd: float = 1.6

    # ---- LLM ----
    model: str = "gpt-4o-mini"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_temperature: float = 0.7
    llm_timeout_s: float = 60.0
    llm_max_failures: int = 3

    # ---- prediction ledger (Forward Prediction Ledger) ----
    ledger_path: str = "predictions.log"
    ledger_source: str = "prod"  # "demo" | "prod"

    # ---- misc ----
    mock: bool = False
    db_path: str = "data.db"

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv()
        cfg = cls()
        cfg.llm_api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        cfg.llm_base_url = os.environ.get("LLM_BASE_URL", cfg.llm_base_url)
        cfg.model = os.environ.get("LLM_MODEL", cfg.model)
        cfg.search_api_key = os.environ.get("SEARCH_API_KEY")
        cfg.search_api_url = os.environ.get("SEARCH_API_URL", cfg.search_api_url)
        cfg.second_model = os.environ.get("SECOND_LLM_MODEL") or cfg.second_model
        cfg.second_model_base_url = os.environ.get("SECOND_LLM_BASE_URL")
        cfg.second_model_api_key = os.environ.get("SECOND_LLM_API_KEY")
        return cfg

    def apply_overrides(self, overrides: dict[str, Any]) -> None:
        known = {f.name for f in fields(self)}
        for key, value in overrides.items():
            if value is None:
                continue
            if key not in known:
                raise KeyError(f"unknown config key: {key}")
            setattr(self, key, value)

    def validate(self) -> None:
        """Fail fast on invalid configuration (production guard).

        Raises ``ValueError`` listing every invalid value so operators never
        silently run with a nonsensical threshold."""
        problems: list[str] = []
        if self.kalshi_rate_limit_rps <= 0:
            problems.append("kalshi_rate_limit_rps must be > 0")
        if self.polymarket_rate_limit_rps <= 0:
            problems.append("polymarket_rate_limit_rps must be > 0")
        if self.backoff_base_s < 0 or self.backoff_cap_s < 0 or self.backoff_jitter_s < 0:
            problems.append("backoff values must be >= 0")
        if self.backoff_max_attempts < 1:
            problems.append("backoff_max_attempts must be >= 1")
        if self.request_timeout_s <= 0:
            problems.append("request_timeout_s must be > 0")
        if self.top_n <= 0:
            problems.append("top_n must be > 0")
        if self.min_volume_24h < 0 or self.max_spread_pp < 0 or self.max_days_to_close < 0:
            problems.append("scanner thresholds must be >= 0")
        if self.bull_bear_samples < 1:
            problems.append("bull_bear_samples must be >= 1")
        for name in ("debate_trigger_pp", "abstain_default_pp", "dual_model_abstain_pp"):
            value = getattr(self, name)
            if value < 0 or value > 100:
                problems.append(f"{name} must be within [0, 100]")
        if self.recalibration_min_samples < 1:
            problems.append("recalibration_min_samples must be >= 1")
        if self.lessons_cap < 0:
            problems.append("lessons_cap must be >= 0")
        if self.research_news_limit < 0 or self.research_history_cap < 0:
            problems.append("research limits must be >= 0")
        if self.aggregation_method not in ("sod", "trimmed"):
            problems.append("aggregation_method must be 'sod' or 'trimmed'")
        if self.daily_decision_cap < 0 or self.daily_cost_cap_usd < 0:
            problems.append("daily caps must be >= 0")
        if problems:
            raise ValueError("invalid config: " + "; ".join(problems))


# field name -> note.  Every entry is an uncalibrated design assumption.
ASSUMPTIONS: dict[str, str] = {
    "min_volume_24h": "scanner min 24h volume filter (V3.1 §6.2) — default uncalibrated",
    "max_spread_pp": "scanner max spread filter — default uncalibrated",
    "max_days_to_close": "scanner max days to close filter — default uncalibrated",
    "top_n": "scanner top-N cutoff — default uncalibrated",
    "wash_depth_ratio_mult": "wash-trading depth/volume ratio multiple of theme median — default uncalibrated",
    "wash_peak_volume_ratio": "wash-trading lone-peak minute-volume ratio — default uncalibrated",
    "wash_peak_price_move": "wash-trading max price move in a lone-peak minute — default uncalibrated",
    "jump_sigma": "jump signal sigma threshold — default uncalibrated",
    "jump_window_min": "jump rolling window (minutes) — default uncalibrated",
    "acceleration_window_min": "acceleration window (minutes) — default uncalibrated",
    "acceleration_ratio": "acceleration first-diff ratio — default uncalibrated",
    "volume_zscore": "volume spike z-score — default uncalibrated",
    "spread_ratio": "spread shock ratio vs rolling mean — default uncalibrated",
    "stale_minutes": "stale quote minutes — default uncalibrated",
    "divergence_pp": "cross-platform divergence trigger (pp) — default uncalibrated",
    "divergence_high_pp": "cross-platform divergence HIGH escalation (pp) — default uncalibrated",
    "negation_pair_threshold_pp": "negation pair |P(E)+P(¬E)-1| threshold (pp) — default uncalibrated",
    "group_sum_deviation_pp": "group probability-sum deviation escalation (pp) — default uncalibrated",
    "bull_bear_samples": "Bull/Bear independent samples K — default uncalibrated",
    "debate_trigger_pp": "debate trigger disagreement (pp) — default uncalibrated",
    "abstain_default_pp": "default abstain disagreement (pp) — default uncalibrated",
    "abstain_signal_weight": "signal-strength weight in abstain score — default uncalibrated",
    "recalibration_min_samples": "min samples per group before a recalibration coefficient is produced — default uncalibrated",
    "lessons_cap": "max lessons injected per theme — default uncalibrated",
    "dual_model_abstain_pp": "dual-model P(YES) divergence that forces abstention (pp) — default uncalibrated",
    "research_news_limit": "max news evidence items retrieved per signal — default uncalibrated",
    "research_history_cap": "max local-history analogies retrieved per signal — default uncalibrated",
}

# field name -> note.  Engineering estimates, to be replaced by POC measurement.
ESTIMATES: dict[str, str] = {
    "tier1_cost_usd": "per-decision cost estimate (V3.1 §7) — estimate, not measured",
    "tier2_cost_usd": "per-decision cost estimate — estimate, not measured",
    "daily_decision_cap": "daily decision cap — estimate, not measured",
    "daily_cost_cap_usd": "daily cost hard cap — estimate, not measured",
}
