#!/usr/bin/env python3
"""Runs Statistical Validation V2 (app/statistical_validation/v2/)
against an already-created Experiment + Backtest and prints the report
as formatted text -- the dependence-aware successor to
run_statistical_validation.py (V1), which is left unchanged and still
usable on its own.

NOT run by pytest -- app/statistical_validation/v2/engine.py is covered
by tests/test_statistical_validation_v2_*.py; only this file's argument
parsing and print formatting are untested.

Usage:
    cd backend && ./venv/bin/python scripts/run_statistical_validation_v2.py \\
        --experiment-id <id> --backtest-id <id>

    # Override the primary horizon, resample count, seed, block-length
    # multiplier, or target power:
    ./venv/bin/python scripts/run_statistical_validation_v2.py \\
        --experiment-id <id> --backtest-id <id> \\
        --primary-window 5 --n-resamples 10000 --seed 1337 \\
        --block-length-multiplier 4 --power 0.80
"""

import argparse
import sys

sys.path.insert(0, __file__.rsplit("/backend/", 1)[0] + "/backend")

from app.models.statistical_validation_v2 import StatisticalValidationReportV2  # noqa: E402
from app.statistical_validation.v2.engine import (  # noqa: E402
    DEFAULT_BLOCK_LENGTH_MULTIPLIER,
    DEFAULT_CI_LEVEL,
    DEFAULT_N_RESAMPLES,
    DEFAULT_POWER,
    DEFAULT_SEED,
    build_statistical_validation_report_v2,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Statistical Validation V2 against an existing experiment/backtest.")
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--backtest-id", required=True)
    parser.add_argument("--primary-window", type=int, default=5, help="Primary horizon in bars (default: 5)")
    parser.add_argument("--n-resamples", type=int, default=DEFAULT_N_RESAMPLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--ci-level", type=float, default=DEFAULT_CI_LEVEL)
    parser.add_argument("--block-length-multiplier", type=int, default=DEFAULT_BLOCK_LENGTH_MULTIPLIER)
    parser.add_argument("--power", type=float, default=DEFAULT_POWER)
    return parser.parse_args()


def _fmt_pct(x: float, decimals: int = 4) -> str:
    return f"{x * 100:.{decimals}f}%"


def print_report(report: StatisticalValidationReportV2) -> None:
    print("=" * 100)
    print("STATISTICAL VALIDATION REPORT -- V2 (dependence-aware baseline)")
    print("=" * 100)
    print(f"experiment_id: {report.experiment_id}")
    print(f"backtest_id:   {report.backtest_id}")
    print(f"generated_at:  {report.generated_at.isoformat()}")
    print(f"seed={report.seed}  n_resamples={report.n_resamples}  ci_level={report.ci_level}  "
          f"block_length_multiplier={report.block_length_multiplier}")
    print(f"primary horizon: {report.primary_window_bars} bars")
    print()

    pop = report.population
    print("POPULATION:")
    print(f"  raw conditioned signals:        {pop.raw_conditioned_signals}")
    print(f"  conditioned episodes:           {pop.conditioned_episodes}")
    print(f"  baseline raw observations:      {pop.baseline_raw_observations}  (full, overlapping)")
    print(f"  Method A effective baseline n:  {pop.method_a_effective_baseline_n}  (non-overlapping windows)")
    print(f"  Method B block length / count:  {pop.method_b_block_length} bars / {pop.method_b_block_count} blocks")
    print()

    for label, md, wd, test in [
        ("METHOD A -- non-overlapping windows", report.method_a_mean_difference, report.method_a_win_rate_difference, report.method_a_test),
        ("METHOD B -- moving block bootstrap", report.method_b_mean_difference, report.method_b_win_rate_difference, report.method_b_test),
    ]:
        print(f"{label} (n_conditioned={md.n_conditioned}, n_baseline={md.n_baseline}):")
        print(f"  conditioned mean: {_fmt_pct(md.conditioned_mean)}   baseline mean: {_fmt_pct(md.baseline_mean)}   "
              f"diff: {_fmt_pct(md.difference)}   95% CI: [{_fmt_pct(md.ci_low)}, {_fmt_pct(md.ci_high)}]")
        print(f"  conditioned win%: {wd.conditioned_win_rate*100:.2f}%   baseline win%: {wd.baseline_win_rate*100:.2f}%   "
              f"diff: {wd.difference_pp:.2f}pp   95% CI: [{wd.ci_low_pp:.2f}, {wd.ci_high_pp:.2f}]pp")
        print(f"  empirical two-sided p-value: {test.p_value_two_sided:.4f}  (observed diff {_fmt_pct(test.observed_mean_difference)}, "
              f"{test.n_resamples} resamples)")
        print()

    es = report.effect_size
    print(f"EFFECT SIZE (Method A, {es.window_bars}-bar): Cohen's d = {es.cohens_d:.4f} (pooled stdev {_fmt_pct(es.pooled_stdev)})")
    print(f"  interpretation: {es.interpretation}")
    print()

    pa = report.power_analysis
    print(f"POWER / DETECTABLE EFFECT (n_conditioned={pa.n_conditioned_episodes}, n_baseline={pa.n_baseline_effective}, "
          f"alpha={pa.alpha}, power={pa.power}):")
    print(f"  minimum detectable effect size (Cohen's d): {pa.minimum_detectable_effect_size:.4f}")
    print(f"  observed effect size:                       {pa.observed_effect_size:.4f}")
    print(f"  observed effect below detectable threshold: {pa.observed_effect_below_detectable_threshold}")
    print()

    rc = report.robustness
    print(f"ROBUSTNESS ({rc.window_bars}-bar horizon): does the conclusion change materially between methods?")
    print(f"  Method A: diff={_fmt_pct(rc.method_a_mean_difference.difference)}, "
          f"CI=[{_fmt_pct(rc.method_a_mean_difference.ci_low)}, {_fmt_pct(rc.method_a_mean_difference.ci_high)}], "
          f"p={rc.method_a_test.p_value_two_sided:.4f}")
    print(f"  Method B: diff={_fmt_pct(rc.method_b_mean_difference.difference)}, "
          f"CI=[{_fmt_pct(rc.method_b_mean_difference.ci_low)}, {_fmt_pct(rc.method_b_mean_difference.ci_high)}], "
          f"p={rc.method_b_test.p_value_two_sided:.4f}")
    print(f"  conclusion changes materially (CI zero-exclusion disagrees): {rc.conclusion_changes_materially}")
    print()

    print("SECONDARY HORIZONS (descriptive only -- no significance test):")
    for h in report.secondary_horizons:
        print(f"  {h.window_bars:>3} bars: raw={h.raw_signal_count} episodes={h.episode_count}  "
              f"cond_mean={_fmt_pct(h.conditioned_episode_mean)}  base_mean={_fmt_pct(h.baseline_mean)}  "
              f"diff={_fmt_pct(h.difference)}  cond_win%={h.conditioned_win_rate*100:.2f}%  base_win%={h.baseline_win_rate*100:.2f}%")


def main() -> None:
    args = parse_args()
    report = build_statistical_validation_report_v2(
        experiment_id=args.experiment_id,
        backtest_id=args.backtest_id,
        primary_window_bars=args.primary_window,
        seed=args.seed,
        n_resamples=args.n_resamples,
        ci_level=args.ci_level,
        block_length_multiplier=args.block_length_multiplier,
        power=args.power,
    )
    print_report(report)


if __name__ == "__main__":
    main()
