#!/usr/bin/env python3
"""Runs OOS Statistical Review V1 (app/oos_statistical_review/) end to
end and prints a report: freezes ONE experiment, registers and
evaluates SEVERAL sequential OOS periods against it (reusing OOS
Evidence Accumulation V1's own mechanism -- see
scripts/run_oos_evidence_accumulation.py), then builds and prints the
resulting formal statistical review.

Requirement 14's own instruction: "if real OOS evidence exists in the
environment, run the review against it. Otherwise use a deterministic
synthetic fixture and explicitly state that real-data validation was
unavailable." This script FIRST checks this app's own storage for
already-ingested real TSLA bars and any configured provider credential
(app.config.get_provider_credential() -- see
_check_for_real_tsla_data() below); neither is present in this
sandboxed environment, reported plainly, so this falls back to
`--demo`: a seeded, reproducible random walk, exactly like
scripts/run_oos_evaluation.py's/run_oos_evidence_accumulation.py's own
`--demo` flag. This is a SYSTEM VALIDATION run, never a strategy
search -- the hypothesis (`price.return_5m > 0` predicts another
positive 15-minute forward return) is chosen once, deliberately
simple, and never tuned against the results this script prints.

Usage:
    cd backend && ./venv/bin/python scripts/run_oos_statistical_review.py --demo

    # Against an experiment that already has completed OOS evidence
    # via the real API:
    ./venv/bin/python scripts/run_oos_statistical_review.py --experiment-id <id>
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, __file__.rsplit("/backend/", 1)[0] + "/backend")

from app import config  # noqa: E402
from app.models.market_data import HistoricalBar  # noqa: E402
from app.models.oos_evidence import OOSPeriod  # noqa: E402
from app.models.oos_partition import OOSPartition, OOSPartitionCreateRequest  # noqa: E402
from app.models.research import ConditionOperator, Experiment, ExperimentCreateRequest, FeatureCondition, FeatureConditionOperator, Outcome  # noqa: E402
from app.oos_evaluation.engine import evaluate_oos  # noqa: E402
from app.oos_evidence.evaluation import evaluate_oos_period  # noqa: E402
from app.oos_evidence.period import validate_new_period  # noqa: E402
from app.oos_statistical_review.engine import build_oos_statistical_review  # noqa: E402
from app.research.lifecycle import build_freeze_snapshot, compute_hypothesis_hash, validate_snapshot_partition_linkage  # noqa: E402
from app.storage import experiment_freeze_repository, historical_bar_repository, oos_evaluation_repository, oos_evidence_repository, oos_partition_repository, research_repository  # noqa: E402
from app.storage.oos_statistical_review_repository import save_review  # noqa: E402

_DEMO_SYMBOL, _DEMO_TIMEFRAME, _DEMO_PROVIDER = "TSLA", "5m", "csv"
_DEMO_SEED = 20260818  # fixed -- reproducible, not re-rolled to hunt for a "good-looking" result
_DEMO_CONDITION = [FeatureCondition(feature_id="price.return_5m", operator=FeatureConditionOperator.GT, value=0.0)]
_DEMO_OUTCOME = Outcome(metric="forward_return", horizon_minutes=15, operator=ConditionOperator.GT, threshold=0.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OOS Statistical Review V1 against a frozen experiment's accumulated OOS evidence.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--experiment-id", help="An already-frozen experiment id with completed OOS evidence.")
    group.add_argument("--demo", action="store_true", help="Seed synthetic data, accumulate evidence, and run a fully self-contained demo.")
    parser.add_argument("--n-periods", type=int, default=6, help="How many OOS periods to accumulate for --demo. Default: 6.")
    parser.add_argument("--db-path", default=None, help="SQLite file to use (default: app's configured DATABASE_PATH).")
    return parser.parse_args()


def _check_for_real_tsla_data(db_path: str | None) -> bool:
    existing = historical_bar_repository.get_bars(
        symbol=_DEMO_SYMBOL, timeframe=_DEMO_TIMEFRAME, provider="alpaca",
        start=datetime(2000, 1, 1, tzinfo=timezone.utc).date(), end=datetime.now(timezone.utc).date(),
        db_path=db_path,
    )
    has_credentials = any(
        config.get_provider_credential(provider, credential) is not None
        for provider, credential in (("alpaca", "api_key_id"), ("massive", "api_key"), ("schwab", "client_id"))
    )
    print(f"[real-data check] stored real (provider='alpaca') TSLA/5m bars found: {len(existing)}")
    print(f"[real-data check] any market-data provider credential configured: {has_credentials}")
    if len(existing) == 0 or not has_credentials:
        print(
            "[real-data check] REAL-DATA VALIDATION UNAVAILABLE in this environment -- falling back to a "
            "deterministic, seeded synthetic dataset (see this script's own module docstring)."
        )
    return len(existing) > 0


def _random_walk_bars(start: datetime, count: int, *, seed: int, symbol: str, timeframe: str, provider: str) -> list[HistoricalBar]:
    import numpy as np

    rng = np.random.default_rng(seed)
    price = 200.0
    bars = []
    for i in range(count):
        price = max(1.0, price * (1 + rng.normal(0, 0.0008)))
        high = price * (1 + abs(rng.normal(0, 0.0004)))
        low = price * (1 - abs(rng.normal(0, 0.0004)))
        open_ = price * (1 + rng.normal(0, 0.0002))
        bars.append(
            HistoricalBar(
                symbol=symbol, timestamp=start + timedelta(minutes=5 * i), open=open_, high=max(high, open_, price),
                low=min(low, open_, price), close=price, volume=int(rng.integers(500, 5000)),
                provider=provider, timeframe=timeframe,
            )
        )
    return bars


def _register_period(db_path, snapshot, new_partition: OOSPartition) -> OOSPeriod:
    validate_snapshot_partition_linkage(snapshot, new_partition)
    already_registered = []
    if snapshot.oos_partition_id is not None:
        original = oos_partition_repository.get_partition(snapshot.oos_partition_id, db_path=db_path)
        if original is not None:
            already_registered.append(original)
    already_registered.extend(
        p for p in (oos_partition_repository.get_partition(period.oos_partition_id, db_path=db_path) for period in oos_evidence_repository.list_periods(snapshot.experiment_id, db_path=db_path)) if p is not None
    )
    validate_new_period(snapshot=snapshot, new_partition=new_partition, already_registered_partitions=already_registered)
    period = OOSPeriod(
        id=new_partition.id, experiment_id=snapshot.experiment_id, oos_partition_id=new_partition.id,
        symbol=new_partition.symbol, timeframe=new_partition.timeframe, provider=new_partition.provider,
        oos_start=new_partition.holdout_start, oos_end=new_partition.holdout_end, label=new_partition.label,
        registered_at=datetime.now(timezone.utc),
    )
    oos_evidence_repository.save_period(period, db_path=db_path)
    return period


def _run_demo(db_path: str | None, n_periods: int) -> str:
    development_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    development_end = datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)
    development_bars = _random_walk_bars(
        development_start, 288, seed=_DEMO_SEED, symbol=_DEMO_SYMBOL, timeframe=_DEMO_TIMEFRAME, provider=_DEMO_PROVIDER
    )
    historical_bar_repository.save_bars(development_bars, db_path=db_path)

    partitions = []
    for i in range(n_periods):
        holdout_start = datetime(2024, 1, 2 + i, tzinfo=timezone.utc)
        holdout_end = datetime(2024, 1, 2 + i, 4, 0, tzinfo=timezone.utc)
        holdout_bars = _random_walk_bars(
            holdout_start, 48, seed=_DEMO_SEED + 1 + i, symbol=_DEMO_SYMBOL, timeframe=_DEMO_TIMEFRAME, provider=_DEMO_PROVIDER
        )
        historical_bar_repository.save_bars(holdout_bars, db_path=db_path)
        partition = OOSPartition.new(
            OOSPartitionCreateRequest(
                symbol=_DEMO_SYMBOL, timeframe=_DEMO_TIMEFRAME, provider=_DEMO_PROVIDER,
                development_start=development_start, development_end=development_end,
                holdout_start=holdout_start, holdout_end=holdout_end, label=f"walk-forward window #{i + 1}",
            )
        )
        oos_partition_repository.save_partition(partition, db_path=db_path)
        partitions.append(partition)
    print(f"[demo] seeded {len(partitions)} sequential OOS periods: " + ", ".join(p.id for p in partitions))

    request = ExperimentCreateRequest(
        name="OOS Statistical Review V1 -- system validation run",
        hypothesis="A positive 5-minute return is followed by another positive 15-minute forward return (deliberately simple -- system validation, not a strategy search).",
        symbol=_DEMO_SYMBOL, start_date=development_start.date(), end_date=development_end.date(),
        timeframe=_DEMO_TIMEFRAME, provider=_DEMO_PROVIDER, conditions=_DEMO_CONDITION, outcome=_DEMO_OUTCOME,
    )
    experiment = Experiment.new(request)
    research_repository.save_experiment(experiment, db_path=db_path)
    research_repository.set_oos_partition(experiment.id, partitions[0].id, db_path=db_path)
    experiment = research_repository.get_experiment(experiment.id, db_path=db_path)

    frozen_at = datetime.now(timezone.utc)
    hypothesis_hash = compute_hypothesis_hash(experiment)
    snapshot = build_freeze_snapshot(experiment, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at)
    experiment_freeze_repository.save_snapshot(snapshot, db_path=db_path)
    research_repository.freeze_experiment(
        experiment.id, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at, oos_partition_id=partitions[0].id, db_path=db_path
    )
    print(f"[demo] experiment {experiment.id} FROZEN against period #1 ({partitions[0].id}), hypothesis_hash={hypothesis_hash}")

    result, signals = evaluate_oos(experiment.id, db_path=db_path)
    oos_evaluation_repository.save_evaluation(result, signals, db_path=db_path)
    print(f"[evaluate] period #1: status={result.status.value}, signals={result.signal_count}")
    if result.status.value == "completed":
        exp = research_repository.get_experiment(experiment.id, db_path=db_path)
        if exp is not None and exp.lifecycle_state.value == "frozen":
            research_repository.mark_oos_evaluated(experiment.id, oos_evaluated_at=result.evaluated_at, db_path=db_path)

    for partition in partitions[1:]:
        _register_period(db_path, snapshot, partition)
        r, s = evaluate_oos_period(experiment.id, partition.id, db_path=db_path)
        oos_evaluation_repository.save_evaluation(r, s, db_path=db_path)
        print(f"[evaluate] additional period {partition.id}: status={r.status.value}, signals={r.signal_count}")

    return experiment.id


def print_review_report(review) -> None:
    print("=" * 100)
    print("OOS STATISTICAL REVIEW REPORT")
    print("=" * 100)
    print(f"review_id:              {review.id}")
    print(f"experiment_id:          {review.experiment_id}")
    print(f"hypothesis_hash:        {review.hypothesis_hash}")
    print(f"review_config_version:  {review.review_config_version}")
    print(f"symbol/timeframe/provider: {review.symbol}/{review.timeframe}/{review.provider}")
    print(f"outcome: {review.outcome_metric} {review.outcome_operator} {review.outcome_threshold} @ {review.outcome_horizon_minutes}m (primary_window_bars={review.primary_window_bars})")
    print(f"included evaluations: {len(review.included_evaluation_ids)}  excluded: {len(review.excluded_evaluations)}")
    print(f"config: seed={review.seed} n_resamples={review.n_resamples} ci_level={review.ci_level} block_length_multiplier={review.block_length_multiplier} power_target={review.power_target} min_episodes={review.min_episodes_for_formal_test}")
    print()
    print(f"sample_sizes: {review.sample_sizes}")
    print()
    if review.method_a_test is not None:
        print(f"Method A (non-overlapping windows): {review.method_a_mean_difference}")
        print(f"  test: {review.method_a_test}")
        print(f"  win rate: {review.method_a_win_rate_difference}")
        print(f"Method B (moving block bootstrap): {review.method_b_mean_difference}")
        print(f"  test: {review.method_b_test}")
        print(f"  win rate: {review.method_b_win_rate_difference}")
        print(f"effect_size: {review.effect_size}")
        print(f"power_analysis: {review.power_analysis}")
        print(f"robustness.conclusion_changes_materially: {review.robustness.conclusion_changes_materially}")
    else:
        print("(too little evidence -- no formal test was run; see verdict_reasoning below)")
    print()
    print("per-period results:")
    for p in review.per_period_results:
        print(f"  - {p.oos_partition_id}  [{p.oos_start.date()} .. {p.oos_end.date()}]  raw={p.raw_signal_count}  episodes={p.episode_count}  mean={p.mean_return}  win_rate={p.win_rate}")
    print()
    print(f"exploratory_horizons_note: {review.exploratory_horizons_note}")
    print()
    print(f"VERDICT: {review.verdict.value.upper()}")
    print(f"reasoning: {review.verdict_reasoning}")
    print("=" * 100)


def main() -> None:
    args = parse_args()
    experiment_id = args.experiment_id
    if args.demo:
        _check_for_real_tsla_data(args.db_path)
        experiment_id = _run_demo(args.db_path, args.n_periods)
    else:
        has_real_data = _check_for_real_tsla_data(args.db_path)
        if not has_real_data:
            print("[warn] proceeding against --experiment-id despite no confirmed real TSLA data in storage.")

    review = build_oos_statistical_review(experiment_id, db_path=args.db_path)
    save_review(review, db_path=args.db_path)
    print_review_report(review)


if __name__ == "__main__":
    main()
