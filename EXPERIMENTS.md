# Experiment Log

A permanent, version-controlled record of real OOS experiments run
against this lab's actual data. This file exists because
`backend/data/historical_bars.db` — where the real `experiments`,
`oos_partitions`, `experiment_freeze_snapshots`, `oos_evaluations`, and
`oos_evaluation_signals` rows this log describes actually live — is
gitignored local data, not part of version control (see
`backend/.gitignore`: `backend/data/`). Without a written record here,
an experiment's hypothesis, IDs, and results would exist only on
whichever machine's local database happened to produce them. Newest
first, like `BUILD_LOG.md`.

---

## Experiment 1 — TSLA Downside-Momentum-on-Volume Continuation (2026-08-18)

**The lab's first real OOS experiment.** A scientific validation run
(OOS Evaluation v1, v0.1.31/v0.1.32) — not a strategy search: one
hypothesis, defined once from development data only, frozen, and
evaluated exactly once against a holdout period it never touched
before that evaluation.

### Hypothesis (frozen before any holdout contact)

> When TSLA exhibits a short-term price decline of at least 0.5% over
> the trailing 15 minutes **AND** relative volume is at least 1.5× its
> time-of-day historical baseline, the subsequent 15-minute forward
> return is lower than the unconditional baseline (continuation, not
> reversal).

| | |
|---|---|
| Conditions (ANDed) | `price.return_15m <= -0.005`, `volume.relative_volume >= 1.5` |
| Outcome | `forward_return`, horizon 15m, success `<= 0.0` |
| Entry semantics | next-bar-open (Backtesting v1, unmodified) |
| Feature contract version | `v1` |
| Threshold selection | round, pre-specified, economically-interpretable values chosen from domain convention *before* inspecting this dataset's own return/volume distribution — not fit, not percentile-derived, not searched |

### Data

Real TSLA 5-minute bars from Alpaca (IEX feed), freshly fetched for
this run via `scripts/backfill_historical_data.py --symbols TSLA
--timeframes 5m --start 2026-06-01 --end 2026-08-15 --provider alpaca`
— 4,387 real bars, no synthetic data anywhere in this experiment.

| | Range | Bars |
|---|---|---:|
| Development | `2026-06-01T00:00:00Z` .. `2026-07-31T23:59:59.999999Z` | 3,577 |
| Holdout | `2026-08-01T00:00:00Z` .. `2026-08-14T23:59:59.999999Z` | 810 |

### Development result (development data only, before freeze)

- **Research (signal-at-close):** 119 raw signals, 65 successful
  (54.6%), mean outcome −0.021%, median −0.061%, std dev 0.758%.
- **Backtest v1 (next-bar-open — the same semantics OOS uses):** 119
  signals, win rate 42.0%, mean return −0.044%, median −0.138%, std
  dev 0.815%, mean MFE +0.538%, mean MAE −0.564%.
- **Statistical Validation V1 (episode-level, non-overlapping):** 119
  raw signals → **63 independent episodes**. Conditioned mean −0.0301%
  vs. baseline mean −0.0297% → difference **−0.0004%**, 95% CI
  **[−0.209%, +0.209%]**, permutation p = **0.9961**, Cohen's d =
  **−0.0006 (negligible)**.
- **Statistical Validation V2 (dependence-aware baseline):** Method A
  p = 0.9329, Method B p = 0.9979, Cohen's d = −0.0110 (negligible),
  conclusion does not change materially between methods. Post-hoc
  power: minimum detectable effect at 80% power is d = 0.362; the
  observed effect (−0.011) is far below that.

**Development verdict at freeze time:** the raw numbers look mildly
directionally supportive, but the properly-adjusted (episode-level,
dependence-corrected) analysis shows no statistically distinguishable
effect. The hypothesis was frozen as-is, with this weak/null evidence
disclosed, rather than modified in search of a better development
result.

### Frozen provenance

| Field | Value |
|---|---|
| Experiment ID | `ceb67061-540d-4e26-bf1d-a89dcc6814ed` |
| Hypothesis hash | `bafb0ddb057c96d2833c46145d813bd7b256a752c844e774f6f4423633466801` |
| Frozen snapshot ID | `ceb67061-540d-4e26-bf1d-a89dcc6814ed` (== experiment_id, by design) |
| OOS partition ID | `c7319390f4d87644696a867c` |
| Frozen at | `2026-08-18T18:44:49.967072Z` |

### OOS result (`POST /experiments/{id}/oos-evaluate`, no request body)

| Field | Value |
|---|---|
| Evaluation ID | `9572d5b4-06ff-4df0-a8b7-1072f74a403a` |
| Status | `completed` |
| Hypothesis hash (matches frozen exactly) | `bafb0ddb057c96d2833c46145d813bd7b256a752c844e774f6f4423633466801` |
| Raw signal count | 7 |
| Episode count (same grouping rule as Stat. Validation) | 4 |
| Win rate | 14.3% (1/7) |
| Mean return | −0.477% |
| Median return | −0.564% |
| Std dev | 0.434% |
| Mean MFE / MAE | +0.133% / −0.746% |

5 of the 7 signals cluster into a single ~35-minute sustained-decline
event on Aug 14; the other two (Aug 12, Aug 13) are isolated — the
same clustering pattern development showed.

### Development vs. OOS

| Metric | Development | OOS |
|---|---:|---:|
| Raw signals | 119 | 7 |
| Episodes | 63 | 4 |
| Mean return (backtest semantics) | −0.044% | −0.477% |
| Median return | −0.138% | −0.564% |
| Win rate | 42.0% | 14.3% |
| Std dev | 0.815% | 0.434% |
| Effect size (episode-level) | d = −0.011 (negligible, p≈0.93–1.00) | not computable — n=4 too small |

### Verdict: **INCONCLUSIVE**

There was never a statistically distinguishable development effect to
"survive" — episode-level Cohen's d ≈ −0.01, p ≈ 0.93–1.00 in both V1
and V2. OOS direction is nominally consistent (negative mean/median in
both periods) and the point estimate is numerically larger in holdout,
but with only 4 independent episodes this is exactly the kind of swing
pure sampling variance produces, not evidence the effect strengthened.
No causal claim is made; OOS being nominally negative is not called an
"edge." Recommended next step: **collect more data** — extend the
holdout collection period until enough independent episodes exist
(dozens, not four) to run the same episode-level/dependence-aware
statistical validation OOS did in development, rather than re-testing
a new threshold against this same 14-day holdout.

### Integrity check (verified, not merely asserted)

- `lifecycle_state` = `oos_evaluated` after evaluation
- `hypothesis_hash` identical across freeze, frozen-snapshot fetch,
  provenance fetch, and OOS result
- Frozen snapshot re-fetched after evaluation: byte-identical to what
  freezing produced
- Exactly one `oos_evaluations` row and one
  `experiment_freeze_snapshots` row exist for this experiment
- Development bar count unchanged (3,577) and holdout bar count
  unchanged (810) before vs. after evaluation
- Zero rows written to `historical_features` for the holdout range —
  confirmed by direct query; OOS Evaluation's feature computation is
  fully in-memory/ephemeral
- No second hypothesis, no post-freeze mutation calls, no re-freeze —
  holdout data was queried by application code exactly once (the
  single `POST .../oos-evaluate` call)
