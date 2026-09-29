"""Forward Prediction Ledger (``predictions.log``).

An append-only, hash-chained public record of the agent's pre-hoc predictions
on open markets.  Each entry commits to a content hash
``sha256(prev_hash + canonical_content)`` so any tampering with history is
detectable.  The file is committed to the repository and never edited in
place: errors are corrected by appending a correction record, never by
rewriting history (that discipline is what makes the ledger evidence).

Methodology anchor: DeepFund (arXiv:2505.11065) live forward testing — the
ledger is the project's forward track record, scored as "pre-hoc Brier" once
markets resolve, separate from the replay/backtest Brier.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from data.models import GroundTruth

# Fields that participate in the content hash.  ``seq`` / ``prev_hash`` /
# ``hash`` are chain metadata and are intentionally excluded.
_CONTENT_FIELDS = ("market_id", "question", "p_yes", "confidence", "tier",
                   "theme", "asof_ts", "model", "source")


def _canonical(entry: dict[str, Any]) -> str:
    data = {k: entry.get(k) for k in _CONTENT_FIELDS}
    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def hash_entry(prev_hash: str, entry: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical(entry)).encode("utf-8")).hexdigest()


class Ledger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def validate(self) -> tuple[bool, str]:
        """Verify the hash chain.  Returns (ok, message)."""
        entries = self.read()
        prev = ""
        for e in entries:
            expected = hash_entry(prev, e)
            if e.get("hash") != expected:
                return False, f"hash mismatch at seq {e.get('seq')}"
            prev = expected
        return True, f"{len(entries)} entries, chain valid"

    def append(self, *, market_id: str, question: str, p_yes: float,
               confidence: float, tier: int, theme: str | None,
               asof_ts: datetime, model: str, source: str) -> dict[str, Any] | None:
        """Append one prediction record.

        Returns the appended entry, or ``None`` when a record for the same
        market on the same as-of day already exists (idempotent per run day).
        """
        entries = self.read()
        asof_str = asof_ts.isoformat() if isinstance(asof_ts, datetime) else str(asof_ts)
        day = asof_str[:10]
        for e in entries:
            if e.get("market_id") == market_id and str(e.get("asof_ts", ""))[:10] == day:
                return None
        seq = len(entries) + 1
        prev_hash = entries[-1]["hash"] if entries else ""
        data = {
            "market_id": market_id,
            "question": question,
            "p_yes": round(float(p_yes), 4),
            "confidence": round(float(confidence), 2),
            "tier": int(tier),
            "theme": theme,
            "asof_ts": asof_str,
            "model": model,
            "source": source,
        }
        entry = {"seq": seq, "prev_hash": prev_hash, **data,
                 "hash": hash_entry(prev_hash, data)}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry


def ledger_resolved_pairs(entries: list[dict[str, Any]],
                          gt: dict[str, GroundTruth]) -> list[tuple[dict[str, Any], float]]:
    """Join ledger records with resolved markets.

    A record counts only when it was made strictly *before* resolution (the
    point-in-time / no-leakage filter).  Returns ``(entry, outcome)`` pairs.
    """
    out: list[tuple[dict[str, Any], float]] = []
    for e in entries:
        g = gt.get(e.get("market_id", ""))
        if g is None:
            continue
        try:
            asof = datetime.fromisoformat(e["asof_ts"])
        except (KeyError, ValueError):
            continue
        if asof >= g.resolved_at:
            continue
        out.append((e, g.outcome))
    return out
