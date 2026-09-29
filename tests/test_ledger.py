"""Forward prediction ledger tests: hash chain, dedupe, point-in-time join."""
from datetime import datetime, timedelta, timezone

from data.ledger import Ledger, hash_entry, ledger_resolved_pairs
from data.models import GroundTruth, Platform


def _dt(days_ago: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _entry(**kw) -> dict:
    base = {"market_id": "m1", "question": "q?", "p_yes": 0.6, "confidence": 70.0,
            "tier": 1, "theme": "x", "asof_ts": _dt().isoformat(),
            "model": "m", "source": "demo"}
    base.update(kw)
    return base


def test_append_and_validate_chain(tmp_path):
    ledger = Ledger(tmp_path / "predictions.log")
    e1 = ledger.append(**_entry(market_id="m1"))
    e2 = ledger.append(**_entry(market_id="m2", asof_ts=_dt(1).isoformat()))
    assert e1["seq"] == 1 and e2["seq"] == 2
    assert e2["prev_hash"] == e1["hash"]
    ok, msg = ledger.validate()
    assert ok and "2 entries" in msg
    assert ledger.read()[1]["hash"] == hash_entry(e1["hash"], e2)


def test_dedupe_same_market_same_asof_day(tmp_path):
    ledger = Ledger(tmp_path / "predictions.log")
    assert ledger.append(**_entry(market_id="m1")) is not None
    # Same market, same as-of day -> idempotent (skip).
    assert ledger.append(**_entry(market_id="m1", p_yes=0.9)) is None
    # Same market, different day -> appended.
    assert ledger.append(**_entry(market_id="m1", asof_ts=_dt(1).isoformat())) is not None
    assert len(ledger.read()) == 2


def test_validate_detects_tampering(tmp_path):
    import json
    path = tmp_path / "predictions.log"
    ledger = Ledger(path)
    ledger.append(**_entry(market_id="m1"))
    ledger.append(**_entry(market_id="m2", asof_ts=_dt(1).isoformat()))
    # Tamper with a historical record's content without re-hashing it.
    lines = path.read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines]
    entries[0]["p_yes"] = 0.99
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
                    encoding="utf-8")
    ok, msg = Ledger(path).validate()
    assert not ok and "hash mismatch" in msg


def test_ledger_resolved_pairs_filters_after_resolution():
    resolved_at = _dt()
    gt = {"m1": GroundTruth(market_id="m1", platform=Platform.KALSHI,
                            outcome=1.0, resolved_at=resolved_at, source="s")}
    entries = [
        _entry(market_id="m1", asof_ts=(resolved_at - timedelta(hours=1)).isoformat(),
               p_yes=0.7),
        _entry(market_id="m1", asof_ts=(resolved_at + timedelta(hours=1)).isoformat(),
               p_yes=0.9),
        _entry(market_id="other", asof_ts=resolved_at.isoformat(), p_yes=0.5),
    ]
    pairs = ledger_resolved_pairs(entries, gt)
    assert len(pairs) == 1
    assert pairs[0][0]["p_yes"] == 0.7
    assert pairs[0][1] == 1.0
