"""OOS Evaluation v1's orchestrator (app/oos_evaluation/): given a
FROZEN (or already OOS_EVALUATED, for a re-run -- see requirement 6)
experiment linked to an OOS partition, runs its frozen hypothesis
against the partition's HOLDOUT data and returns an OOSEvaluationResult
+ its OOSSignal rows. Persistence and the FROZEN -> OOS_EVALUATED
lifecycle transition are NOT this module's job -- see
app/api/oos_evaluation.py, the only caller.

EXACT PIPELINE (frozen experiment -> holdout data -> features ->
conditions -> backtest -> result):

  1. Load the live Experiment (only for its CURRENT lifecycle_state --
     every research-defining fact comes from the snapshot, never this
     row -- see requirement 2) and the immutable ExperimentFreezeSnapshot
     (app/models/experiment_freeze.py).
  2. Load the linked OOSPartition (app/storage/oos_partition_repository.py)
     and re-validate symbol/timeframe/provider compatibility and
     development-range containment against the SNAPSHOT's own fields
     (app/research/lifecycle.py::validate_snapshot_partition_linkage()
     -- reused, not reimplemented).
  3. Compute a bounded, DEVELOPMENT-side warm-up range
     (app/oos_evaluation/warmup.py -- pure, no I/O) and read it via
     app.storage.historical_bar_repository.get_bars_in_range()
     (general-purpose, unrestricted for development data, exactly as
     app/oos/access.py::get_development_bars() itself already is).
  4. Read the partition's holdout bars via app.oos.access.get_holdout_bars(
     partition, confirm_oos_validation_use=True) -- THE SOLE holdout
     access path (requirement: "the holdout access boundary must remain
     the authoritative data-access mechanism"). No other function in
     this file, or anywhere this module calls into, reads
     `historical_bars` for the experiment's own symbol/timeframe/
     provider by any other route.
  5. Compute features (app.features.engine.compute_features(), THE
     UNMODIFIED Feature Engine) over warm-up-bars + holdout-bars
     TOGETHER, in that chronological order, under the frozen
     feature_contract_version -- then DISCARD every resulting
     FeatureRecord whose timestamp is not one of the holdout bars'
     own. This is the ENTIRE warm-up mechanism -- see "WARM-UP
     HANDLING" below.
  6. Evaluate the frozen conditions and run Backtesting v1's own
     next-bar-open/multi-window semantics (app.backtesting.engine.
     run_backtest(), THE UNMODIFIED Backtesting v1 engine) with
     `bars` = holdout bars ONLY (never warm-up bars) and
     `feature_records` = the holdout-only records from step 5. Because
     `bars` never contains a warm-up bar, a signal, an entry, or an
     outcome can structurally never land on one -- see run_backtest()'s
     own no-look-ahead guarantees, entirely unmodified here.
  7. Wrap the resulting BacktestSignal rows into OOSSignal (app/models/
     oos_evaluation.py -- a relabeling only, zero new math) and the
     BacktestResults into the returned OOSEvaluationResult, alongside
     every provenance field requirement 2 asks this operation to
     record.

WARM-UP HANDLING, precisely:

  compute_features() emits exactly one FeatureRecord per bar in the
  list it is given, in order (app/features/engine.py's own, unmodified
  guarantee) -- there is no way to ask it for "only some indices'
  records" or to seed it with a starting internal state; every
  trailing-window feature (return_Nm, realized_volatility, ATR, SMA20/
  SMA50, VWAP/intraday-range-position -- see app/features/{price,
  volatility,price_position}.py) independently recomputes its OWN
  window by walking BACKWARD through the `bars` list it was given, up
  to that feature's own fixed window size, using
  app/features/timeframes.py::is_contiguous_window() to refuse to
  compute across any gap. Concretely: `bars = warmup_bars + holdout_bars`
  is passed to compute_features() ONCE; the resulting FeatureRecord
  list has one entry per bar in THAT combined list, in the same order.
  Every record whose `.timestamp` is NOT one of holdout_bars' own
  timestamps -- i.e., every record computed at a warm-up bar -- is then
  filtered out before anything downstream (condition evaluation,
  run_backtest()) ever sees it. The record AT the first holdout bar,
  however, legitimately has real, non-look-ahead trailing history
  available to it (the warm-up bars immediately preceding it, all
  strictly DEVELOPMENT-side, all strictly BEFORE holdout_start) -- so
  its return_60m/ATR/realized_volatility/SMA20/SMA50 features are
  populated exactly as they would be for ANY bar with real history
  behind it, not spuriously None just because it happens to be the
  holdout period's first bar.

  volatility_ratio/volatility_percentile specifically need up to 252
  TRADING SESSIONS of history (app/features/volatility.py::
  VOLATILITY_HISTORY_SESSIONS) -- app/oos_evaluation/warmup.py's own
  docstring documents in detail why that specific requirement is NOT
  warmed up (reading a full trading year of development data for two
  features would not be "minimum required context"): those two
  features may legitimately read None for some span into the holdout
  period. This is the Feature Engine's own EXISTING "insufficient
  history yet" behavior (unmodified), simply still visible here because
  warm-up is deliberately bounded rather than unlimited -- and a
  FeatureCondition referencing either feature, evaluated against a None
  value, is never satisfied (app/research/conditions.py::
  evaluate_feature_conditions(), unmodified) -- it never produces a
  signal, it never silently defaults to true or false.

  NO FUTURE HOLDOUT DATA IS EVER USED TO INITIALIZE A FEATURE: warm-up
  bars are read from strictly BEFORE holdout_start, and never beyond
  the partition's own declared development window either
  (app/oos_evaluation/warmup.py::warmup_range()'s own
  `end = min(holdout_start - 1 microsecond, development_end)` -- audit
  finding, 2026-08-18: this used to be unconditionally
  `holdout_start - 1 microsecond`, which could read an undeclared gap
  between development_end and holdout_start if a partition left one;
  see warmup_range()'s own docstring for the confirmed, fixed case)
  -- there is no code path in this module that reads a bar timestamped
  at or after holdout_start except through get_holdout_bars() itself.
  compute_features() itself never looks FORWARD past the index it is
  currently computing (app/features/engine.py's own, unmodified
  no-look-ahead guarantee) -- so even the record at a LATER holdout bar
  can never influence the record at an EARLIER one, warm-up or not.

Reuses, never modifies:
  - app.oos.access.get_holdout_bars()/get_development_bars() -- unmodified.
  - app.oos.partition.classify_range() -- unmodified (via
    app.research.lifecycle.validate_snapshot_partition_linkage()).
  - app.features.engine.compute_features() -- unmodified.
  - app.backtesting.engine.run_backtest() -- unmodified (which itself
    reuses app.research.conditions.evaluate_feature_conditions()
    unmodified).
  - app.research.metrics.bars_for_window() -- to convert the frozen
    Outcome's single horizon_minutes into the ONE window run_backtest()
    is invoked with (never more than one -- requirement 4 forbids
    changing horizons, and the frozen Outcome only ever had one).
  - app.storage.research_repository/experiment_freeze_repository/
    oos_partition_repository/historical_bar_repository -- read-only.
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.backtesting.engine import run_backtest
from app.features.engine import compute_features
from app.models.experiment_freeze import ExperimentFreezeSnapshot
from app.models.oos_evaluation import OOSEvaluationResult, OOSEvaluationStatus, OOSSignal
from app.models.oos_partition import OOSPartition
from app.models.research import Experiment, ExperimentLifecycleState
from app.oos.access import get_holdout_bars
from app.oos_evaluation.warmup import warmup_range
from app.research.lifecycle import validate_snapshot_partition_linkage
from app.research.metrics import bars_for_window
from app.storage import experiment_freeze_repository, historical_bar_repository, oos_partition_repository, research_repository

# Reused, not re-derived -- see app/api/features.py's own module
# docstring for why this app decides market-context eligibility at the
# API layer, not inside a pure engine. This module accepts
# `market_context_symbols` as an explicit parameter (below) rather than
# importing app.api.features directly, matching every other engine in
# this app (app/research/engine.py, app/backtesting/engine.py,
# app/statistical_validation/engine.py) never importing from app.api.
SPY_SYMBOL = "SPY"
QQQ_SYMBOL = "QQQ"


class OOSEvaluationError(Exception):
    """Base class for every precondition failure this module raises
    BEFORE any holdout data is ever touched (requirement 1's reject
    list). app/api/oos_evaluation.py maps each subclass to a specific
    HTTP status; none of these ever result in a persisted
    OOSEvaluationResult row -- a precondition failure means the
    operation was never actually attempted against holdout data at
    all, so there is nothing yet worth recording (contrast an
    unexpected failure DURING the pipeline itself, which IS persisted
    as a FAILED result -- see evaluate_oos()'s own docstring)."""


class ExperimentNotFoundForEvaluationError(OOSEvaluationError):
    pass


class InvalidLifecycleForEvaluationError(OOSEvaluationError):
    pass


class IncompleteProvenanceError(OOSEvaluationError):
    pass


class NoLinkedPartitionError(OOSEvaluationError):
    pass


class PartitionNotFoundForEvaluationError(OOSEvaluationError):
    pass


# Lifecycle states this operation may run against -- requirement 1 says
# "lifecycle state == FROZEN", and requirement 6 separately requires
# supporting re-running the SAME frozen experiment more than once
# (producing a new, additional OOSEvaluationResult each time, never
# replacing a prior one). Both are satisfied by accepting FROZEN
# (first-ever evaluation) OR OOS_EVALUATED (a re-run of an experiment
# already past its first evaluation) here, while DRAFT and ARCHIVED
# remain explicitly rejected (requirement 1's own reject list) --
# app/api/oos_evaluation.py only calls
# research_repository.mark_oos_evaluated() when the CURRENT state is
# exactly FROZEN, so a re-run against an already-OOS_EVALUATED
# experiment never attempts the (invalid, per app/research/
# lifecycle.py's own state table) OOS_EVALUATED -> OOS_EVALUATED
# transition.
_EVALUABLE_LIFECYCLE_STATES = frozenset({ExperimentLifecycleState.FROZEN, ExperimentLifecycleState.OOS_EVALUATED})


def _check_preconditions(
    experiment_id: str, *, db_path: str | Path | None = None
) -> tuple[Experiment, ExperimentFreezeSnapshot, OOSPartition]:
    """Requirement 1, in full: raises a specific OOSEvaluationError
    subclass (or app.research.lifecycle.PartitionLinkageError) for
    every condition requirement 1 asks to reject, and returns the three
    objects the rest of the pipeline needs once every precondition
    holds. Nothing here reads a holdout bar -- this function runs
    entirely before step 3 of the module docstring's pipeline."""
    experiment = research_repository.get_experiment(experiment_id, db_path=db_path)
    if experiment is None:
        raise ExperimentNotFoundForEvaluationError(f"No experiment with id {experiment_id!r}")

    if experiment.lifecycle_state not in _EVALUABLE_LIFECYCLE_STATES:
        raise InvalidLifecycleForEvaluationError(
            f"Experiment {experiment_id!r} is {experiment.lifecycle_state.value!r} -- OOS evaluation requires "
            f"FROZEN (or OOS_EVALUATED, for a re-run). "
            + (
                "Freeze it first (POST .../freeze)."
                if experiment.lifecycle_state == ExperimentLifecycleState.DRAFT
                else "An ARCHIVED experiment can no longer be evaluated."
            )
        )

    snapshot = experiment_freeze_repository.get_snapshot(experiment_id, db_path=db_path)
    if snapshot is None or not snapshot.hypothesis_hash:
        # Should be structurally impossible (freeze_experiment() and
        # save_snapshot() are written together, in the same operation --
        # app/api/experiment_freeze.py) -- checked explicitly anyway,
        # per requirement 1's own "frozen hypothesis snapshot exists" /
        # "hypothesis_hash exists" bullets, rather than trusting that
        # invariant blindly.
        raise IncompleteProvenanceError(
            f"Experiment {experiment_id!r} has no complete frozen provenance (missing snapshot or hypothesis_hash) "
            "-- cannot determine what to evaluate."
        )

    if snapshot.oos_partition_id is None:
        raise NoLinkedPartitionError(f"Experiment {experiment_id!r} has no linked OOS partition to evaluate against.")

    partition = oos_partition_repository.get_partition(snapshot.oos_partition_id, db_path=db_path)
    if partition is None:
        raise PartitionNotFoundForEvaluationError(
            f"Experiment {experiment_id!r}'s linked OOS partition {snapshot.oos_partition_id!r} no longer exists."
        )

    validate_snapshot_partition_linkage(snapshot, partition)  # raises PartitionLinkageError

    return experiment, snapshot, partition


def _run_pipeline(
    snapshot: ExperimentFreezeSnapshot,
    partition: OOSPartition,
    *,
    market_context_symbols: frozenset[str] | set[str],
    db_path: str | Path | None,
) -> tuple[OOSEvaluationResult, list[OOSSignal]]:
    """Steps 3-7 of the module docstring's pipeline -- everything that
    actually touches bar/feature data. Any exception here is caught by
    evaluate_oos() and turned into a FAILED OOSEvaluationResult rather
    than propagating -- see that function's own docstring."""
    evaluation_id = str(uuid.uuid4())
    evaluated_at = datetime.now(timezone.utc)

    # Step 3: bounded, DEVELOPMENT-side warm-up (never holdout).
    warmup_bars = []
    bounds = warmup_range(
        holdout_start=partition.holdout_start,
        development_start=partition.development_start,
        development_end=partition.development_end,
        timeframe=snapshot.timeframe,
    )
    if bounds is not None:
        warmup_start, warmup_end = bounds
        warmup_bars = historical_bar_repository.get_bars_in_range(
            symbol=snapshot.symbol, timeframe=snapshot.timeframe, provider=snapshot.provider,
            start=warmup_start, end=warmup_end, db_path=db_path,
        )

    # Step 4: THE sole holdout access path.
    holdout_bars = get_holdout_bars(partition, confirm_oos_validation_use=True, db_path=db_path)
    holdout_timestamps = {bar.timestamp for bar in holdout_bars}

    # Step 5: Feature Engine over warm-up + holdout TOGETHER, then
    # discard every warm-up-only record -- see "WARM-UP HANDLING" above.
    is_market_context_eligible = snapshot.symbol in market_context_symbols
    spy_bars, qqq_bars = [], []
    if is_market_context_eligible:
        context_start = bounds[0] if bounds is not None else partition.holdout_start
        spy_bars = historical_bar_repository.get_bars_in_range(
            symbol=SPY_SYMBOL, timeframe=snapshot.timeframe, provider=snapshot.provider,
            start=context_start, end=partition.holdout_end, db_path=db_path,
        )
        qqq_bars = historical_bar_repository.get_bars_in_range(
            symbol=QQQ_SYMBOL, timeframe=snapshot.timeframe, provider=snapshot.provider,
            start=context_start, end=partition.holdout_end, db_path=db_path,
        )

    all_records = compute_features(
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        provider=snapshot.provider,
        bars=warmup_bars + holdout_bars,
        calculated_at=evaluated_at,
        spy_bars=spy_bars,
        qqq_bars=qqq_bars,
        market_context_symbols=market_context_symbols if is_market_context_eligible else frozenset(),
    )
    holdout_feature_records = [record for record in all_records if record.timestamp in holdout_timestamps]

    # Step 6: the frozen outcome's single horizon, converted to bars --
    # never more than one window, never a different horizon.
    outcome_window_bars = bars_for_window(snapshot.outcome.horizon_minutes, snapshot.timeframe)

    backtest_signals, backtest_results = run_backtest(
        backtest_id=evaluation_id,
        experiment_id=snapshot.experiment_id,
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        conditions=snapshot.conditions,
        windows=[outcome_window_bars],
        bars=holdout_bars,  # NEVER warmup_bars -- see the module docstring
        feature_records=holdout_feature_records,
        feature_contract_version=snapshot.feature_contract_version,
    )

    # Step 7: relabel BacktestSignal -> OOSSignal (zero new math).
    signals = [
        OOSSignal(
            evaluation_id=evaluation_id,
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            signal_timestamp=signal.signal_timestamp,
            entry_timestamp=signal.entry_timestamp,
            entry_price=signal.entry_price,
            feature_values=signal.feature_values,
            outcomes=signal.outcomes,
        )
        for signal in backtest_signals
    ]

    result = OOSEvaluationResult(
        id=evaluation_id,
        experiment_id=snapshot.experiment_id,
        hypothesis_hash=snapshot.hypothesis_hash,
        frozen_snapshot_id=snapshot.experiment_id,
        oos_partition_id=partition.id,
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        provider=snapshot.provider,
        holdout_start=partition.holdout_start,
        holdout_end=partition.holdout_end,
        feature_contract_version=snapshot.feature_contract_version,
        outcome_horizon_minutes=snapshot.outcome.horizon_minutes,
        outcome_window_bars=outcome_window_bars,
        signal_count=len(signals),
        results=backtest_results,
        status=OOSEvaluationStatus.COMPLETED,
        error_message=None,
        frozen_at=snapshot.frozen_at,
        evaluated_at=evaluated_at,
    )
    return result, signals


def evaluate_oos(
    experiment_id: str,
    *,
    market_context_symbols: frozenset[str] | set[str] = frozenset(),
    db_path: str | Path | None = None,
) -> tuple[OOSEvaluationResult, list[OOSSignal]]:
    """The single entry point: preconditions (requirement 1) are
    checked FIRST and raise immediately (OOSEvaluationError or
    PartitionLinkageError) -- app/api/oos_evaluation.py maps these to
    4xx responses and persists NOTHING, since no holdout data was ever
    touched.

    Once preconditions pass, the actual pipeline (steps 3-7) runs
    inside a try/except: an unexpected failure there is caught and
    returned as a FAILED OOSEvaluationResult (status=FAILED,
    error_message=str(exc), results=None, signal_count=0) with every
    identity/provenance field this module already knows (experiment_id,
    hypothesis_hash, frozen_snapshot_id, oos_partition_id, symbol/
    timeframe/provider, holdout_start/end, feature_contract_version,
    frozen_at) still populated -- requirement 7's "preserve the FROZEN
    state ... persist an error/status record if appropriate", mirroring
    app/api/research.py's/app/api/backtesting.py's own "a run failing
    is a normal, persisted FAILED status, not a 500" convention exactly.
    This function NEVER raises for a pipeline-stage failure -- only for
    a precondition one.
    """
    _experiment, snapshot, partition = _check_preconditions(experiment_id, db_path=db_path)

    try:
        return _run_pipeline(snapshot, partition, market_context_symbols=market_context_symbols, db_path=db_path)
    except Exception as exc:  # noqa: BLE001 -- see this function's own docstring: a pipeline failure is a persisted FAILED result, not a re-raise
        evaluated_at = datetime.now(timezone.utc)
        outcome_window_bars = None
        try:
            outcome_window_bars = bars_for_window(snapshot.outcome.horizon_minutes, snapshot.timeframe)
        except Exception:  # noqa: BLE001 -- best-effort only; the failure being reported is whatever `exc` above already is
            pass
        failed_result = OOSEvaluationResult(
            id=str(uuid.uuid4()),
            experiment_id=snapshot.experiment_id,
            hypothesis_hash=snapshot.hypothesis_hash,
            frozen_snapshot_id=snapshot.experiment_id,
            oos_partition_id=partition.id,
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            provider=snapshot.provider,
            holdout_start=partition.holdout_start,
            holdout_end=partition.holdout_end,
            feature_contract_version=snapshot.feature_contract_version,
            outcome_horizon_minutes=snapshot.outcome.horizon_minutes,
            outcome_window_bars=outcome_window_bars,
            signal_count=0,
            results=None,
            status=OOSEvaluationStatus.FAILED,
            error_message=str(exc),
            frozen_at=snapshot.frozen_at,
            evaluated_at=evaluated_at,
        )
        return failed_result, []
