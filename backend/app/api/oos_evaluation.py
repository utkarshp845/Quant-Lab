"""API route for OOS Evaluation v1:

    POST /research/experiments/{id}/oos-evaluate   run the frozen hypothesis against holdout data
    GET  /research/experiments/{id}/oos-evaluations  every evaluation ever run for this experiment
    GET  /research/oos-evaluations/{evaluation_id}     one evaluation
    GET  /research/oos-evaluations/{evaluation_id}/signals   its individual OOS signals

The POST route takes NO body -- requirement 8's own instruction:
"the request must not allow callers to provide new conditions,
thresholds, feature versions, horizons, date ranges, symbols, or
providers. All of those must come from the frozen experiment and
linked OOS partition." There is nothing FastAPI could even parse a
request body INTO here (no Pydantic request model is declared, no
`request: ...` parameter exists on the handler) -- a caller supplying a
JSON body gets it silently ignored by FastAPI, never read by this
route or app.oos_evaluation.engine.evaluate_oos(), which itself takes
only `experiment_id` (and `market_context_symbols`, supplied by THIS
route, never by the caller).

Reads app.oos_evaluation.engine (the orchestrator -- holdout access,
Feature Engine, Backtesting v1, all reused unmodified there) and
app.storage.oos_evaluation_repository (append-only persistence). Never
writes to `historical_bars`/`historical_features`/`backtests`/
`experiment_freeze_snapshots` -- this operation consumes those, it
never modifies any of them (requirement: "the OOS operation must NOT
modify the frozen hypothesis").
"""

from fastapi import APIRouter, HTTPException

from app.api.features import MARKET_CONTEXT_SYMBOLS
from app.models.oos_evaluation import OOSEvaluationResult, OOSEvaluationStatus, OOSSignal
from app.models.research import ExperimentLifecycleState
from app.oos_evaluation.engine import (
    ExperimentNotFoundForEvaluationError,
    IncompleteProvenanceError,
    InvalidLifecycleForEvaluationError,
    NoLinkedPartitionError,
    PartitionNotFoundForEvaluationError,
    evaluate_oos,
)
from app.research.lifecycle import PartitionLinkageError
from app.storage import oos_evaluation_repository, research_repository

router = APIRouter()


@router.post("/research/experiments/{experiment_id}/oos-evaluate", response_model=OOSEvaluationResult)
def run_oos_evaluation(experiment_id: str) -> OOSEvaluationResult:
    """Runs (requirement 8's endpoint). Precondition failures
    (requirement 1's reject list) are mapped to specific status codes
    and persist NOTHING -- no holdout data was ever touched. Once the
    pipeline actually runs, BOTH a COMPLETED and a FAILED
    OOSEvaluationResult are persisted (see app.oos_evaluation.engine.
    evaluate_oos()'s own docstring for why a pipeline-stage failure is
    a normal, recorded outcome, not an HTTP error) -- only a COMPLETED
    result advances the lifecycle (requirement 7): FROZEN ->
    OOS_EVALUATED, and ONLY when the experiment was still exactly
    FROZEN going into this call (a re-run of an already-OOS_EVALUATED
    experiment leaves lifecycle_state untouched, matching
    app/research/lifecycle.py's own state table, which has no
    OOS_EVALUATED -> OOS_EVALUATED transition to attempt)."""
    try:
        result, signals = evaluate_oos(experiment_id, market_context_symbols=MARKET_CONTEXT_SYMBOLS)
    except ExperimentNotFoundForEvaluationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PartitionNotFoundForEvaluationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidLifecycleForEvaluationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (IncompleteProvenanceError, NoLinkedPartitionError, PartitionLinkageError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    oos_evaluation_repository.save_evaluation(result, signals)

    if result.status == OOSEvaluationStatus.COMPLETED:
        experiment = research_repository.get_experiment(experiment_id)
        if experiment is not None and experiment.lifecycle_state == ExperimentLifecycleState.FROZEN:
            research_repository.mark_oos_evaluated(experiment_id, oos_evaluated_at=result.evaluated_at)

    return result


@router.get("/research/experiments/{experiment_id}/oos-evaluations", response_model=list[OOSEvaluationResult])
def list_oos_evaluations(experiment_id: str) -> list[OOSEvaluationResult]:
    if research_repository.get_experiment(experiment_id) is None:
        raise HTTPException(status_code=404, detail=f"No experiment with id {experiment_id!r}")
    return oos_evaluation_repository.list_evaluations(experiment_id)


@router.get("/research/oos-evaluations/{evaluation_id}", response_model=OOSEvaluationResult)
def get_oos_evaluation(evaluation_id: str) -> OOSEvaluationResult:
    evaluation = oos_evaluation_repository.get_evaluation(evaluation_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail=f"No OOS evaluation with id {evaluation_id!r}")
    return evaluation


@router.get("/research/oos-evaluations/{evaluation_id}/signals", response_model=list[OOSSignal])
def get_oos_evaluation_signals(evaluation_id: str) -> list[OOSSignal]:
    if oos_evaluation_repository.get_evaluation(evaluation_id) is None:
        raise HTTPException(status_code=404, detail=f"No OOS evaluation with id {evaluation_id!r}")
    return oos_evaluation_repository.get_signals(evaluation_id)
