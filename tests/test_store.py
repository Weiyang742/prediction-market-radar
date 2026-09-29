from datetime import datetime, timezone

from data.models import (Decision, Direction, GroundTruth, Market, Platform,
                         PricePoint, Tier)
from data.store import Store


def test_store_roundtrip_decision(tmp_path):
    with Store(str(tmp_path / "t.db")) as store:
        d = Decision(decision_id="d1", market_id="m1", tier=Tier.T1, theme="x",
                     dominant_signal="jump", router_score=1, snapshot_hash="h1",
                     samples=[], aggregate=0.5, debate=None, direction=Direction.YES,
                     p_yes=0.5, confidence=80.0, abstain=False, thesis="t",
                     key_risks=[], model="m", asof_ts=datetime.now(timezone.utc))
        store.insert_decision(d)
        got = store.get_decisions()
        assert len(got) == 1
        assert got[0].decision_id == "d1"
        assert got[0].tier is Tier.T1


def test_store_price_series_ordered(tmp_path):
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    with Store(str(tmp_path / "t.db")) as store:
        store.insert_price_points([
            PricePoint(market_id="m", ts=now, price=0.5, volume=1, asof_ts=now),
            PricePoint(market_id="m", ts=now + timedelta(minutes=1), price=0.6, volume=1, asof_ts=now),
        ])
        series = store.get_price_series("m")
        assert [p.price for p in series] == [0.5, 0.6]


def test_store_snapshot_save_and_get(tmp_path):
    with Store(str(tmp_path / "t.db")) as store:
        store.save_snapshot("h", '{"a": 1}')
        assert store.get_snapshot("h") == '{"a": 1}'
        assert store.get_snapshot("missing") is None


def test_store_gt_upsert(tmp_path):
    now = datetime.now(timezone.utc)
    with Store(str(tmp_path / "t.db")) as store:
        store.upsert_gt(GroundTruth(market_id="m", platform=Platform.POLYMARKET,
                                    outcome=1.0, resolved_at=now, source="s"))
        assert store.get_gt()["m"].outcome == 1.0


def test_store_roundtrip_lessons_injected_and_dual(tmp_path):
    with Store(str(tmp_path / "t.db")) as store:
        d = Decision(decision_id="d2", market_id="m2", tier=Tier.T1, theme="x",
                     dominant_signal="jump", router_score=1, snapshot_hash="h2",
                     samples=[], aggregate=0.5, debate=None, direction=Direction.YES,
                     p_yes=0.5, confidence=80.0, abstain=False, thesis="t",
                     key_risks=[], model="m", lessons_injected=[1, 2],
                     dual_model="deepseek-chat", dual_p_yes=0.55,
                     asof_ts=datetime.now(timezone.utc))
        store.insert_decision(d)
        got = store.get_decisions()[0]
        assert got.lessons_injected == [1, 2]
        assert got.dual_model == "deepseek-chat"
        assert got.dual_p_yes == 0.55


def test_store_migration_adds_columns_to_existing_db(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    # Pre-migration decisions table (only the columns the index references).
    conn.execute("CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, tier INTEGER)")
    conn.commit()
    conn.close()
    store = Store(path)
    cols = {r["name"] for r in store.conn.execute("PRAGMA table_info(decisions)")}
    assert {"lessons_injected", "dual_model", "dual_p_yes"} <= cols
    store.close()


def test_history_analogies_point_in_time(tmp_path):
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    with Store(str(tmp_path / "t.db")) as store:
        store.upsert_market(Market(market_id="a", platform=Platform.POLYMARKET,
                                   question="old A", yes_price=0.5, volume_24h=0.0,
                                   spread=0.0, theme="fed_rates",
                                   asof_ts=now - timedelta(days=2), raw={}))
        store.upsert_gt(GroundTruth(market_id="a", platform=Platform.POLYMARKET,
                                    outcome=1.0, resolved_at=now - timedelta(days=1),
                                    source="s"))
        store.upsert_market(Market(market_id="b", platform=Platform.POLYMARKET,
                                   question="future B", yes_price=0.5, volume_24h=0.0,
                                   spread=0.0, theme="fed_rates",
                                   asof_ts=now + timedelta(days=1), raw={}))
        store.upsert_gt(GroundTruth(market_id="b", platform=Platform.POLYMARKET,
                                    outcome=0.0, resolved_at=now + timedelta(days=2),
                                    source="s"))
        rows = store.history_analogies("fed_rates", now, cap=5)
        assert [r["market_id"] for r in rows] == ["a"]  # only point-in-time
