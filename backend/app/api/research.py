"""API routes for Research v1: define -> run -> inspect a falsifiable
hypothesis against the existing normalized historical bar dataset.

    POST /research/experiments               create a DRAFT experiment
    POST /research/experiments/{id}/run       execute it against stored bars
    GET  /research/experiments                every saved experiment, newest first
    GET  /research/experiments/{id}           one experiment (with results, once completed)
    GET  /research/experiments/{id}/events    the individual signal observations

Reads ONLY from app.storage.historical_bar_repository -- the existing
normalized historical dataset -- never app.storage.raw_ingestion_repository
(the audit/reprocessing layer) and never writes to `historical_bars` at
all: Research must never modify historical data (spec section 8).

RUN's exception handling deliberately differs from every other route in
this app (contrast app/api/historical_data.py's exception mapping,
which turns failures into HTTP status codes for a request that never
produced a persisted resource): here, a run failing is itself a normal,
already-scoped-for lifecycle outcome -- FAILED is one of the four
statuses the spec asks Experiment to support -- so an unexpected error
during execution is caught, persisted as FAILED with its message on the
experiment record, and returned as a normal 200 with that status, not
raised as a 500. Only the "does this experiment id exist at all" check
raises HTTPException, the same as every other route's not-found case.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.api.historical_data import ALLOWED_SYMBOLS, ALLOWED_TIMEFRAMES
from app.models.research import (
    Experiment,
    ExperimentCreateRequest,
    ExperimentEventsResponse,
    ExperimentStatus,
)
from app.research import metrics
from app.research.engine import run_experiment
from app.storage import historical_bar_repository, research_repository

router = APIRouter()


def _validate_request(request: ExperimentCreateRequest) -> None:
    """Everything checkable before an Experiment object even exists --
    the same "validate at the route, against the same ALLOWED_* sets
    every other route already uses" convention as
    app/api/historical_data.py / app/api/historical_storage.py, plus
    the two v1-specific checks the metric windows themselves require
    (see app/research/metrics.py::bars_for_window() -- a window that
    is not a whole multiple of the experiment's own timeframe has no
    honest answer and must be rejected here, not silently rounded)."""
    symbol = request.symbol.upper()
    if symbol not in ALLOWED_SYMBOLS:
        raise HTTPException(
            status_code=400, detail=f"Symbol {symbol!r} is not supported yet. Allowed: {sorted(ALLOWED_SYMBOLS)}"
        )
    if request.timeframe not in ALLOWED_TIMEFRAMES:
        raise HTTPException(
            status_code=400, detail=f"Unsupported timeframe {request.timeframe!r}. Allowed: {sorted(ALLOWED_TIMEFRAMES)}"
        )
    if request.end_date < request.start_date:
        raise HTTPException(status_code=400, detail="end_date must not be before start_date")

    try:
        window_minutes = metrics.parse_trailing_return_metric(request.condition.metric)
        metrics.bars_for_window(window_minutes, request.timeframe)
        metrics.bars_for_window(request.outcome.horizon_minutes, request.timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/research/experiments", response_model=Experiment)
def create_experiment(request: ExperimentCreateRequest) -> Experiment:
    _validate_request(request)
    experiment = Experiment.new(request)
    research_repository.save_experiment(experiment)
    return experiment


@router.get("/research/experiments", response_model=list[Experiment])
def get_all_experiments() -> list[Experiment]:
    return research_repository.list_experiments()


@router.get("/research/experiments/{experiment_id}", response_model=Experiment)
def get_experiment(experiment_id: str) -> Experiment:
    experiment = research_repository.get_experiment(experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail=f"No experiment with id {experiment_id!r}")
    return experiment


@router.get("/research/experiments/{experiment_id}/events", response_model=ExperimentEventsResponse)
def get_experiment_events(experiment_id: str) -> ExperimentEventsResponse:
    experiment = research_repository.get_experiment(experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail=f"No experiment with id {experiment_id!r}")

    events = research_repository.get_events(experiment_id)
    return ExperimentEventsResponse(experiment_id=experiment_id, event_count=len(events), events=events)


@router.post("/research/experiments/{experiment_id}/run", response_model=Experiment)
def run_experiment_route(experiment_id: str) -> Experiment:
    """Executes (or re-executes) an experiment. Safe to call more than
    once on the same id -- see app/storage/research_repository.py's
    replace_events() -- so re-running the same experiment against the
    same, unchanged dataset always produces the same events and results
    (spec completion criterion 10)."""
    experiment = research_repository.get_experiment(experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail=f"No experiment with id {experiment_id!r}")

    research_repository.mark_running(experiment.id)

    try:
        bars = historical_bar_repository.get_bars(
            symbol=experiment.symbol,
            timeframe=experiment.timeframe,
            provider=experiment.provider,
            start=experiment.start_date,
            end=experiment.end_date,
        )
        events, results = run_experiment(
            experiment_id=experiment.id,
            symbol=experiment.symbol,
            timeframe=experiment.timeframe,
            condition=experiment.condition,
            outcome=experiment.outcome,
            bars=bars,
        )
    except Exception as exc:  # noqa: BLE001 -- see module docstring: a run failing is a persisted FAILED status, not a 500
        research_repository.update_experiment_run(
            experiment.id,
            status=ExperimentStatus.FAILED,
            completed_at=datetime.now(timezone.utc),
            results=None,
            error_message=str(exc),
        )
        return research_repository.get_experiment(experiment.id)

    research_repository.replace_events(experiment.id, events)
    research_repository.update_experiment_run(
        experiment.id,
        status=ExperimentStatus.COMPLETED,
        completed_at=datetime.now(timezone.utc),
        results=results,
        error_message=None,
    )
    return research_repository.get_experiment(experiment.id)
