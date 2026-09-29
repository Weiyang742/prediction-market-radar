"""Theme bucketing (versionable keyword rules) — V3.1 §6.2 step 2."""
from __future__ import annotations

THEME_RULES: dict[str, list[str]] = {
    "fed_rates": ["fed", "rate cut", "rate hike", "interest rate", "fomc",
                  "fed funds", "cut", "hike", "basis point", "bp", "hold"],
    "us_elections": ["election", "president", "senate", "house", "primary",
                     "nominee", "electoral", "governor", "ballot"],
    "btc_price": ["bitcoin", "btc", "crypto", "ethereum", "eth "],
    "weather": ["temperature", "rain", "snow", "hurricane", "high in", "low in"],
}


def classify_theme(question: str) -> str:
    q = question.lower()
    for theme, keywords in THEME_RULES.items():
        if any(k in q for k in keywords):
            return theme
    return "other"


def similarity(q1: str, q2: str) -> float:
    """Character-level + keyword-overlap similarity used for cross-platform
    pairing (V3.1 §6.2 step 3).  Not a calibrated metric — a coarse filter
    whose output is later confirmed by an LLM discriminator."""
    a, b = q1.lower(), q2.lower()
    if not a or not b:
        return 0.0
    bigrams = lambda s: {s[i:i + 2] for i in range(len(s) - 1)} or {s[:1]}
    sa, sb = bigrams(a), bigrams(b)
    dice = 2 * len(sa & sb) / max(1, len(sa) + len(sb))
    wa, wb = set(a.split()), set(b.split())
    jac = len(wa & wb) / max(1, len(wa | wb))
    return round(0.5 * dice + 0.5 * jac, 4)
