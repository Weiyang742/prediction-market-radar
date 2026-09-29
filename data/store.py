"""SQLite persistence layer (point-in-time contract).

Every row carries ``asof_ts``.  Decisions store the full agent input snapshot
hash plus the raw per-sample outputs, so a resolution can be replayed and
scored later (R13 layered ablation).  Composite index (market_id, ts) supports
per-market time-series replay in milliseconds (V3.1 §5.3 storage rationale).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from data.models import (Decision, Evidence, GroundTruth, Lesson, Market,
                         PricePoint, SampleOutput, utcnow)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    market_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    question TEXT NOT NULL,
    yes_price REAL NOT NULL,
    volume_24h REAL NOT NULL,
    spread REAL NOT NULL,
    close_time TEXT,
    depth REAL,
    group_key TEXT,
    theme TEXT,
    asof_ts TEXT NOT NULL,
    raw TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_points (
    market_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    price REAL NOT NULL,
    volume REAL NOT NULL,
    spread REAL,
    asof_ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_points_market_ts ON price_points (market_id, ts);
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    tier INTEGER NOT NULL,
    theme TEXT,
    dominant_signal TEXT,
    router_score INTEGER NOT NULL DEFAULT 0,
    snapshot_hash TEXT NOT NULL,
    samples TEXT NOT NULL,
    aggregate REAL,
    debate TEXT,
    direction TEXT NOT NULL,
    p_yes REAL NOT NULL,
    confidence REAL NOT NULL,
    confidence_recalibrated REAL,
    recalib_coef REAL,
    abstain INTEGER NOT NULL,
    abstain_reason TEXT,
    thesis TEXT,
    key_risks TEXT NOT NULL,
    invalidated_if TEXT,
    model TEXT NOT NULL,
    lessons_injected TEXT NOT NULL DEFAULT '[]',
    dual_model TEXT,
    dual_p_yes REAL,
    asof_ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_tier ON decisions (tier);
CREATE TABLE IF NOT EXISTS ground_truth (
    market_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    outcome REAL NOT NULL,
    resolved_at TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    theme TEXT,
    lesson TEXT NOT NULL,
    source_snapshot_hash TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calibrations (
    group_key TEXT PRIMARY KEY,
    n INTEGER NOT NULL,
    mean_confidence REAL NOT NULL,
    accuracy REAL NOT NULL,
    coef REAL NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ops_log (
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ops_ts ON ops_log (ts);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_hash TEXT PRIMARY KEY,
    snapshot TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT,
    query TEXT,
    quote TEXT NOT NULL,
    source TEXT NOT NULL,
    published_at TEXT,
    asof_ts TEXT NOT NULL,
    freshness TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_market ON evidence (market_id);
"""


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the initial schema to pre-existing DBs."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(decisions)")}
        if "lessons_injected" not in cols:
            self.conn.execute(
                "ALTER TABLE decisions ADD COLUMN lessons_injected TEXT NOT NULL DEFAULT '[]'")
        if "dual_model" not in cols:
            self.conn.execute("ALTER TABLE decisions ADD COLUMN dual_model TEXT")
        if "dual_p_yes" not in cols:
            self.conn.execute("ALTER TABLE decisions ADD COLUMN dual_p_yes REAL")


    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- markets ----
    def upsert_market(self, m: Market) -> None:
        self.conn.execute(
            "INSERT INTO markets (market_id, platform, question, yes_price, volume_24h,"
            " spread, close_time, depth, group_key, theme, asof_ts, raw)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(market_id) DO UPDATE SET"
            " yes_price=excluded.yes_price, volume_24h=excluded.volume_24h,"
            " spread=excluded.spread, close_time=excluded.close_time, depth=excluded.depth,"
            " group_key=excluded.group_key, theme=excluded.theme, asof_ts=excluded.asof_ts,"
            " raw=excluded.raw",
            (m.market_id, m.platform.value, m.question, m.yes_price, m.volume_24h,
             m.spread, m.close_time.isoformat() if m.close_time else None, m.depth,
             m.group_key, m.theme, m.asof_ts.isoformat(), json.dumps(m.raw, default=str)),
        )
        self.conn.commit()

    # ---- price points ----
    def insert_price_points(self, points: list[PricePoint]) -> None:
        self.conn.executemany(
            "INSERT INTO price_points (market_id, ts, price, volume, spread, asof_ts)"
            " VALUES (?,?,?,?,?,?)",
            [(p.market_id, p.ts.isoformat(), p.price, p.volume, p.spread,
              p.asof_ts.isoformat()) for p in points],
        )
        self.conn.commit()

    def get_price_series(self, market_id: str) -> list[PricePoint]:
        rows = self.conn.execute(
            "SELECT market_id, ts, price, volume, spread, asof_ts FROM price_points"
            " WHERE market_id=? ORDER BY ts",
            (market_id,),
        ).fetchall()
        return [PricePoint(market_id=r["market_id"], ts=datetime.fromisoformat(r["ts"]),
                           price=r["price"], volume=r["volume"], spread=r["spread"],
                           asof_ts=datetime.fromisoformat(r["asof_ts"])) for r in rows]

    # ---- decisions ----
    def insert_decision(self, d: Decision) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO decisions (decision_id, market_id, tier, theme, dominant_signal,"
            " router_score, snapshot_hash,"
            " samples, aggregate, debate, direction, p_yes, confidence,"
            " confidence_recalibrated, recalib_coef, abstain, abstain_reason, thesis,"
            " key_risks, invalidated_if, model, lessons_injected, dual_model, dual_p_yes,"
            " asof_ts)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d.decision_id, d.market_id, int(d.tier.value), d.theme, d.dominant_signal,
             d.router_score, d.snapshot_hash,
             json.dumps([s.model_dump() for s in d.samples], default=str),
             d.aggregate,
             json.dumps(d.debate.model_dump(), default=str) if d.debate else None,
             d.direction.value, d.p_yes, d.confidence, d.confidence_recalibrated,
             d.recalib_coef, int(d.abstain), d.abstain_reason, d.thesis,
             json.dumps(d.key_risks), d.invalidated_if, d.model,
             json.dumps(d.lessons_injected), d.dual_model, d.dual_p_yes,
             d.asof_ts.isoformat()),
        )
        self.conn.commit()

    def get_decisions(self) -> list[Decision]:
        rows = self.conn.execute("SELECT * FROM decisions ORDER BY asof_ts").fetchall()
        out: list[Decision] = []
        for r in rows:
            out.append(Decision(
                decision_id=r["decision_id"], market_id=r["market_id"],
                tier=r["tier"], theme=r["theme"], dominant_signal=r["dominant_signal"],
                router_score=r["router_score"], snapshot_hash=r["snapshot_hash"],
                samples=[SampleOutput(**s) for s in json.loads(r["samples"])],
                aggregate=r["aggregate"],
                debate=json.loads(r["debate"]) if r["debate"] else None,
                direction=r["direction"], p_yes=r["p_yes"], confidence=r["confidence"],
                confidence_recalibrated=r["confidence_recalibrated"],
                recalib_coef=r["recalib_coef"], abstain=bool(r["abstain"]),
                abstain_reason=r["abstain_reason"], thesis=r["thesis"],
                key_risks=json.loads(r["key_risks"]), invalidated_if=r["invalidated_if"],
                model=r["model"], lessons_injected=json.loads(r["lessons_injected"]),
                dual_model=r["dual_model"], dual_p_yes=r["dual_p_yes"],
                asof_ts=datetime.fromisoformat(r["asof_ts"]),
            ))
        return out

    # ---- ground truth ----
    def upsert_gt(self, gt: GroundTruth) -> None:
        self.conn.execute(
            "INSERT INTO ground_truth (market_id, platform, outcome, resolved_at, source)"
            " VALUES (?,?,?,?,?) ON CONFLICT(market_id) DO UPDATE SET"
            " outcome=excluded.outcome, resolved_at=excluded.resolved_at, source=excluded.source",
            (gt.market_id, gt.platform.value, gt.outcome, gt.resolved_at.isoformat(), gt.source),
        )
        self.conn.commit()

    def get_gt(self) -> dict[str, GroundTruth]:
        rows = self.conn.execute("SELECT * FROM ground_truth").fetchall()
        return {r["market_id"]: GroundTruth(
            market_id=r["market_id"], platform=r["platform"], outcome=r["outcome"],
            resolved_at=datetime.fromisoformat(r["resolved_at"]), source=r["source"],
        ) for r in rows}

    # ---- lessons ----
    def insert_lesson(self, lesson: Lesson) -> int:
        cur = self.conn.execute(
            "INSERT INTO lessons (theme, lesson, source_snapshot_hash, created_at)"
            " VALUES (?,?,?,?)",
            (lesson.theme, lesson.lesson, lesson.source_snapshot_hash,
             lesson.created_at.isoformat()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def recent_lessons(self, theme: str | None, cap: int) -> list[Lesson]:
        rows = self.conn.execute(
            "SELECT * FROM lessons WHERE theme=? ORDER BY id DESC LIMIT ?",
            (theme, cap),
        ).fetchall()
        return [Lesson(id=r["id"], theme=r["theme"], lesson=r["lesson"],
                       source_snapshot_hash=r["source_snapshot_hash"],
                       created_at=datetime.fromisoformat(r["created_at"])) for r in rows]

    # ---- calibration ----
    def upsert_calibration(self, group_key: str, n: int, mean_confidence: float,
                           accuracy: float, bias: float) -> None:
        # The ``coef`` column now stores the P(YES) recalibration bias (realized
        # frequency − mean prediction); kept for schema stability.
        self.conn.execute(
            "INSERT INTO calibrations (group_key, n, mean_confidence, accuracy, coef, updated_at)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT(group_key) DO UPDATE SET"
            " n=excluded.n, mean_confidence=excluded.mean_confidence,"
            " accuracy=excluded.accuracy, coef=excluded.coef, updated_at=excluded.updated_at",
            (group_key, n, mean_confidence, accuracy, bias, utcnow().isoformat()),
        )
        self.conn.commit()

    def get_calibration(self, group_key: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM calibrations WHERE group_key=?", (group_key,)).fetchone()
        return dict(r) if r else None

    def all_calibrations(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM calibrations").fetchall()]

    # ---- ops log (observability) ----
    def log_ops(self, kind: str, detail: str) -> None:
        self.conn.execute("INSERT INTO ops_log (ts, kind, detail) VALUES (?,?,?)",
                          (utcnow().isoformat(), kind, detail))
        self.conn.commit()

    def ops_summary(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT kind, COUNT(*) AS n FROM ops_log GROUP BY kind").fetchall()
        return {r["kind"]: int(r["n"]) for r in rows}

    # ---- meta ----
    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO meta (key, value) VALUES (?,?)"
                          " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    # ---- snapshots (point-in-time input capture for verify) ----
    def save_snapshot(self, snapshot_hash: str, snapshot_json: str) -> None:
        self.conn.execute("INSERT INTO snapshots (snapshot_hash, snapshot) VALUES (?,?)"
                          " ON CONFLICT(snapshot_hash) DO UPDATE SET snapshot=excluded.snapshot",
                          (snapshot_hash, snapshot_json))
        self.conn.commit()

    def get_snapshot(self, snapshot_hash: str) -> str | None:
        r = self.conn.execute("SELECT snapshot FROM snapshots WHERE snapshot_hash=?",
                              (snapshot_hash,)).fetchone()
        return r["snapshot"] if r else None

    # ---- evidence (v2 research agent; 铁律 4 persistence) ----
    def insert_evidence(self, e: Evidence) -> None:
        self.conn.execute(
            "INSERT INTO evidence (market_id, query, quote, source, published_at,"
            " asof_ts, freshness) VALUES (?,?,?,?,?,?,?)",
            (e.market_id, getattr(e, "query", None), e.quote, e.source,
             e.published_at.isoformat() if e.published_at else None,
             e.asof_ts.isoformat(), e.freshness.value),
        )
        self.conn.commit()

    def evidence_count(self, market_id: str) -> int:
        r = self.conn.execute("SELECT COUNT(*) AS n FROM evidence WHERE market_id=?",
                              (market_id,)).fetchone()
        return int(r["n"])

    def history_analogies(self, theme: str | None, asof_ts: datetime,
                          cap: int) -> list[dict[str, Any]]:
        """Local history library: past resolved markets of the same theme, with
        their price path and outcome, strictly point-in-time (asof <= target)."""
        if not theme:
            return []
        rows = self.conn.execute(
            "SELECT m.market_id, m.question, m.theme, m.asof_ts,"
            "       g.outcome, g.resolved_at"
            "  FROM markets m JOIN ground_truth g ON m.market_id = g.market_id"
            " WHERE m.theme = ? AND m.asof_ts <= ?"
            " ORDER BY m.asof_ts DESC LIMIT ?",
            (theme, asof_ts.isoformat(), cap),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            series = self.get_price_series(r["market_id"])
            path = [p.price for p in series[-10:]] if series else []
            out.append({
                "market_id": r["market_id"],
                "question": r["question"],
                "theme": r["theme"],
                "outcome": r["outcome"],
                "resolved_at": r["resolved_at"],
                "price_path": path,
            })
        return out
