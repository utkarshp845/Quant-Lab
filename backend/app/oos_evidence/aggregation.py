"""OOS Evidence Accumulation V1's read model (app/oos_evidence/): turns
every COMPLETED OOS evaluation ever run for a frozen experiment --
across every OOS period, the experiment's originally frozen-time-linked
partition included -- into one OOSEvidenceSummary (app/models/
oos_evidence.py). Pure: takes already-fetched evaluations/signals, no
I/O of its own (app/api/oos_evidence.py is the only caller, and does
the fetching via app.storage.oos_evaluation_repository, itself
UNMODIFIED).

Reuses, never re-derives:
  - app.backtesting.aggregation.aggregate_results() for the pooled RAW
    mean/median/win_rate/std_dev_return/mean_mfe/mean_mae -- the SAME
    function Backtesting v1 and (indirectly, via BacktestResults)
    OOS Evaluation v1 already use for one run's own signals, called
    here across every completed evaluation's signals, pooled. OOSSignal
    (app/models/oos_evaluation.py) mirrors BacktestSignal's shape
    closely enough (both carry `.outcomes: list[BacktestWindowOutcome]`,
    the only field aggregate_results() actually reads) that no adapter
    or duplicate implementation is needed.
  - app.statistical_validation.episodes.group_into_episodes() for the
    independent-episode count -- the SAME non-overlapping-consecutive-
    bar grouping rule Statistical Validation V1 already established,
    applied PER EVALUATION and summed. NEVER applied across two
    different evaluations' pooled signals: two OOS periods are, by
    construction (app/oos_evidence/period.py::validate_new_period()),
    never adjacent in time to one another the way two consecutive bars
    of the SAME holdout window are -- grouping across periods would
    silently invent a fictitious "episode" spanning two genuinely
    independent windows, exactly the mistake this feature's own
    instructions forbid ("do not simply pool all raw signals and
    pretend they are independent").
  - app.research.metrics.timeframe_minutes() to get each evaluation's
    own bar interval for episode grouping -- the identical call
    app/statistical_validation/engine.py already makes for the same
    purpose.

NOT computed here, on purpose (this feature's own explicit
instruction: "do not perform formal statistical significance testing
in V1 -- that belongs to the later OOS Statistical Review step"): no
p-value, confidence interval, standard error, or significant/not-
significant verdict.
"""

from datetime import timedelta

from app.backtesting.aggregation import aggregate_results
from app.models.backtesting import BacktestResults
from app.models.oos_evaluation import OOSEvaluationResult, OOSEvaluationStatus, OOSSignal
from app.models.oos_evidence import OOSEvidencePeriodResult, OOSEvidenceSummary
from app.research.metrics import timeframe_minutes
from app.statistical_validation.episodes import group_into_episodes


def build_evidence_summary(
    *,
    experiment_id: str,
    hypothesis_hash: str,
    evaluations: list[OOSEvaluationResult],
    signals_by_evaluation: dict[str, list[OOSSignal]],
) -> OOSEvidenceSummary:
    """`evaluations` is every OOSEvaluationResult ever run for this
    experiment (app/storage/oos_evaluation_repository.py::
    list_evaluations()'s own, already-append-only, full history --
    COMPLETED and FAILED alike; this function does the COMPLETED-only
    filtering itself, so a FAILED row is still visible via
    `failed_evaluation_count` without a second query).
    `signals_by_evaluation` is evaluation_id -> its own OOSSignal rows
    (app/storage/oos_evaluation_repository.py::get_signals()) -- the
    caller only needs to have fetched this for COMPLETED evaluations;
    a missing key is treated as "zero signals", never an error (a
    COMPLETED evaluation legitimately has zero signals sometimes -- see
    app/models/oos_evaluation.py::OOSEvaluationStatus's own docstring).
    `hypothesis_hash` is read from the caller's own
    ExperimentFreezeSnapshot, never re-derived from an evaluation row
    (every COMPLETED evaluation's own `hypothesis_hash` is structurally
    guaranteed to already equal it -- see app/oos_evaluation/engine.py's
    own pipeline -- but this function trusts the snapshot as the one
    source of truth for the hypothesis, on principle, matching every
    other module in this feature)."""
    completed = [e for e in evaluations if e.status == OOSEvaluationStatus.COMPLETED]
    failed_count = sum(1 for e in evaluations if e.status == OOSEvaluationStatus.FAILED)

    per_period_results: list[OOSEvidencePeriodResult] = []
    all_signals: list[OOSSignal] = []
    total_episodes = 0
    outcome_window_bars: int | None = None

    for evaluation in completed:
        signals = signals_by_evaluation.get(evaluation.id, [])
        all_signals.extend(signals)
        if outcome_window_bars is None:
            # Structurally identical across every evaluation of the
            # SAME experiment (derived once, deterministically, from
            # the immutable snapshot's own outcome.horizon_minutes +
            # timeframe -- see app/oos_evaluation/engine.py) -- read
            # off the first completed evaluation encountered, not
            # re-derived or asserted equal across the rest, matching
            # this feature's own "trust structural invariants
            # established elsewhere" convention (e.g. app/research/
            # lifecycle.py::validate_snapshot_partition_linkage()'s own
            # docstring makes the identical judgment).
            outcome_window_bars = evaluation.outcome_window_bars

        bar_interval = timedelta(minutes=timeframe_minutes(evaluation.timeframe))
        episodes = group_into_episodes(signals, bar_interval=bar_interval)
        total_episodes += len(episodes)

        per_period_results.append(
            OOSEvidencePeriodResult(
                evaluation_id=evaluation.id,
                oos_partition_id=evaluation.oos_partition_id,
                oos_start=evaluation.holdout_start,
                oos_end=evaluation.holdout_end,
                status=evaluation.status,
                signal_count=evaluation.signal_count,
                episode_count=len(episodes),
                results=evaluation.results,
                evaluated_at=evaluation.evaluated_at,
            )
        )

    distinct_periods = {evaluation.oos_partition_id for evaluation in completed}

    pooled_window = None
    if outcome_window_bars is not None:
        pooled: BacktestResults = aggregate_results(all_signals, windows=[outcome_window_bars])
        pooled_window = pooled.windows[0]

    earliest_start = min((e.holdout_start for e in completed), default=None)
    latest_end = max((e.holdout_end for e in completed), default=None)

    return OOSEvidenceSummary(
        experiment_id=experiment_id,
        hypothesis_hash=hypothesis_hash,
        oos_period_count=len(distinct_periods),
        completed_evaluation_count=len(completed),
        failed_evaluation_count=failed_count,
        total_raw_signals=len(all_signals),
        total_independent_episodes=total_episodes,
        mean_return=pooled_window.mean_return if pooled_window else None,
        median_return=pooled_window.median_return if pooled_window else None,
        win_rate=pooled_window.win_rate if pooled_window else None,
        std_dev_return=pooled_window.std_dev_return if pooled_window else None,
        mean_mfe=pooled_window.mean_mfe if pooled_window else None,
        mean_mae=pooled_window.mean_mae if pooled_window else None,
        earliest_oos_start=earliest_start,
        latest_oos_end=latest_end,
        per_period_results=per_period_results,
    )
