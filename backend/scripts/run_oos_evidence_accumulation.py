#!/usr/bin/env python3
"""Runs OOS Evidence Accumulation V1 (app/oos_evidence/) end to end and
prints a report: freezes ONE experiment, then registers and evaluates
TWO OR MORE sequential OOS periods against it, then prints the
aggregated evidence.

Requirement 8's own instruction: "run a small real-data validation
using existing real TSLA data if sufficient sequential dates are
available." This script FIRST checks this app's own storage
(app.storage.historical_bar_repository) for already-ingested, real
TSLA bars spanning enough sequential dates to build a development
window plus two non-overlapping OOS windows; if found, it uses THAT
data, unmodified, and fetches nothing. This sandboxed environment has
neither stored real TSLA bars nor provider credentials configured
(app.config.get_provider_credential() -- confirmed empty for every
provider this app supports) to fetch any at run time, so in practice
this falls back to `--demo`: a seeded, reproducible synthetic random
walk, exactly like scripts/run_oos_evaluation.py's own `--demo` flag
and for the identical, explicitly-stated reason (no live provider
access here) -- this is a SYSTEM VALIDATION run (spec section 8's own
neighbor, "Do not optimize the hypothesis based on the results"), never
a strategy search, and the fallback is reported plainly in the printed
output, not silently substituted.

Usage:
    cd backend && ./venv/bin/python scripts/run_oos_evidence_accumulation.py --demo

    # Against an experiment already frozen (optionally with an
    # originally-linked partition) via the real API, when real TSLA
    # data already covers enough sequential dates:
    ./venv/bin/python scripts/run_oos_evidence_accumulation.py --experiment-id <id>
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
from app.oos_evidence.aggregation import build_evidence_summary  # noqa: E402
from app.oos_evidence.evaluation import evaluate_oos_period  # noqa: E402
from app.oos_evidence.period import validate_new_period  # noqa: E402
from app.research.lifecycle import build_freeze_snapshot, compute_hypothesis_hash, validate_snapshot_partition_linkage  # noqa: E402
from app.storage import experiment_freeze_repository, historical_bar_repository, oos_evaluation_repository, oos_evidence_repository, oos_partition_repository, research_repository  # noqa: E402

_DEMO_SYMBOL, _DEMO_TIMEFRAME, _DEMO_PROVIDER = "TSLA", "5m", "csv"
_DEMO_SEED = 20260818  # fixed -- reproducible, not re-rolled to hunt for a "good-looking" result
_DEMO_CONDITION = [FeatureCondition(feature_id="price.return_5m", operator=FeatureConditionOperator.GT, value=-999.0)]
_DEMO_OUTCOME = Outcome(metric="forward_return", horizon_minutes=5, operator=ConditionOperator.GT, threshold=-999.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OOS Evidence Accumulation V1 against a frozen experiment.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--experiment-id", help="An already-frozen experiment id (real TSLA data must already be stored).")
    group.add_argument("--demo", action="store_true", help="Seed synthetic data and run a fully self-contained demo.")
    parser.add_argument("--n-periods", type=int, default=2, help="How many OOS periods to register and evaluate (>= 2). Default: 2.")
    parser.add_argument("--db-path", default=None, help="SQLite file to use (default: app's configured DATABASE_PATH).")
    return parser.parse_args()


def _check_for_real_tsla_data(db_path: str | None) -> bool:
    """Requirement 8's own instruction, honored literally: checks
    whether real, previously-ingested TSLA bars already exist in this
    app's own storage AND whether any provider credential is even
    configured to fetch more -- prints exactly what it finds, never
    silently assumes either way."""
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


def _run_demo(db_path: str | None, n_periods: int) -> tuple[str, list[str]]:
    """Seeds one shared development window + `n_periods` sequential,
    non-overlapping OOS windows, freezes ONE experiment against the
    FIRST window (as its originally-linked partition -- OOS Evaluation
    v1's own mechanism, unmodified), then registers the remaining
    `n_periods - 1` windows as OOS Evidence Accumulation V1 periods.
    Returns (experiment_id, [oos_partition_id, ...] for every
    ADDITIONAL period, in order)."""
    development_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    development_end = datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)
    development_bars = _random_walk_bars(
        development_start, 288, seed=_DEMO_SEED, symbol=_DEMO_SYMBOL, timeframe=_DEMO_TIMEFRAME, provider=_DEMO_PROVIDER
    )
    historical_bar_repository.save_bars(development_bars, db_path=db_path)

    holdout_windows = [
        (datetime(2024, 1, 2 + i, tzinfo=timezone.utc), datetime(2024, 1, 2 + i, 4, 0, tzinfo=timezone.utc)) for i in range(n_periods)
    ]
    partitions = []
    for i, (holdout_start, holdout_end) in enumerate(holdout_windows):
        holdout_bars = _random_walk_bars(
            holdout_start, 48, seed=_DEMO_SEED + 1 + i, symbol=_DEMO_SYMBOL, timeframe=_DEMO_TIMEFRAME, provider=_DEMO_PROVIDER
        )
        historical_bar_repository.save_bars(holdout_bars, db_path=db_path)
        partition = OOSPartition.new(
            OOSPartitionCreateRequest(
                symbol=_DEMO_SYMBOL, timeframe=_DEMO_TIMEFRAME, provider=_DEMO_PROVIDER,
                development_start=development_start, development_end=development_end,
                holdout_start=holdout_start, holdout_end=holdout_end,
                label=f"walk-forward window #{i + 1}",
            )
        )
        oos_partition_repository.save_partition(partition, db_path=db_path)
        partitions.append(partition)
    print(f"[demo] seeded {len(partitions)} sequential OOS periods: " + ", ".join(p.id for p in partitions))

    request = ExperimentCreateRequest(
        name="OOS Evidence Accumulation V1 -- system validation run",
        hypothesis="A positive 5-minute return is followed by another positive 5-minute return (deliberately trivial -- system validation, not a strategy search).",
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

    additional_partition_ids = []
    for partition in partitions[1:]:
        _register_period(db_path, snapshot, partition)
        additional_partition_ids.append(partition.id)
        print(f"[demo] registered additional OOS period {partition.id} ({partition.holdout_start.date()})")

    return experiment.id, additional_partition_ids


def _register_period(db_path, snapshot, new_partition: OOSPartition) -> OOSPeriod:
    """Mirrors app/api/oos_evidence.py's real registration route."""
    validate_snapshot_partition_linkage(snapshot, new_partition)
    already_registered = []
    if snapshot.oos_partition_id is not None:
        original = oos_partition_repository.get_partition(snapshot.oos_partition_id, db_path=db_path)
        if original is not None:
            already_registered.append(original)
    already_registered.extend(
        partition
        for partition in (
            oos_partition_repository.get_partition(p.oos_partition_id, db_path=db_path)
            for p in oos_evidence_repository.list_periods(snapshot.experiment_id, db_path=db_path)
        )
        if partition is not None
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


def print_evidence_report(experiment_id: str, db_path: str | None) -> None:
    snapshot = experiment_freeze_repository.get_snapshot(experiment_id, db_path=db_path)
    evaluations = oos_evaluation_repository.list_evaluations(experiment_id, db_path=db_path)
    signals_by_evaluation = {
        e.id: oos_evaluation_repository.get_signals(e.id, db_path=db_path)
        for e in evaluations
        if e.status.value == "completed"
    }
    summary = build_evidence_summary(
        experiment_id=experiment_id, hypothesis_hash=snapshot.hypothesis_hash,
        evaluations=evaluations, signals_by_evaluation=signals_by_evaluation,
    )

    print("=" * 100)
    print("OOS EVIDENCE ACCUMULATION REPORT")
    print("=" * 100)
    print(f"experiment_id:      {summary.experiment_id}")
    print(f"hypothesis_hash:    {summary.hypothesis_hash}")
    print(f"oos_period_count:   {summary.oos_period_count}")
    print(f"completed/failed:   {summary.completed_evaluation_count} / {summary.failed_evaluation_count}")
    print(f"total_raw_signals:      {summary.total_raw_signals}")
    print(f"total_independent_episodes: {summary.total_independent_episodes}")
    print(f"mean_return:        {summary.mean_return}")
    print(f"median_return:      {summary.median_return}")
    print(f"win_rate:           {summary.win_rate}")
    print(f"std_dev_return:     {summary.std_dev_return}")
    print(f"mean_mfe / mean_mae: {summary.mean_mfe} / {summary.mean_mae}")
    print(f"earliest_oos_start: {summary.earliest_oos_start}")
    print(f"latest_oos_end:     {summary.latest_oos_end}")
    print()
    print("per-period results:")
    for period_result in summary.per_period_results:
        print(
            f"  - evaluation={period_result.evaluation_id}  partition={period_result.oos_partition_id}  "
            f"window=[{period_result.oos_start.date()} .. {period_result.oos_end.date()}]  "
            f"status={period_result.status.value}  signals={period_result.signal_count}  episodes={period_result.episode_count}"
        )
    print("=" * 100)

    hashes = {e.hypothesis_hash for e in evaluations}
    print(f"hypothesis_hash identical across all {len(evaluations)} evaluations: {hashes == {snapshot.hypothesis_hash}}")


def main() -> None:
    args = parse_args()
    if args.n_periods < 2:
        raise SystemExit("--n-periods must be at least 2 to demonstrate accumulation across multiple periods.")

    experiment_id = args.experiment_id
    additional_partition_ids: list[str] = []

    if args.demo:
        experiment_id, additional_partition_ids = _run_demo(args.db_path, args.n_periods)
    else:
        _check_for_real_tsla_data(args.db_path)
        experiment = research_repository.get_experiment(experiment_id, db_path=args.db_path)
        if experiment is None:
            raise SystemExit(f"No experiment with id {experiment_id!r}")
        additional_partition_ids = [p.oos_partition_id for p in oos_evidence_repository.list_periods(experiment_id, db_path=args.db_path)]
        if not additional_partition_ids:
            raise SystemExit(
                f"Experiment {experiment_id!r} has no OOS periods registered yet -- register at least one "
                "additional period via POST /research/experiments/{id}/oos-periods first (see the README's "
                "OOS Evidence Accumulation V1 section), or re-run with --demo."
            )

    # Evaluate the original partition's own evaluation (OOS Evaluation
    # v1's own, unmodified pipeline) plus every additional period, in
    # order -- append-only, each with its own evaluation_id.
    from app.oos_evaluation.engine import evaluate_oos

    original_result, original_signals = evaluate_oos(experiment_id, db_path=args.db_path)
    oos_evaluation_repository.save_evaluation(original_result, original_signals, db_path=args.db_path)
    print(f"[evaluate] period #1 (original partition {original_result.oos_partition_id}): status={original_result.status.value}, signals={original_result.signal_count}")
    if original_result.status.value == "completed":
        experiment = research_repository.get_experiment(experiment_id, db_path=args.db_path)
        if experiment is not None and experiment.lifecycle_state.value == "frozen":
            research_repository.mark_oos_evaluated(experiment_id, oos_evaluated_at=original_result.evaluated_at, db_path=args.db_path)

    for partition_id in additional_partition_ids:
        result, signals = evaluate_oos_period(experiment_id, partition_id, db_path=args.db_path)
        oos_evaluation_repository.save_evaluation(result, signals, db_path=args.db_path)
        print(f"[evaluate] additional period (partition {partition_id}): status={result.status.value}, signals={result.signal_count}")
        if result.status.value == "completed":
            experiment = research_repository.get_experiment(experiment_id, db_path=args.db_path)
            if experiment is not None and experiment.lifecycle_state.value == "frozen":
                research_repository.mark_oos_evaluated(experiment_id, oos_evaluated_at=result.evaluated_at, db_path=args.db_path)

    print_evidence_report(experiment_id, args.db_path)


if __name__ == "__main__":
    main()
