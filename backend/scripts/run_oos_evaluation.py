#!/usr/bin/env python3
"""Runs OOS Evaluation v1 (app/oos_evaluation/) end to end and prints a
report -- either against an already-frozen, already-partition-linked
experiment (`--experiment-id`), or, with `--demo`, a fully self-
contained real-data validation run: seeds a deterministic synthetic
OHLCV dataset, creates an OOS partition, creates and freezes a
DELIBERATELY SIMPLE experiment (a single, un-optimized condition -- see
`_DEMO_CONDITIONS`/`_DEMO_OUTCOME` below, chosen once and never tuned
against the data), links it to the partition, and evaluates it -- this
is a SYSTEM VALIDATION run (spec section 11: "Do not optimize it."),
never a strategy search.

`--demo`'s synthetic data exists because this sandboxed environment has
no live provider credentials/network access (unlike
scripts/backfill_historical_data.py, which fetches real historical
bars from a real provider) -- it is a seeded, reproducible random walk
(numpy.random.default_rng(seed), not hand-picked to make the condition
fire), never a hand-crafted series engineered to satisfy the demo
condition. Without `--demo`, this script computes nothing new that
app/oos_evaluation/engine.py::evaluate_oos() doesn't already -- it is a
thin CLI wrapper, matching scripts/run_statistical_validation.py's own
"manual, opt-in script for a real run, not baked into the HTTP surface
for daily use" precedent (the actual HTTP surface for this feature is
app/api/oos_evaluation.py; this script exists for exactly this kind of
end-to-end demonstration/audit run).

Usage:
    cd backend && ./venv/bin/python scripts/run_oos_evaluation.py --demo

    # Against an experiment already frozen+linked via the real API:
    ./venv/bin/python scripts/run_oos_evaluation.py --experiment-id <id>
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, __file__.rsplit("/backend/", 1)[0] + "/backend")

from app.models.market_data import HistoricalBar  # noqa: E402
from app.models.oos_evaluation import OOSEvaluationResult, OOSSignal  # noqa: E402
from app.models.oos_partition import OOSPartition, OOSPartitionCreateRequest  # noqa: E402
from app.models.research import ConditionOperator, Experiment, ExperimentCreateRequest, ExperimentLifecycleState, FeatureCondition, FeatureConditionOperator, Outcome  # noqa: E402
from app.oos_evaluation.engine import evaluate_oos  # noqa: E402
from app.research.lifecycle import build_freeze_snapshot, compute_hypothesis_hash  # noqa: E402
from app.storage import experiment_freeze_repository, historical_bar_repository, oos_evaluation_repository, oos_partition_repository, research_repository  # noqa: E402

_DEMO_SYMBOL, _DEMO_TIMEFRAME, _DEMO_PROVIDER = "TSLA", "5m", "csv"
_DEMO_SEED = 20260818  # fixed -- reproducible, not re-rolled to hunt for a "good-looking" result
_DEMO_CONDITIONS = [FeatureCondition(feature_id="price.return_15m", operator=FeatureConditionOperator.LTE, value=-0.005)]
_DEMO_OUTCOME = Outcome(metric="forward_return", horizon_minutes=30, operator=ConditionOperator.LT, threshold=0.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OOS Evaluation v1 against a frozen experiment.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--experiment-id", help="An already-frozen, partition-linked experiment id.")
    group.add_argument("--demo", action="store_true", help="Seed synthetic data and run a fully self-contained demo.")
    parser.add_argument("--db-path", default=None, help="SQLite file to use (default: app's configured DATABASE_PATH).")
    return parser.parse_args()


def _random_walk_bars(start: datetime, count: int, *, seed: int, symbol: str, timeframe: str, provider: str) -> list[HistoricalBar]:
    import numpy as np

    rng = np.random.default_rng(seed)
    price = 200.0
    bars = []
    for i in range(count):
        # A small, realistic per-bar drift: mean 0, stdev ~8bps -- not
        # tuned to make any particular condition fire more or less
        # often, just a plausible intraday random walk.
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


def _run_demo(db_path: str | None) -> str:
    development_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    holdout_start = datetime(2024, 3, 1, tzinfo=timezone.utc)
    holdout_end = holdout_start + timedelta(days=5)
    development_end = holdout_start - timedelta(microseconds=1)

    # ~2 months of continuous 5-minute bars for development (comfortably
    # covers the largest feature warm-up window many times over), 5
    # days for holdout.
    development_bar_count = int((holdout_start - development_start).total_seconds() // 300)
    holdout_bar_count = int((holdout_end - holdout_start).total_seconds() // 300) + 1

    print(f"[demo] seeding {development_bar_count} development bars + {holdout_bar_count} holdout bars "
          f"(seed={_DEMO_SEED}, random walk, {_DEMO_SYMBOL}/{_DEMO_TIMEFRAME}/{_DEMO_PROVIDER})")
    development_bars = _random_walk_bars(
        development_start, development_bar_count, seed=_DEMO_SEED, symbol=_DEMO_SYMBOL, timeframe=_DEMO_TIMEFRAME, provider=_DEMO_PROVIDER
    )
    holdout_bars = _random_walk_bars(
        holdout_start, holdout_bar_count, seed=_DEMO_SEED + 1, symbol=_DEMO_SYMBOL, timeframe=_DEMO_TIMEFRAME, provider=_DEMO_PROVIDER
    )
    historical_bar_repository.save_bars(development_bars, db_path=db_path)
    historical_bar_repository.save_bars(holdout_bars, db_path=db_path)

    partition = OOSPartition.new(
        OOSPartitionCreateRequest(
            symbol=_DEMO_SYMBOL, timeframe=_DEMO_TIMEFRAME, provider=_DEMO_PROVIDER,
            development_start=development_start, development_end=development_end,
            holdout_start=holdout_start, holdout_end=holdout_end,
        )
    )
    oos_partition_repository.save_partition(partition, db_path=db_path)
    print(f"[demo] OOS partition {partition.id}")

    request = ExperimentCreateRequest(
        name="OOS Evaluation V1 -- system validation run",
        hypothesis="A >=0.5% decline over the trailing 15 minutes is followed by a further decline over the next 30.",
        symbol=_DEMO_SYMBOL, start_date=development_start.date(), end_date=development_end.date(),
        timeframe=_DEMO_TIMEFRAME, provider=_DEMO_PROVIDER, conditions=_DEMO_CONDITIONS, outcome=_DEMO_OUTCOME,
    )
    experiment = Experiment.new(request)
    research_repository.save_experiment(experiment, db_path=db_path)
    research_repository.set_oos_partition(experiment.id, partition.id, db_path=db_path)
    experiment = research_repository.get_experiment(experiment.id, db_path=db_path)

    frozen_at = datetime.now(timezone.utc)
    hypothesis_hash = compute_hypothesis_hash(experiment)
    snapshot = build_freeze_snapshot(experiment, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at)
    experiment_freeze_repository.save_snapshot(snapshot, db_path=db_path)
    research_repository.freeze_experiment(
        experiment.id, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at, oos_partition_id=partition.id, db_path=db_path
    )
    print(f"[demo] experiment {experiment.id} FROZEN, hypothesis_hash={hypothesis_hash}")

    return experiment.id


def print_report(result: OOSEvaluationResult, signals: list[OOSSignal]) -> None:
    print("=" * 100)
    print("OOS EVALUATION REPORT")
    print("=" * 100)
    print(f"evaluation_id:      {result.id}")
    print(f"experiment_id:      {result.experiment_id}")
    print(f"frozen_snapshot_id: {result.frozen_snapshot_id}")
    print(f"hypothesis_hash:    {result.hypothesis_hash}")
    print(f"oos_partition_id:   {result.oos_partition_id}")
    print(f"symbol/timeframe/provider: {result.symbol}/{result.timeframe}/{result.provider}")
    print(f"feature_contract_version:  {result.feature_contract_version}")
    print(f"holdout: [{result.holdout_start.isoformat()} .. {result.holdout_end.isoformat()}]")
    print(f"frozen_at:    {result.frozen_at.isoformat()}")
    print(f"evaluated_at: {result.evaluated_at.isoformat()} (after freeze: {result.evaluated_at > result.frozen_at})")
    print(f"outcome horizon: {result.outcome_horizon_minutes}m ({result.outcome_window_bars} bars)")
    print(f"status: {result.status.value}")
    if result.error_message:
        print(f"error_message: {result.error_message}")
    print(f"signal_count: {result.signal_count}")
    print()
    if result.results:
        for window in result.results.windows:
            print(
                f"window={window.window_bars} bars  signals={window.signal_count}  win_rate="
                f"{window.win_rate}  mean_return={window.mean_return}  mean_mfe={window.mean_mfe}  "
                f"mean_mae={window.mean_mae}"
            )
    if signals:
        print()
        print(f"first signal: {signals[0].signal_timestamp.isoformat()} entry={signals[0].entry_timestamp.isoformat()} "
              f"entry_price={signals[0].entry_price:.4f}")
        print(f"last signal:  {signals[-1].signal_timestamp.isoformat()}")
    print("=" * 100)


def main() -> None:
    args = parse_args()
    experiment_id = args.experiment_id
    if args.demo:
        experiment_id = _run_demo(args.db_path)

    result, signals = evaluate_oos(experiment_id, db_path=args.db_path)
    oos_evaluation_repository.save_evaluation(result, signals, db_path=args.db_path)

    from app.models.oos_evaluation import OOSEvaluationStatus

    if result.status == OOSEvaluationStatus.COMPLETED:
        experiment = research_repository.get_experiment(experiment_id, db_path=args.db_path)
        if experiment is not None and experiment.lifecycle_state == ExperimentLifecycleState.FROZEN:
            research_repository.mark_oos_evaluated(experiment_id, oos_evaluated_at=result.evaluated_at, db_path=args.db_path)

    print_report(result, signals)


if __name__ == "__main__":
    main()
