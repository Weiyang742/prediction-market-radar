# Layered ablation (R13)

> regenerated 2026-09-15 with a **real DeepSeek LLM** (`deepseek-chat`, the key in
> this environment) against the Kalshi demo sandbox (`demo-api.kalshi.co`).
>
> command: `python main.py backfill --db ab.db --force-tier {0,1,2} --cutoff-hours 72
> --sample 20 --research-enabled false` (three runs into one DB, then `calibrate`).
>
> data source: Kalshi demo sandbox, settled binary markets — sandbox wire format,
> demo liquidity (volume=0). **Not production data.**

## result (T-72h, n=20 per tier, same market batch, fixed seed)

| tier | decisions | Brier | aggregation |
|------|-----------|-------|-------------|
| T0 rule | 20 | 0.1752 | sod |
| T1 LLM  | 20 | 0.0413 | sod |
| T2 LLM  | 20 | 0.0576 | sod |

- overall Brier: 0.0914 (60 decisions with resolution)
- abstain rate: 0.367 (learned selective threshold scanned post-hoc: 72.0)

## honest reading (this run)

- **Do not read "T1/T2 ≪ T0" as "CGAD depth works".** The gap is a sandbox
  artifact: T0 (rule fallback) scores the *last mid price* at the cutoff, and in
  these demo markets bid/ask sit at 0/1 so the mid is stuck at 0.5 while the true
  outcome is 0 — that inflates T0's Brier toward the 0.25 ignorance baseline.
- The LLM tiers output lower P(YES) by reading the question text (long-shot YES
  markets that resolve NO), not by deeper multi-agent reasoning. **T2 shows no
  gain over T1** — T2 (0.0576) is slightly *worse* than T1 (0.0413), consistent
  with the earlier 2026-09-09 zero-result where T0/T1/T2 had no separation once
  convergence-zone leakage was removed.
- The reliability chart for this batch is degenerate: every confidence bucket has
  `actual = 0.00` because nearly all sampled settled markets resolved NO. It
  validates the pipeline, not calibration.
- T-168h was not re-run in this pass (time). The 2026-09-09 two-cutoff table
  remains the reference for the second horizon. A real architecture comparison
  still requires production data — Polymarket is SNI-blocked and Kalshi
  production is unreachable from this network without an overseas proxy.

## caveats (honest reading)

- Sandbox data: demo markets often have no real trading, so bid/ask can sit at
  0/1 with mid stuck at 0.5 while the true outcome is 0 or 1; that inflates Brier
  toward the 0.25 ignorance baseline for those markets.
- Sample size: per-tier N=20 is still noise; treat these numbers as pipeline
  validation, not a real architecture comparison.
- rule-only mode scores the *last mid price* at the cutoff, not CGAD depth; a
  real architecture comparison requires the LLM mode + production data.
- Aggregation axis (SOD vs trimmed) is a *reserved* table column: fill the
  two-column comparison once production data is available.
