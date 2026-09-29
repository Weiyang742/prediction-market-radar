"""CLI entry point: scan / run / history / calibrate / verify.

Usage:
  python main.py run --mock
  python main.py scan --mock
  python main.py history <market_id> --mock
  python main.py calibrate --mock
  python main.py verify <decision_id> --db data.db
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from dataclasses import dataclass, fields
from datetime import timedelta
from pathlib import Path
from typing import Any

from data.config import Config
from data.kalshi_client import KalshiClient
from data.ledger import Ledger, ledger_resolved_pairs
from data.mock_data import mock_markets, mock_series, seed_mock_resolved
from data.models import (GroundTruth, Market, Platform, PricePoint, Severity, Signal,
                         Tier, snapshot_hash, utcnow)
from data.news import make_news_client
from data.polymarket_client import PolymarketClient
from data.store import Store
from analysis import divergence, scanner, signals, themes
from agents import calibrate as cal
from agents import judge, reflector, research, router
from agents.llm import LLMError, make_llm, make_second_llm
from agents.prompts import PAIR_SCHEMA, pair_prompt
from report import render

log = logging.getLogger(__name__)

_SEVERITY_ORDER = {Severity.LOW: 0, Severity.MED: 1, Severity.HIGH: 2}


@dataclass
class FetchResult:
    pm: list[Market]
    ks: list[Market]
    notes: list[str]
    counters: dict[str, int]


@dataclass
class MarketContext:
    """Everything the scan/run pipelines derive from the two platform fetches."""
    markets: list[Market]
    series: dict[str, list[PricePoint]]
    div_pp: dict[str, float]
    div_sig: dict[str, Signal]
    counterpart: dict[str, float]
    group_dev: dict[str, float]
    signals_by_id: dict[str, list[Signal]]
    selected: list[Market]
    wash: dict[str, list[str]]


def _bool_arg(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _field_type(f: Any) -> Any:
    t = str(f.type)
    if "bool" in t:
        return _bool_arg
    if "int" in t:
        return int
    if "float" in t:
        return float
    return str


def _add_config_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mock", action="store_true", default=None,
                        help="deterministic synthetic data, no network / no LLM key")
    parser.add_argument("--db", dest="db_path", default=None, help="sqlite db path")
    parser.add_argument("--verbose", action="store_true", default=False)
    for f in fields(Config):
        if f.name in ("mock", "db_path", "llm_api_key", "search_api_key",
                      "second_model_api_key"):
            continue
        parser.add_argument(f"--{f.name.replace('_', '-')}", dest=f.name,
                            default=None, type=_field_type(f),
                            help="uncalibrated default; see README assumptions table")


def _build_config(args: argparse.Namespace) -> Config:
    cfg = Config.from_env()
    overrides = {f.name: getattr(args, f.name) for f in fields(Config)
                 if getattr(args, f.name, None) is not None}
    if args.db_path is not None:
        overrides["db_path"] = args.db_path
    if args.mock is not None:
        overrides["mock"] = args.mock
    cfg.apply_overrides(overrides)
    cfg.validate()
    return cfg


def _fetch_platforms(cfg: Config) -> FetchResult:
    """Fetch both platforms.  Per-platform failures are logged and reported as
    degraded notes (no silent absorption)."""
    if cfg.mock:
        markets = mock_markets(cfg)
        return FetchResult(
            pm=[m for m in markets if m.platform is Platform.POLYMARKET],
            ks=[m for m in markets if m.platform is Platform.KALSHI],
            notes=[], counters={"http_429": 0, "retries": 0},
        )
    notes: list[str] = []
    pm: list[Market] = []
    ks: list[Market] = []
    counters = {"http_429": 0, "retries": 0}
    try:
        with PolymarketClient(cfg) as client:
            pm = client.fetch_markets(limit=200)
            counters["http_429"] += client.http_429_count
            counters["retries"] += client.retry_count
    except Exception as exc:  # surfaced in the report, never swallowed
        log.exception("polymarket fetch failed")
        notes.append(f"polymarket: {exc}")
    try:
        with KalshiClient(cfg) as client:
            ks = client.fetch_markets(limit=200)
            counters["http_429"] += client.http_429_count
            counters["retries"] += client.retry_count
    except Exception as exc:
        log.exception("kalshi fetch failed")
        notes.append(f"kalshi: {exc}")
    return FetchResult(pm=pm, ks=ks, notes=notes, counters=counters)


def _build_series(markets: list[Market], cfg: Config, notes: list[str]) -> dict[str, list[PricePoint]]:
    if cfg.mock:
        return {m.market_id: mock_series(m, cfg) for m in markets}
    # Real mode: fetch per-market candles so the sequence-based rules
    # (jump/acceleration/volume/spread) actually run live.  Kalshi uses the
    # event's series_ticker; Polymarket resolves a clobTokenId from the gamma
    # single-market endpoint (UNVERIFIED — host SNI-blocked here).  Failures
    # degrade per-market with a note, never crash the run.
    series: dict[str, list[PricePoint]] = {}
    ks_markets = [m for m in markets
                  if m.platform is Platform.KALSHI and m.series_ticker]
    if ks_markets:
        try:
            with KalshiClient(cfg) as client:
                for m in ks_markets:
                    try:
                        end = int(m.asof_ts.timestamp())
                        pts = client.fetch_candles(m.series_ticker, m.market_id,
                                                   end - 14 * 86400, end)
                        if len(pts) >= 2:
                            series[m.market_id] = pts
                    except Exception as exc:  # per-market degradation, never fatal
                        log.warning("series fetch failed %s: %s", m.market_id, exc)
        except Exception as exc:
            notes.append(f"kalshi series fetch failed: {exc}")
    pm_markets = [m for m in markets if m.platform is Platform.POLYMARKET]
    if pm_markets:
        try:
            with PolymarketClient(cfg) as client:
                for m in pm_markets:
                    try:
                        token = client.resolve_clob_token_id(m.market_id)
                        if not token:
                            continue
                        pts = client.fetch_candles(token)
                        if len(pts) >= 2:
                            series[m.market_id] = pts
                    except Exception as exc:  # per-market degradation, never fatal
                        log.warning("series fetch failed %s: %s", m.market_id, exc)
        except Exception as exc:
            notes.append(f"polymarket series fetch failed: {exc}")
    if not series:
        notes.append("no real price series fetched (series-based signals unavailable)")
    return series


def _confirmer(cfg: Config, llm: Any):
    if cfg.mock:
        return lambda q1, q2: themes.similarity(q1, q2) >= 0.25
    if not llm.is_available():
        return lambda q1, q2: False
    cache: dict[tuple[str, str], bool] = {}

    def confirm(q1: str, q2: str) -> bool:
        key = (q1, q2)
        if key not in cache:
            sys_p, user = pair_prompt(q1, q2)
            cache[key] = bool(llm.complete(sys_p, user, schema=PAIR_SCHEMA)["same_event"])
        return cache[key]

    return confirm


def _build_pairs(pm: list[Market], ks: list[Market], cfg: Config,
                 llm: Any) -> tuple[dict[str, float], dict[str, Signal], dict[str, float]]:
    pairs = divergence.find_pairs(pm, ks, cfg, _confirmer(cfg, llm))
    div_pp: dict[str, float] = {}
    div_sig: dict[str, Signal] = {}
    counterpart: dict[str, float] = {}
    for pmm, ksm, _score in pairs:
        pp = abs(pmm.yes_price - ksm.yes_price) * 100.0
        div_pp[pmm.market_id] = pp
        div_pp[ksm.market_id] = pp
        counterpart[pmm.market_id] = ksm.yes_price
        counterpart[ksm.market_id] = pmm.yes_price
        s = divergence.cross_platform_divergence(pmm.yes_price, ksm.yes_price, cfg)
        if s is not None:
            div_sig[pmm.market_id] = s
            div_sig[ksm.market_id] = s
    return div_pp, div_sig, counterpart


def _group_dev(markets: list[Market]) -> dict[str, float]:
    by_group: dict[str, list[float]] = {}
    for m in markets:
        if m.group_key:
            by_group.setdefault(m.group_key, []).append(m.yes_price)
    out: dict[str, float] = {}
    for group, prices in by_group.items():
        if len(prices) >= 3:  # 2-market groups are handled as negation pairs
            d = divergence.group_sum_deviation(prices)
            for m in markets:
                if m.group_key == group:
                    out[m.market_id] = d
    return out


def _negation_signals(markets: list[Market], cfg: Config) -> dict[str, Signal]:
    """Emit a NEGATION_PAIR_DEVIATION signal for two-market groups whose
    probabilities deviate from a complement (|P(E)+P(¬E)-1| > threshold)."""
    by_group: dict[str, list[Market]] = {}
    for m in markets:
        if m.group_key:
            by_group.setdefault(m.group_key, []).append(m)
    out: dict[str, Signal] = {}
    for group, ms in by_group.items():
        if len(ms) != 2:
            continue
        sig = divergence.negation_pair_deviation(ms[0].yes_price, ms[1].yes_price, cfg)
        if sig is not None:
            for m in ms:
                out[m.market_id] = sig
    return out


def _collect_signals(markets: list[Market], series: dict[str, list[PricePoint]],
                     div_sig: dict[str, Signal], neg_sig: dict[str, Signal],
                     cfg: Config) -> dict[str, list[Signal]]:
    out: dict[str, list[Signal]] = {}
    for m in markets:
        sigs = signals.compute_local_signals(m, series.get(m.market_id, []), cfg)
        ds = div_sig.get(m.market_id)
        if ds is not None:
            sigs.append(ds)
        ns = neg_sig.get(m.market_id)
        if ns is not None:
            sigs.append(ns)
        out[m.market_id] = sigs
    return out


def collect_market_context(cfg: Config, llm: Any) -> tuple[FetchResult, MarketContext | None]:
    """Fetch + classify + signal + filter.  Returns (fetch, None) on zero markets."""
    fr = _fetch_platforms(cfg)
    markets = fr.pm + fr.ks
    if not markets:
        return fr, None
    for m in markets:
        m.theme = themes.classify_theme(m.question)
    series = _build_series(markets, cfg, fr.notes)
    div_pp, div_sig, counterpart = _build_pairs(fr.pm, fr.ks, cfg, llm)
    group_dev = _group_dev(markets)
    neg_sig = _negation_signals(markets, cfg)
    signals_by_id = _collect_signals(markets, series, div_sig, neg_sig, cfg)
    selected, wash = scanner.scan(markets, series, cfg)
    return fr, MarketContext(
        markets=markets, series=series, div_pp=div_pp, div_sig=div_sig,
        counterpart=counterpart, group_dev=group_dev, signals_by_id=signals_by_id,
        selected=selected, wash=wash,
    )


def _dominant_signal(sigs: list[Signal]) -> str | None:
    if not sigs:
        return None
    return max(sigs, key=lambda s: _SEVERITY_ORDER[s.severity]).type.value


def _build_snapshot(m: Market, sigs: list[Signal], div_pp: float | None,
                    group_dev: float | None, counterpart: float | None,
                    research_block: dict[str, Any] | None = None) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "market": {
            "market_id": m.market_id,
            "question": m.question,
            "yes_price": m.yes_price,
            "volume_24h": m.volume_24h,
            "spread": m.spread,
            "close_time": m.close_time.isoformat() if m.close_time else None,
            "asof_ts": m.asof_ts.isoformat(),
        },
        "signals": [{"type": s.type.value, "severity": s.severity.value, "detail": s.detail}
                    for s in sigs],
        "cross_platform": ({"kalshi_same_topic_price": counterpart, "divergence_pp": div_pp}
                           if div_pp is not None else None),
        "related_group": {"sibling_markets": [], "group_sum_deviation": group_dev},
    }
    if research_block is not None:
        snap["research"] = research_block
    snap["snapshot_hash"] = snapshot_hash(snap)
    return snap


def cmd_scan(args: argparse.Namespace, cfg: Config) -> int:
    llm = make_llm(cfg)
    try:
        fr, ctx = collect_market_context(cfg, llm)
    finally:
        llm.close()
    if ctx is None:
        reason = "; ".join(fr.notes) or "no markets returned by either platform"
        print(f"ERROR: 0 markets scanned. reason: {reason}", file=sys.stderr)
        return 2
    print(render.render_scan(ctx.selected, ctx.signals_by_id, ctx.wash, cfg))
    return 0


def cmd_run(args: argparse.Namespace, cfg: Config) -> int:
    store = Store(cfg.db_path)
    llm = make_llm(cfg)
    second_llm = make_second_llm(cfg)
    news = make_news_client(cfg)
    try:
        return _run_pipeline(store, cfg, llm, second_llm, news)
    finally:
        news.close()
        if second_llm is not None:
            second_llm.close()
        llm.close()


def _run_pipeline(store: Store, cfg: Config, llm: Any,
                  second_llm: Any | None = None,
                  news: Any | None = None) -> int:
    fr, ctx = collect_market_context(cfg, llm)
    if fr.counters["http_429"]:
        store.log_ops("http_429", str(fr.counters["http_429"]))
    if fr.counters["retries"]:
        store.log_ops("degraded", f"http retries: {fr.counters['retries']}")
    if ctx is None:
        reason = "; ".join(fr.notes) or "no markets returned by either platform"
        store.log_ops("degraded", f"0 markets: {reason}")
        print(f"ERROR: 0 markets. reason: {reason}", file=sys.stderr)
        return 2

    for m in ctx.markets:
        store.upsert_market(m)
    for mid, pts in ctx.series.items():
        store.insert_price_points(pts)

    decisions: list[Any] = []
    cost = 0.0
    streak = 0
    for m in ctx.selected:
        if len(decisions) >= cfg.daily_decision_cap:
            store.log_ops("degraded", "daily_decision_cap reached")
            break
        if cost >= cfg.daily_cost_cap_usd:
            store.log_ops("degraded", "daily_cost_cap reached")
            break
        sigs = ctx.signals_by_id.get(m.market_id, [])
        dom = _dominant_signal(sigs)
        r = router.route(sigs, cfg, ctx.div_pp.get(m.market_id), ctx.group_dev.get(m.market_id))
        research_block = None
        if r.tier is Tier.T2 and cfg.research_enabled and news is not None:
            try:
                result = research.run_research_agent(m, cfg, llm, news, store)
                research_block = research.research_snapshot_block(result)
            except Exception as exc:  # research failure logged, never crashes the run
                log.warning("research failed for %s: %s", m.market_id, exc)
                store.log_ops("degraded", f"research failed: {exc}")
        snap = _build_snapshot(m, sigs, ctx.div_pp.get(m.market_id),
                               ctx.group_dev.get(m.market_id), ctx.counterpart.get(m.market_id),
                               research_block)
        store.save_snapshot(snap["snapshot_hash"], json.dumps(snap, default=str))
        if r.tier is Tier.T0:
            d = judge.rule_fallback(m, r.tier, m.theme, dom, r.score,
                                    snap["snapshot_hash"], model="rule_tier0",
                                    thesis="tier0 rule label")
            store.insert_decision(d)
            decisions.append(d)
            continue
        cost += cfg.tier2_cost_usd if r.tier is Tier.T2 else cfg.tier1_cost_usd
        try:
            d = judge.run_judge(snap, r.tier, r.score, m.theme, dom, cfg, llm, store,
                                second_llm)
            decisions.append(d)
            streak = 0
        except LLMError as exc:
            log.warning("LLM failure for %s: %s", m.market_id, exc)
            streak += 1
            if streak >= cfg.llm_max_failures:
                store.log_ops("llm_fallback", str(exc))
                d = judge.rule_fallback(m, r.tier, m.theme, dom, r.score,
                                        snap["snapshot_hash"], model="rule_fallback")
                store.insert_decision(d)
                decisions.append(d)
                streak = 0
            else:
                store.log_ops("degraded", f"llm failure: {exc}")

    for note in fr.notes:
        store.log_ops("degraded", note)
    _write_ledger(cfg, ctx, decisions)
    ops = store.ops_summary()
    ops["cost_usd"] = round(cost, 4)
    mode = "mock" if cfg.mock else "live"
    print(render.render_report(decisions, ops, cfg, mode))
    return 0


def _write_ledger(cfg: Config, ctx: MarketContext, decisions: list[Any]) -> int:
    """Append pre-hoc predictions for adjudicated open markets to the forward
    prediction ledger (idempotent per market per as-of day).  Returns the number
    of entries appended this run."""
    if not decisions:
        return 0
    market_by_id = {m.market_id: m for m in ctx.markets}
    ledger = Ledger(cfg.ledger_path)
    source = "demo" if cfg.mock else cfg.ledger_source
    written = 0
    for d in decisions:
        m = market_by_id.get(d.market_id)
        if m is None:
            continue
        entry = ledger.append(
            market_id=d.market_id, question=m.question, p_yes=d.p_yes,
            confidence=d.confidence, tier=int(d.tier.value), theme=d.theme,
            asof_ts=d.asof_ts, model=d.model, source=source)
        if entry is not None:
            written += 1
    return written


def cmd_history(args: argparse.Namespace, cfg: Config) -> int:
    with Store(cfg.db_path) as store:
        series = store.get_price_series(args.market_id)
    print(render.render_history(args.market_id, series))
    return 0


def cmd_calibrate(args: argparse.Namespace, cfg: Config) -> int:
    store = Store(cfg.db_path)
    if cfg.mock:
        seed_mock_resolved(store, cfg)
    decisions = store.get_decisions()
    gt = store.get_gt()
    pairs = cal.decisions_with_outcomes(decisions, gt)
    if not pairs:
        print("ERROR: 0 resolved markets with decisions (GT module empty). "
              "Cannot compute Brier.", file=sys.stderr)
        return 2
    overall_brier = sum(cal.brier_score(d.p_yes, o) for d, o in pairs) / len(pairs)
    tier_brier: dict[int, float] = {}
    for t in (0, 1, 2):
        subset = [(d, o) for d, o in pairs if int(d.tier.value) == t]
        if subset:
            tier_brier[t] = sum(cal.brier_score(d.p_yes, o) for d, o in subset) / len(subset)
    groups = cal.compute_group_stats(pairs)
    coefs = cal.recalibrate_groups(store, pairs, cfg)
    threshold, _best_sb, _cov = cal.scan_selective_threshold(pairs, cfg)
    if threshold is not None:
        store.set_meta("selective_threshold", str(threshold))
    abstain_rate = sum(1.0 for d, _o in pairs if d.abstain) / len(pairs)

    # Reliability chart + memory-injection dashboard (Pattern B→C decision data).
    total = len(pairs)
    reliability = cal.reliability_buckets(pairs) if total >= 30 else None
    lesson_dash = cal.lesson_dashboard(pairs) if total >= 30 else None

    # Forward prediction ledger: pre-hoc Brier, kept separate from replay Brier.
    ledger = Ledger(cfg.ledger_path)
    entries = ledger.read()
    chain_ok, chain_msg = ledger.validate()
    ledger_pairs = ledger_resolved_pairs(entries, gt)
    ledger_brier = (sum(cal.brier_score(float(e["p_yes"]), o) for e, o in ledger_pairs)
                    / len(ledger_pairs)) if ledger_pairs else None

    out = render.render_calibration(pairs, groups, coefs, threshold,
                                    overall_brier, tier_brier, abstain_rate,
                                    reliability, lesson_dash)
    out += _ledger_section(chain_ok, chain_msg, ledger_pairs, ledger_brier)
    if cfg.mock:
        out = "# WARNING: MOCK DATA — NOT REAL ABLATION\n\n" + out
    print(out)
    reliability_md = render.render_reliability_md(reliability, lesson_dash, total)
    if cfg.mock:
        reliability_md = "# WARNING: MOCK DATA — NOT REAL ABLATION\n\n" + reliability_md
    Path("examples/reliability.md").write_text(reliability_md, encoding="utf-8")
    return 0


def _ledger_section(chain_ok: bool, chain_msg: str,
                    ledger_pairs: list[tuple[dict, float]],
                    ledger_brier: float | None) -> str:
    lines = ["", "## forward prediction ledger", "",
             f"- chain: {chain_msg}",
             f"- ledger predictions with resolution: {len(ledger_pairs)}"]
    if ledger_brier is None:
        lines.append("- pre-hoc Brier: n/a (no ledger records joined with resolved markets)")
    else:
        lines.append(f"- pre-hoc Brier: {ledger_brier:.4f} (separate from replay Brier)")
    return "\n".join(lines) + "\n"


def cmd_backfill(args: argparse.Namespace, cfg: Config) -> int:
    """Replay settled Kalshi markets and score real layered Brier (R13).

    For each settled binary market: fetch historical candles -> replay price
    series -> signals -> route -> adjudicate -> persist Decision + GroundTruth,
    then write examples/ablation.md from the scored SQLite records.
    """
    store = Store(cfg.db_path)
    llm = make_llm(cfg)
    second_llm = make_second_llm(cfg)
    news = make_news_client(cfg)
    try:
        return _backfill_pipeline(store, args, cfg, llm, second_llm, news)
    finally:
        news.close()
        if second_llm is not None:
            second_llm.close()
        llm.close()


def _backfill_pipeline(store: Store, args: argparse.Namespace, cfg: Config,
                       llm: Any, second_llm: Any | None = None,
                       news: Any | None = None) -> int:
    try:
        with KalshiClient(cfg) as client:
            settled = client.fetch_settled(limit=100)
    except Exception as exc:
        log.exception("settled fetch failed")
        print(f"ERROR: fetch settled markets failed: {exc}", file=sys.stderr)
        return 2
    settled = [s for s in settled if s["outcome"] in (0.0, 1.0)]
    if not settled:
        print("ERROR: 0 settled binary markets with 0/1 outcome.", file=sys.stderr)
        return 2

    # Phase 1: pull candles + time-truncate, keep only markets with >=2 points.
    valid: list[tuple[dict, list]] = []
    with KalshiClient(cfg) as client:
        for s in settled:
            try:
                end = int(s["resolved_at"].timestamp())
                series = client.fetch_candles(s["series_ticker"], s["market_id"],
                                              end - 14 * 86400, end)
            except Exception as exc:
                log.warning("candles failed %s: %s", s["market_id"], exc)
                continue
            # Time truncation: judge only sees data up to N hours before
            # resolution (pre-judgement, not convergence-zone leakage).
            if args.cutoff_hours:
                cutoff_ts = s["resolved_at"] - timedelta(hours=args.cutoff_hours)
                series = [p for p in series if p.ts <= cutoff_ts]
            if len(series) >= 2:
                valid.append((s, series))

    # Phase 2: sample from the *valid* pool (fixed seed, reproducible).
    if args.sample and len(valid) > args.sample:
        valid = random.Random(args.sample).sample(valid, args.sample)
    elif not args.sample:
        valid = valid[: args.max_markets]

    if not valid:
        print("ERROR: 0 markets with >=2 candles after cutoff.", file=sys.stderr)
        return 2

    # Phase 3: replay -> route -> adjudicate -> persist.
    for s, series in valid:
        market = Market(market_id=s["market_id"], platform=Platform.KALSHI,
                        question=s["question"], yes_price=series[-1].price,
                        volume_24h=0.0, spread=0.0, close_time=s["resolved_at"],
                        asof_ts=series[-1].ts)
        market.theme = themes.classify_theme(s["question"])
        store.upsert_market(market)  # persist for the local history library
        sigs = signals.compute_local_signals(market, series, cfg, now=series[-1].ts)
        r = router.route(sigs, cfg)
        if args.force_tier is not None:
            r.tier = Tier(args.force_tier)
        dom = _dominant_signal(sigs)
        research_block = None
        if (not args.rule_only and r.tier is Tier.T2
                and cfg.research_enabled and news is not None):
            try:
                result = research.run_research_agent(market, cfg, llm, news, store)
                research_block = research.research_snapshot_block(result)
            except Exception as exc:
                log.warning("research failed %s: %s", market.market_id, exc)
        snap = _build_snapshot(market, sigs, None, None, None, research_block)
        store.save_snapshot(snap["snapshot_hash"], json.dumps(snap, default=str))
        store.upsert_gt(GroundTruth(market_id=s["market_id"], platform=Platform.KALSHI,
                                    outcome=s["outcome"], resolved_at=s["resolved_at"],
                                    source=s["source"]))
        if r.tier is Tier.T0 or args.rule_only:
            d = judge.rule_fallback(market, r.tier, market.theme, dom, r.score,
                                    snap["snapshot_hash"], model="rule_tier0",
                                    thesis="backfill rule label")
        else:
            try:
                d = judge.run_judge(snap, r.tier, r.score, market.theme, dom,
                                    cfg, llm, store=None, second_llm=second_llm)
            except LLMError as exc:
                log.warning("LLM failure %s: %s", market.market_id, exc)
                d = judge.rule_fallback(market, r.tier, market.theme, dom, r.score,
                                        snap["snapshot_hash"], model="rule_fallback")
        store.insert_decision(d)
        # EF-Reflexion: store a lesson for future runs.  Adjudication here used
        # store=None, so lessons do NOT leak into this batch's other decisions.
        if not args.rule_only and d.model not in ("rule_tier0", "rule_fallback"):
            try:
                reflector.reflect(d, s["outcome"], snap, store, llm, cfg)
            except LLMError as exc:
                log.warning("reflect failed %s: %s", market.market_id, exc)
        print(f"  {s['market_id']} tier=T{r.tier.value} p_yes={d.p_yes:.3f} "
              f"outcome={s['outcome']:.0f}", file=sys.stderr)

    pairs = cal.decisions_with_outcomes(store.get_decisions(), store.get_gt())
    if not pairs:
        print("ERROR: 0 decisions matched ground truth.", file=sys.stderr)
        return 2
    overall = sum(cal.brier_score(d.p_yes, o) for d, o in pairs) / len(pairs)
    tier_rows: dict[int, tuple[int, float]] = {}
    for t in (0, 1, 2):
        sub = [(d, o) for d, o in pairs if int(d.tier.value) == t]
        if sub:
            tier_rows[t] = (len(sub), sum(cal.brier_score(d.p_yes, o) for d, o in sub) / len(sub))

    meta = [f"adjudication: {'rule-only' if args.rule_only else 'CGAD (Tier>=1 via LLM)'}",
            f"aggregation method: {cfg.aggregation_method} (SOD | trimmed, reserved ablation axis)"]
    if args.cutoff_hours:
        meta.append(f"time cutoff: T-{args.cutoff_hours}h (pre-resolution truncation)")
    if args.force_tier is not None:
        meta.append(f"forced tier: T{args.force_tier}")
    if args.sample:
        meta.append(f"sample: {args.sample} markets (fixed seed)")
    if not args.rule_only:
        meta.append(f"research agent: {'enabled (Tier 2)' if cfg.research_enabled else 'disabled'}")
    lines = [
        "# Layered ablation (R13)",
        "",
        f"> generated by `python main.py backfill --db {cfg.db_path}` on {utcnow().isoformat()}",
        "> data source: Kalshi demo sandbox (`demo-api.kalshi.co`), settled binary markets",
        "> NOTE: sandbox wire format, demo liquidity (volume=0). Not production data.",
        "> " + "; ".join(meta),
        "",
        "## result",
        "",
        f"- decisions with resolution: {len(pairs)}",
        f"- overall Brier: {overall:.4f}",
        "",
        "| tier | decisions | Brier | aggregation |",
        "|------|-----------|-------|-------------|",
    ]
    for t in (0, 1, 2):
        n, b = tier_rows.get(t, (0, 0.0))
        lines.append(f"| T{t} | {n} | {b:.4f} | {cfg.aggregation_method} |")
    lines += [
        "",
        "## caveats (honest reading)",
        "",
        "- Sandbox data: demo markets often have no real trading, so bid/ask can sit",
        "  at 0/1 with mid stuck at 0.5 while the true outcome is 0 or 1; that inflates",
        "  Brier toward the 0.25 ignorance baseline for those markets.",
        "- Sample size: report the per-tier N; a Brier over <15 decisions is noise.",
        "- rule-only mode scores the *last mid price* at the cutoff, not CGAD depth;",
        "  a real architecture comparison requires the LLM mode + production data.",
        "- Aggregation axis (SOD vs trimmed) is a *reserved* table column: fill the",
        "  two-column comparison once production data is available (⑨).",
    ]
    text = "\n".join(lines) + "\n"
    Path("examples/ablation.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


def cmd_verify(args: argparse.Namespace, cfg: Config) -> int:
    with Store(cfg.db_path) as store:
        target = next((d for d in store.get_decisions() if d.decision_id == args.decision_id), None)
        if target is None:
            print(f"ERROR: no decision with id {args.decision_id}", file=sys.stderr)
            return 2
        snap_json = store.get_snapshot(target.snapshot_hash)
    print(f"decision_id: {target.decision_id}")
    print(f"market_id:   {target.market_id}")
    print(f"tier:        T{target.tier.value}")
    print(f"snapshot_hash: {target.snapshot_hash}")
    print("input snapshot:")
    print(snap_json if snap_json else "(snapshot not found in store)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="main.py",
                                     description="prediction-market-radar CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("scan", cmd_scan), ("run", cmd_run),
                          ("history", cmd_history), ("calibrate", cmd_calibrate),
                          ("backfill", cmd_backfill), ("verify", cmd_verify)):
        p = sub.add_parser(name)
        _add_config_flags(p)
        p.set_defaults(handler=handler)
    sub.choices["history"].add_argument("market_id")
    sub.choices["verify"].add_argument("decision_id")
    sub.choices["backfill"].add_argument("--max-markets", type=int, default=30,
                                         help="max settled markets to replay")
    sub.choices["backfill"].add_argument("--rule-only", action="store_true",
                                         help="use rule labels for every tier (no LLM)")
    sub.choices["backfill"].add_argument("--cutoff-hours", type=int, default=None,
                                         help="truncate price series to N hours before resolution")
    sub.choices["backfill"].add_argument("--force-tier", type=int, default=None,
                                         choices=[0, 1, 2],
                                         help="force every market through this tier")
    sub.choices["backfill"].add_argument("--sample", type=int, default=None,
                                         help="randomly sample N settled markets (fixed seed)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    try:
        cfg = _build_config(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return args.handler(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
