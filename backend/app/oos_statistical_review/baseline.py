"""OOS-scoped unconditional baseline construction for OOS Statistical
Review V1 (app/oos_statistical_review/): "what does a forward return
from ANY eligible bar look like, ignoring the frozen research condition
entirely" -- computed over the SAME OOS holdout bars a given COMPLETED
evaluation already used, NEVER development-era bars (this feature's
own explicit instruction: "the baseline must be constructed from the
SAME OOS time ranges, not from development data").

Mirrors app/oos_evaluation/engine.py::_run_pipeline()'s own bar/feature
pipeline EXACTLY (bounded development-side warm-up via
app.oos_evaluation.warmup.warmup_range(), the sole holdout access path
app.oos.access.get_holdout_bars(..., confirm_oos_validation_use=True),
app.features.engine.compute_features() over warm-up+holdout bars
together, then discarding every warm-up-only record) -- reused
verbatim, not re-derived -- with app.statistical_validation.baseline.
CONTROL_CONDITION (reused UNMODIFIED) run through
app.backtesting.engine.run_backtest() (reused UNMODIFIED) in place of
the frozen hypothesis' own conditions. This is the OOS-layer
counterpart of app.statistical_validation.baseline.
compute_unconditional_baseline(), which does the identical job one
layer down, over DEVELOPMENT-era bars -- neither function is modified
by the other's existence.

Never computes market context (spy_bars/qqq_bars/market_context_symbols
are always empty) -- CONTROL_CONDITION (`volume.volume >= 0`) never
references a market-context feature, so there is nothing for that
extra computation to feed, matching app.statistical_validation.baseline.
compute_unconditional_baseline()'s own identical omission.

FAILS CLOSED (raises BaselineConstructionError), never silently
substitutes development data or an empty baseline: if the partition
backing a COMPLETED evaluation can no longer be found, no longer
matches that evaluation's own recorded symbol/timeframe/provider, or
its holdout bars can no longer be read.
"""

from pathlib import Path

from app.backtesting.engine import run_backtest
from app.features.engine import compute_features
from app.models.backtesting import BacktestSignal
from app.models.oos_evaluation import OOSEvaluationResult
from app.oos.access import get_holdout_bars
from app.oos_evaluation.warmup import warmup_range
from app.statistical_validation.baseline import CONTROL_CONDITION
from app.storage import historical_bar_repository, oos_partition_repository


class BaselineConstructionError(ValueError):
    """Raised when the OOS-scoped unconditional baseline cannot be
    safely constructed for a given COMPLETED evaluation -- see the
    module docstring. Never silently falls back to development data or
    an empty baseline; app/oos_statistical_review/engine.py propagates
    this unchanged (it is already a specific, actionable error), and
    app/api/oos_statistical_review.py maps it to a 4xx."""


def compute_oos_unconditional_baseline(
    evaluation: OOSEvaluationResult, *, db_path: str | Path | None = None
) -> list[BacktestSignal]:
    """The unconditional (CONTROL_CONDITION) population over exactly
    the holdout bars `evaluation` itself was run against -- the SAME
    partition, the SAME warm-up-bounding rule, the SAME feature
    contract. Every returned BacktestSignal's own outcomes are
    computed purely from HOLDOUT bars (warm-up bars are read only to
    give the Feature Engine legitimate trailing context at the first
    holdout bar, exactly as app/oos_evaluation/engine.py's own pipeline
    already establishes -- never fed into run_backtest()'s own `bars`
    argument, so a warm-up bar can structurally never become a baseline
    observation either).
    """
    partition = oos_partition_repository.get_partition(evaluation.oos_partition_id, db_path=db_path)
    if partition is None:
        raise BaselineConstructionError(
            f"OOS partition {evaluation.oos_partition_id!r} (evaluation {evaluation.id!r}) no longer exists -- "
            "cannot safely construct an OOS-scoped baseline for it."
        )
    if (partition.symbol, partition.timeframe, partition.provider) != (
        evaluation.symbol,
        evaluation.timeframe,
        evaluation.provider,
    ):
        raise BaselineConstructionError(
            f"OOS partition {partition.id!r} ({partition.symbol}/{partition.timeframe}/{partition.provider}) no "
            f"longer matches evaluation {evaluation.id!r}'s own recorded symbol/timeframe/provider "
            f"({evaluation.symbol}/{evaluation.timeframe}/{evaluation.provider}) -- refusing to construct a "
            "baseline against mismatched data."
        )
    if evaluation.outcome_window_bars is None:
        raise BaselineConstructionError(
            f"Evaluation {evaluation.id!r} has no outcome_window_bars recorded -- cannot construct a baseline "
            "at an undefined horizon."
        )

    warmup_bars = []
    bounds = warmup_range(
        holdout_start=partition.holdout_start,
        development_start=partition.development_start,
        development_end=partition.development_end,
        timeframe=partition.timeframe,
    )
    if bounds is not None:
        warmup_start, warmup_end = bounds
        warmup_bars = historical_bar_repository.get_bars_in_range(
            symbol=partition.symbol, timeframe=partition.timeframe, provider=partition.provider,
            start=warmup_start, end=warmup_end, db_path=db_path,
        )

    holdout_bars = get_holdout_bars(partition, confirm_oos_validation_use=True, db_path=db_path)
    if not holdout_bars:
        raise BaselineConstructionError(
            f"OOS partition {partition.id!r} (evaluation {evaluation.id!r}) has no holdout bars -- cannot "
            "safely construct an OOS-scoped baseline for it."
        )
    holdout_timestamps = {bar.timestamp for bar in holdout_bars}

    all_records = compute_features(
        symbol=partition.symbol, timeframe=partition.timeframe, provider=partition.provider,
        bars=warmup_bars + holdout_bars, calculated_at=evaluation.evaluated_at,
        spy_bars=[], qqq_bars=[], market_context_symbols=frozenset(),
    )
    holdout_feature_records = [record for record in all_records if record.timestamp in holdout_timestamps]

    signals, _results = run_backtest(
        backtest_id=f"oos-statistical-review-baseline-{evaluation.id}",
        experiment_id=evaluation.experiment_id,
        symbol=partition.symbol,
        timeframe=partition.timeframe,
        conditions=CONTROL_CONDITION,
        windows=[evaluation.outcome_window_bars],
        bars=holdout_bars,  # NEVER warmup_bars -- see the module docstring
        feature_records=holdout_feature_records,
        feature_contract_version=evaluation.feature_contract_version,
    )
    return signals
