"""API routes for OOS Evidence Accumulation V1 (app/oos_evidence/):

    POST /research/experiments/{id}/oos-periods                             register an additional OOS partition as an evaluation period
    GET  /research/experiments/{id}/oos-periods                             every OOS period registered for this experiment
    POST /research/experiments/{id}/oos-periods/{oos_partition_id}/evaluate  run (once) this period's own OOS evaluation
    GET  /research/experiments/{id}/oos-evidence                             aggregated evidence across every completed period

`GET /research/experiments/{id}/oos-evaluations` (app/api/oos_evaluation.py,
UNMODIFIED) already returns every evaluation ever run for this
experiment -- both the originally frozen-time-linked partition's own
run(s) and every additional period's own run, since both write into
the SAME append-only `oos_evaluations` table -- so no second "list
evaluations" route is added here.

The register route's body is the single, minimal field this feature
needs (which already-created, independent OOSPartition -- via the
existing, unmodified POST /oos/partitions -- to register); the
evaluate route takes NO body at all, matching app/api/oos_evaluation.py's
own POST .../oos-evaluate exactly: every research-defining fact still
comes from the frozen snapshot, never the caller, and nothing here can
ever modify the frozen hypothesis, an ExperimentFreezeSnapshot, or a
prior OOSEvaluationResult.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.api.features import MARKET_CONTEXT_SYMBOLS
from app.models.oos_evaluation import OOSEvaluationResult, OOSEvaluationStatus
from app.models.oos_evidence import OOSEvidenceSummary, OOSPeriod, OOSPeriodLinkRequest
from app.models.research import ExperimentLifecycleState
from app.oos_evidence.aggregation import build_evidence_summary
from app.oos_evidence.evaluation import (
    ExperimentNotFoundForEvaluationError,
    IncompleteProvenanceError,
    InvalidLifecycleForEvaluationError,
    PartitionNotFoundForEvaluationError,
    PeriodAlreadyEvaluatedError,
    PeriodNotRegisteredError,
    evaluate_oos_period,
)
from app.oos_evidence.period import OOSPeriodLinkageError, validate_new_period
from app.research.lifecycle import PartitionLinkageError, validate_snapshot_partition_linkage
from app.storage import (
    experiment_freeze_repository,
    oos_evaluation_repository,
    oos_evidence_repository,
    oos_partition_repository,
    research_repository,
)

router = APIRouter()

_REGISTRABLE_LIFECYCLE_STATES = frozenset(
    {ExperimentLifecycleState.FROZEN, ExperimentLifecycleState.OOS_EVALUATED}
)


def _get_frozen_snapshot_or_error(experiment_id: str):
    """Requirement: OOS periods may only be registered for an
    ALREADY-FROZEN experiment (FROZEN or already OOS_EVALUATED -- the
    identical lifecycle-state set OOS Evaluation v1 itself accepts for
    evaluation, app/oos_evaluation/engine.py's own
    `_EVALUABLE_LIFECYCLE_STATES`). A DRAFT experiment has no frozen
    hypothesis yet to accumulate evidence FOR; an ARCHIVED one is done
    -- new evidence is never added to it."""
    experiment = research_repository.get_experiment(experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail=f"No experiment with id {experiment_id!r}")
    if experiment.lifecycle_state not in _REGISTRABLE_LIFECYCLE_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"Experiment {experiment_id!r} is {experiment.lifecycle_state.value!r} -- OOS periods can only "
            "be registered for a FROZEN (or already OOS_EVALUATED) experiment.",
        )
    snapshot = experiment_freeze_repository.get_snapshot(experiment_id)
    if snapshot is None:
        raise HTTPException(status_code=409, detail=f"Experiment {experiment_id!r} has not been frozen yet.")
    return snapshot


@router.post("/research/experiments/{experiment_id}/oos-periods", response_model=OOSPeriod)
def register_oos_period(experiment_id: str, request: OOSPeriodLinkRequest) -> OOSPeriod:
    """Registers `request.oos_partition_id` (an already-created,
    independent OOSPartition) as an additional OOS evaluation period
    for `experiment_id`. Validated in two layers, both against the
    IMMUTABLE ExperimentFreezeSnapshot, never the live Experiment row:

      1. validate_snapshot_partition_linkage() (app/research/
         lifecycle.py, reused UNMODIFIED -- the SAME check OOS
         Evaluation v1 itself applies before ever evaluating a
         partition): symbol/timeframe/provider compatible, and the
         experiment's own development range entirely contained within
         the new partition's development window (which structurally
         implies the new partition's holdout window starts strictly
         after the experiment's own development range ends -- see
         app/oos_evidence/period.py's own module docstring for why).
      2. validate_new_period() (app/oos_evidence/period.py, this
         feature's own addition): no overlap/touch/contamination
         against any OOS period (or the experiment's originally
         frozen-time-linked partition) already registered for this
         SAME experiment, and no duplicate registration of the same
         partition.

    Nothing here creates, mutates, or even reads a single bar of
    market data -- registration is boundary validation only, exactly
    like app/oos/partition.py's own partition-definition checks.
    """
    snapshot = _get_frozen_snapshot_or_error(experiment_id)

    new_partition = oos_partition_repository.get_partition(request.oos_partition_id)
    if new_partition is None:
        raise HTTPException(status_code=404, detail=f"No OOS partition with id {request.oos_partition_id!r}")

    try:
        validate_snapshot_partition_linkage(snapshot, new_partition)
    except PartitionLinkageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    already_registered_partitions = []
    if snapshot.oos_partition_id is not None:
        original_partition = oos_partition_repository.get_partition(snapshot.oos_partition_id)
        if original_partition is not None:
            already_registered_partitions.append(original_partition)
    already_registered_partitions.extend(
        partition
        for partition in (
            oos_partition_repository.get_partition(period.oos_partition_id)
            for period in oos_evidence_repository.list_periods(experiment_id)
        )
        if partition is not None
    )

    try:
        validate_new_period(
            snapshot=snapshot, new_partition=new_partition, already_registered_partitions=already_registered_partitions
        )
    except OOSPeriodLinkageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    period = OOSPeriod(
        id=new_partition.id,
        experiment_id=experiment_id,
        oos_partition_id=new_partition.id,
        symbol=new_partition.symbol,
        timeframe=new_partition.timeframe,
        provider=new_partition.provider,
        oos_start=new_partition.holdout_start,
        oos_end=new_partition.holdout_end,
        label=new_partition.label,
        registered_at=datetime.now(timezone.utc),
    )
    oos_evidence_repository.save_period(period)
    return oos_evidence_repository.get_period(experiment_id, new_partition.id)


@router.get("/research/experiments/{experiment_id}/oos-periods", response_model=list[OOSPeriod])
def list_oos_periods(experiment_id: str) -> list[OOSPeriod]:
    if research_repository.get_experiment(experiment_id) is None:
        raise HTTPException(status_code=404, detail=f"No experiment with id {experiment_id!r}")
    return oos_evidence_repository.list_periods(experiment_id)


@router.post(
    "/research/experiments/{experiment_id}/oos-periods/{oos_partition_id}/evaluate",
    response_model=OOSEvaluationResult,
)
def evaluate_oos_period_route(experiment_id: str, oos_partition_id: str) -> OOSEvaluationResult:
    """Runs (exactly once per period, ever -- see
    app/oos_evidence/evaluation.py's own docstring) the SAME OOS
    Evaluation v1 pipeline against `oos_partition_id`'s holdout data.
    Both a COMPLETED and a FAILED OOSEvaluationResult are persisted
    (app/oos_evaluation/engine.py's own "a pipeline failure is a
    normal, recorded outcome" rule, reused unmodified) -- only a
    COMPLETED result advances the lifecycle, and ONLY FROZEN ->
    OOS_EVALUATED, and ONLY when the experiment was still exactly
    FROZEN going into this call: an experiment already OOS_EVALUATED
    (from this period, an earlier one, or its own original partition)
    stays exactly OOS_EVALUATED -- no OOS_EVALUATED -> FROZEN or
    OOS_EVALUATED -> DRAFT transition is ever attempted, matching
    app/research/lifecycle.py's own state table, which has no such
    transitions to attempt in the first place."""
    try:
        result, signals = evaluate_oos_period(
            experiment_id, oos_partition_id, market_context_symbols=MARKET_CONTEXT_SYMBOLS
        )
    except ExperimentNotFoundForEvaluationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PartitionNotFoundForEvaluationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PeriodNotRegisteredError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidLifecycleForEvaluationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PeriodAlreadyEvaluatedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (IncompleteProvenanceError, PartitionLinkageError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    oos_evaluation_repository.save_evaluation(result, signals)

    if result.status == OOSEvaluationStatus.COMPLETED:
        experiment = research_repository.get_experiment(experiment_id)
        if experiment is not None and experiment.lifecycle_state == ExperimentLifecycleState.FROZEN:
            research_repository.mark_oos_evaluated(experiment_id, oos_evaluated_at=result.evaluated_at)

    return result


@router.get("/research/experiments/{experiment_id}/oos-evidence", response_model=OOSEvidenceSummary)
def get_oos_evidence(experiment_id: str) -> OOSEvidenceSummary:
    """Read-only, aggregated evidence across every COMPLETED OOS
    evaluation ever run for this experiment (app/oos_evidence/
    aggregation.py -- see that module's own docstring for exactly what
    is, and is not, computed). Never writes anything -- this route
    only reads app.storage.oos_evaluation_repository (UNMODIFIED,
    append-only) and app.storage.experiment_freeze_repository
    (UNMODIFIED, immutable)."""
    experiment = research_repository.get_experiment(experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail=f"No experiment with id {experiment_id!r}")
    snapshot = experiment_freeze_repository.get_snapshot(experiment_id)
    if snapshot is None:
        raise HTTPException(status_code=409, detail=f"Experiment {experiment_id!r} has not been frozen yet.")

    evaluations = oos_evaluation_repository.list_evaluations(experiment_id)
    signals_by_evaluation = {
        evaluation.id: oos_evaluation_repository.get_signals(evaluation.id)
        for evaluation in evaluations
        if evaluation.status == OOSEvaluationStatus.COMPLETED
    }
    return build_evidence_summary(
        experiment_id=experiment_id,
        hypothesis_hash=snapshot.hypothesis_hash,
        evaluations=evaluations,
        signals_by_evaluation=signals_by_evaluation,
    )
