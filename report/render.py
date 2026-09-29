"""Markdown rendering for scan, run, history, calibrate (V3.1 §6.4)."""
from __future__ import annotations

from typing import Any, Sequence

from data.config import Config
from data.models import Decision, Market, PricePoint, Signal, utcnow


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(str(h) for h in headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def render_scan(markets: Sequence[Market], signals_by_id: dict[str, list[Signal]],
                wash_flags: dict[str, list[str]], cfg: Config) -> str:
    now = utcnow().isoformat()
    rows: list[Sequence[Any]] = []
    for m in markets:
        sigs = signals_by_id.get(m.market_id, [])
        sig_str = ", ".join(f"{s.type.value}:{s.severity.value}" for s in sigs) or "-"
        wash = "; ".join(wash_flags.get(m.market_id, [])) or "-"
        rows.append([m.market_id, m.platform.value, m.theme or "-", m.question[:60],
                     f"{m.yes_price:.2f}", f"{m.volume_24h:,.0f}", f"{m.spread * 100:.1f}pp",
                     sig_str, wash])
    body = _table(["id", "platform", "theme", "question", "p_yes", "vol24h",
                   "spread", "signals", "wash"], rows)
    return f"# scan {now}\n\n{body}\n\n_Note: scanner thresholds are uncalibrated defaults (V3.1 §6.2)._\n"


def render_report(decisions: Sequence[Decision], ops: dict[str, int],
                  cfg: Config, mode: str) -> str:
    now = utcnow().isoformat()
    rows: list[Sequence[Any]] = []
    for d in decisions:
        rows.append([d.market_id, d.theme or "-", f"T{d.tier.value}",
                     d.direction.value, f"{d.p_yes:.3f}", f"{d.confidence:.1f}",
                     f"{d.recalib_coef:+.3f}" if d.recalib_coef is not None else "-",
                     "YES" if d.abstain else "-", d.model])
    table = _table(["market", "theme", "tier", "dir", "p_yes", "conf", "bias",
                    "abstain", "model"], rows)
    lines = [
        f"# daily report {now}",
        "",
        f"mode: {mode}",
        "",
        table,
        "",
        "## observability",
        "",
        f"- degraded: {ops.get('degraded', 0)}",
        f"- http_429_retries: {ops.get('http_429', 0)}",
        f"- llm_fallback: {ops.get('llm_fallback', 0)}",
        f"- cost_usd: {ops.get('cost_usd', 0)}",
        "",
        "## limits",
        "",
        f"- daily decision cap: {cfg.daily_decision_cap}",
        f"- daily cost cap: ${cfg.daily_cost_cap_usd}",
    ]
    return "\n".join(lines) + "\n"


def render_history(market_id: str, series: Sequence[PricePoint]) -> str:
    if not series:
        return f"# history {market_id}\n\nno price points\n"
    prices = [p.price for p in series]
    lo, hi = min(prices), max(prices)
    span = (hi - lo) or 1e-9
    width = 40
    lines = [f"# history {market_id}", f"range {lo:.3f} .. {hi:.3f}", ""]
    for p in series:
        bar = "#" * max(1, int((p.price - lo) / span * width))
        lines.append(f"{p.ts.isoformat()}  {p.price:.4f}  {bar}")
    return "\n".join(lines) + "\n"


def render_calibration(pairs: list[tuple[Decision, float]], groups: dict[str, dict[str, Any]],
                       coefs: dict[str, float], threshold: float | None,
                       overall_brier: float, tier_brier: dict[int, float],
                       abstain_rate: float, reliability: list[dict] | None = None,
                       lesson_dash: dict | None = None) -> str:
    now = utcnow().isoformat()
    lines = [f"# calibration {now}", "", "## overall", "", f"- decisions with resolution: {len(pairs)}",
             f"- Brier: {overall_brier:.4f}", f"- abstain rate: {abstain_rate:.3f}",
             f"- learned selective threshold: {threshold if threshold is not None else 'n/a (not enough data)'}",
             "", "## per-tier Brier", ""]
    trows = [[f"T{t}", f"{b:.4f}"] for t, b in sorted(tier_brier.items())]
    lines.append(_table(["tier", "brier"], trows))
    lines += ["", "## recalibration groups", ""]
    grows: list[Sequence[Any]] = []
    for key, s in groups.items():
        label = key.replace("|", "/")
        grows.append([label, s["n"], f"{s['mean_confidence']:.1f}", f"{s['accuracy']:.3f}",
                      f"{coefs.get(key):+.3f}" if key in coefs else "n/a (<min samples)"])
    lines.append(_table(["group", "n", "mean_conf", "accuracy", "bias"], grows))
    if reliability is not None or lesson_dash is not None:
        lines += ["", *_reliability_section(reliability, len(pairs)),
                  *_lesson_section(lesson_dash, len(pairs))]
    return "\n".join(lines) + "\n"


def _bar(value: float, width: int = 20) -> str:
    v = max(0.0, min(1.0, value))
    filled = int(round(v * width))
    return "#" * filled + "." * (width - filled)


def _reliability_section(buckets: list[dict] | None, total: int) -> list[str]:
    if not buckets:
        return ["## reliability", "",
                f"数据不足 (<30 resolved samples; have {total}) — no reliability numbers", ""]
    lines = ["## reliability (confidence vs actual frequency)", "",
             "diagonal = perfect calibration (pred == actual)", ""]
    for b in buckets:
        pred = b["pred_confidence"] / 100.0
        act = b["actual_frequency"]
        lines.append(f"  conf {b['bucket']:>7} n={b['n']:<4} "
                     f"pred {pred:.2f} {_bar(pred)}  "
                     f"actual {act:.2f} {_bar(act)}  dev {b['deviation']:+.3f}")
    lines += ["", "| bucket | n | pred conf | actual freq | deviation |",
              "|--------|---|-----------|-------------|-----------|"]
    for b in buckets:
        lines.append(f"| {b['bucket']} | {b['n']} | {b['pred_confidence']:.1f} | "
                     f"{b['actual_frequency']:.3f} | {b['deviation']:+.3f} |")
    return lines + [""]


def _lesson_section(lesson_dash: dict | None, total: int) -> list[str]:
    lines = ["## memory-injection dashboard", ""]
    if lesson_dash is None:
        return lines + [f"数据不足 (<30 resolved samples; have {total}) — Pattern B→C decision data", ""]
    if lesson_dash.get("note"):
        return lines + [lesson_dash["note"], ""]
    lines += [
        f"- injected decisions: {lesson_dash['injected_n']}",
        f"- control (no lesson) decisions: {lesson_dash['control_n']}",
        f"- lesson hit rate (injected-group accuracy): {lesson_dash['hit_rate']:.3f}",
        f"- injected Brier: {lesson_dash['injected_brier']:.4f}",
        f"- control Brier: {lesson_dash['control_brier']:.4f}",
        f"- net effect (injected − control): {lesson_dash['net_effect']:+.4f} "
        "(negative = lessons helped)",
        "",
    ]
    return lines


def render_reliability_md(buckets: list[dict] | None, lesson_dash: dict | None,
                          total: int) -> str:
    """Full reliability + memory-injection dashboard artifact for examples/."""
    lines = ["# reliability + memory-injection dashboard", "",
             f"generated by `python main.py calibrate` on {utcnow().isoformat()}",
             f"- resolved decisions: {total}", ""]
    lines += _reliability_section(buckets, total)
    lines += _lesson_section(lesson_dash, total)
    return "\n".join(lines).rstrip("\n") + "\n"
