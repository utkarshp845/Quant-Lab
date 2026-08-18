#!/usr/bin/env python3
"""Runs Statistical Validation V1 (app/statistical_validation/) against
an already-created Experiment + Backtest and prints the report as
formatted text.

NOT run by pytest -- like backfill_historical_data.py, this is a manual
CLI wrapper around already-tested logic (app/statistical_validation/
engine.py is covered by tests/test_statistical_validation_*.py); only
this file's argument parsing and print formatting are untested.

Usage:
    cd backend && ./venv/bin/python scripts/run_statistical_validation.py \\
        --experiment-id <id> --backtest-id <id>

    # Override the primary horizon, resample counts, or seed:
    ./venv/bin/python scripts/run_statistical_validation.py \\
        --experiment-id <id> --backtest-id <id> \\
        --primary-window 5 --n-bootstrap 10000 --n-permutations 10000 --seed 1337

Reads from the database at $DATABASE_PATH (or the app's own default,
backend/data/historical_bars.db) -- the same experiment/backtest data
the API already created and ran; this script computes nothing that
wasn't already there, it only adds inference on top of it.
"""

import argparse
import sys

sys.path.insert(0, __file__.rsplit("/backend/", 1)[0] + "/backend")

from app.models.statistical_validation import StatisticalValidationReport  # noqa: E402
from app.statistical_validation.engine import (  # noqa: E402
    DEFAULT_CI_LEVEL,
    DEFAULT_N_BOOTSTRAP,
    DEFAULT_N_PERMUTATIONS,
    DEFAULT_SEED,
    build_statistical_validation_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Statistical Validation V1 against an existing experiment/backtest.")
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--backtest-id", required=True)
    parser.add_argument("--primary-window", type=int, default=5, help="Primary horizon in bars (default: 5)")
    parser.add_argument("--n-bootstrap", type=int, default=DEFAULT_N_BOOTSTRAP)
    parser.add_argument("--n-permutations", type=int, default=DEFAULT_N_PERMUTATIONS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--ci-level", type=float, default=DEFAULT_CI_LEVEL)
    return parser.parse_args()


def _fmt_pct(x: float, decimals: int = 4) -> str:
    return f"{x * 100:.{decimals}f}%"


def print_report(report: StatisticalValidationReport) -> None:
    print("=" * 100)
    print("STATISTICAL VALIDATION REPORT")
    print("=" * 100)
    print(f"experiment_id: {report.experiment_id}")
    print(f"backtest_id:   {report.backtest_id}")
    print(f"generated_at:  {report.generated_at.isoformat()}")
    print(f"seed={report.seed}  n_bootstrap={report.n_bootstrap}  n_permutations={report.n_permutations}")
    print(f"primary horizon: {report.primary_window_bars} bars")
    print()
    print("Episode rule:", report.episode_rule.description)
    print()

    header = (
        f"{'Window':>7} {'Role':>11} {'raw':>5} {'episodes':>9} {'baseline':>9} "
        f"{'cond mean':>10} {'base mean':>10} {'diff':>9} {'95% CI':>22} "
        f"{'cond win%':>10} {'base win%':>10} {'win diff pp':>12} {'win% 95% CI (pp)':>20}"
    )
    print(header)
    print("-" * len(header))
    for h in report.horizons:
        role = "PRIMARY" if h.is_primary else "exploratory"
        md, wd = h.mean_difference, h.win_rate_difference
        ci = f"[{_fmt_pct(md.ci_low)}, {_fmt_pct(md.ci_high)}]"
        win_ci = f"[{wd.ci_low_pp:.2f}, {wd.ci_high_pp:.2f}]"
        print(
            f"{h.window_bars:>7} {role:>11} {h.sample_sizes.raw_signal_count:>5} {h.sample_sizes.episode_count:>9} "
            f"{h.sample_sizes.baseline_count:>9} {_fmt_pct(md.conditioned_mean):>10} {_fmt_pct(md.baseline_mean):>10} "
            f"{_fmt_pct(md.difference):>9} {ci:>22} {_fmt_pct(wd.conditioned_win_rate, 2):>10} "
            f"{_fmt_pct(wd.baseline_win_rate, 2):>10} {wd.difference_pp:>11.2f}pp {win_ci:>20}"
        )
        sb = h.session_boundary
        print(
            f"         session-boundary crossings: conditioned {sb.n_conditioned_crossing}/{sb.n_conditioned_observations}, "
            f"baseline {sb.n_baseline_crossing}/{sb.n_baseline_observations}"
        )
    print()

    pt = report.primary_permutation_test
    print(f"PRIMARY PERMUTATION TEST ({pt.window_bars}-bar horizon, episode-level, n_conditioned={pt.n_conditioned}, "
          f"n_baseline={pt.n_baseline}, seed={pt.seed}):")
    print(f"  H0: the condition provides no information about {pt.window_bars}-bar forward returns beyond baseline.")
    print(f"  observed mean difference: {_fmt_pct(pt.observed_mean_difference)}")
    print(f"  empirical two-sided p-value: {pt.p_value_two_sided:.4f} (from {pt.n_permutations} permutations)")
    print()

    es = report.primary_effect_size
    print(f"PRIMARY EFFECT SIZE ({es.window_bars}-bar horizon, episode-level):")
    print(f"  Cohen's d: {es.cohens_d:.4f} (pooled stdev {_fmt_pct(es.pooled_stdev)})")
    print(f"  interpretation: {es.interpretation}")
    print()

    rc = report.robustness_check
    print(f"ROBUSTNESS CHECK ({rc.window_bars}-bar horizon): raw signal population vs. episode-level population")
    print(f"  raw (n={rc.raw_n}):      mean diff {_fmt_pct(rc.raw_mean_difference)}, p={rc.raw_p_value:.4f}")
    print(f"  episode (n={rc.episode_n}): mean diff {_fmt_pct(rc.episode_mean_difference)}, p={rc.episode_p_value:.4f}  <- authoritative")


def main() -> None:
    args = parse_args()
    report = build_statistical_validation_report(
        experiment_id=args.experiment_id,
        backtest_id=args.backtest_id,
        primary_window_bars=args.primary_window,
        seed=args.seed,
        n_bootstrap=args.n_bootstrap,
        n_permutations=args.n_permutations,
        ci_level=args.ci_level,
    )
    print_report(report)


if __name__ == "__main__":
    main()
