"""API routes for Backtesting v1: select an existing Research Experiment
-> run an event-based historical backtest against its already-persisted
bars/features -> inspect the individual signals and aggregate results.

    POST /backtests                    create a DRAFT backtest referencing an existing experiment
    POST /backtests/{id}/run           execute it against stored bars + features
    GET  /backtests                    every saved backtest, newest first (optionally filtered by experiment_id)
    GET  /backtests/{id}               one backtest (with results, once completed)
    GET  /backtests/{id}/signals       every individual signal, not just the aggregate

Reads the referenced Experiment (app.storage.research_repository) ONLY
to copy its symbol/timeframe/provider/date-range/conditions/
feature_contract_version -- never writes to `experiments` or
`experiment_events`, and never redefines a condition of its own (see
app/models/backtesting.py's module docstring: "Select an existing
Research experiment" is the entire hypothesis-definition step).
Otherwise mirrors app/api/research.py's own boundaries exactly: reads
ONLY from app.storage.historical_bar_repository (bars) and
app.storage.feature_repository (already-computed FeatureRecords), never
app.features.engine (features arrive pre-computed), and never writes to
`historical_bars`/`historical_features` at all.

RUN's exception handling is identical to app/api/research.py::run()'s
own: a run failing is caught, persisted as FAILED with its message, and
returned as a normal 200 -- not raised as a 500. Only "does this id
exist at all" (the backtest's own, or the experiment it references)
raises HTTPException.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.backtesting.engine import run_backtest
from app.models.backtesting import (
    Backtest,
    BacktestCreateRequest,
    BacktestSignalsResponse,
    BacktestStatus,
)
from app.storage import backtest_repository, feature_repository, historical_bar_repository, research_repository

router = APIRouter()


@router.post("/backtests", response_model=Backtest)
def create_backtest(request: BacktestCreateRequest) -> Backtest:
    experiment = research_repository.get_experiment(request.experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail=f"No experiment with id {request.experiment_id!r}")

    backtest = Backtest.new(
        experiment_id=experiment.id,
        symbol=experiment.symbol,
        timeframe=experiment.timeframe,
        provider=experiment.provider,
        windows=request.windows,
        feature_contract_version=experiment.feature_contract_version,
    )
    backtest_repository.save_backtest(backtest)
    return backtest


@router.get("/backtests", response_model=list[Backtest])
def get_all_backtests(experiment_id: str | None = None) -> list[Backtest]:
    return backtest_repository.list_backtests(experiment_id=experiment_id)


@router.get("/backtests/{backtest_id}", response_model=Backtest)
def get_backtest(backtest_id: str) -> Backtest:
    backtest = backtest_repository.get_backtest(backtest_id)
    if backtest is None:
        raise HTTPException(status_code=404, detail=f"No backtest with id {backtest_id!r}")
    return backtest


@router.get("/backtests/{backtest_id}/signals", response_model=BacktestSignalsResponse)
def get_backtest_signals(backtest_id: str) -> BacktestSignalsResponse:
    backtest = backtest_repository.get_backtest(backtest_id)
    if backtest is None:
        raise HTTPException(status_code=404, detail=f"No backtest with id {backtest_id!r}")

    signals = backtest_repository.get_signals(backtest_id)
    return BacktestSignalsResponse(backtest_id=backtest_id, signal_count=len(signals), signals=signals)


@router.post("/backtests/{backtest_id}/run", response_model=Backtest)
def run_backtest_route(backtest_id: str) -> Backtest:
    """Executes (or re-executes) a backtest. Safe to call more than once
    on the same id -- see backtest_repository.replace_signals() -- so
    re-running the same backtest against the same, unchanged dataset
    always produces the same signals and results.

    Re-fetches the referenced Experiment at RUN time (not just at
    create time) purely to read its `start_date`/`end_date` (the
    date-range bars/features are queried over) and `conditions` (what
    actually gets evaluated) -- every other field this route needs
    (symbol/timeframe/provider/feature_contract_version) was already
    captured onto the Backtest itself at creation, so a run never
    depends on the experiment's mutable fields (it has none -- see
    Experiment's own docstring) changing between create and run.
    """
    backtest = backtest_repository.get_backtest(backtest_id)
    if backtest is None:
        raise HTTPException(status_code=404, detail=f"No backtest with id {backtest_id!r}")

    experiment = research_repository.get_experiment(backtest.experiment_id)
    if experiment is None:
        raise HTTPException(
            status_code=404, detail=f"Referenced experiment {backtest.experiment_id!r} no longer exists"
        )

    backtest_repository.mark_running(backtest.id)

    try:
        bars = historical_bar_repository.get_bars(
            symbol=experiment.symbol,
            timeframe=experiment.timeframe,
            provider=experiment.provider,
            start=experiment.start_date,
            end=experiment.end_date,
        )
        feature_records = feature_repository.get_features(
            symbol=experiment.symbol,
            timeframe=experiment.timeframe,
            provider=experiment.provider,
            start=experiment.start_date,
            end=experiment.end_date,
        )
        signals, results = run_backtest(
            backtest_id=backtest.id,
            experiment_id=experiment.id,
            symbol=backtest.symbol,
            timeframe=backtest.timeframe,
            conditions=experiment.conditions,
            windows=backtest.windows,
            bars=bars,
            feature_records=feature_records,
            feature_contract_version=backtest.feature_contract_version,
        )
    except Exception as exc:  # noqa: BLE001 -- see module docstring: a run failing is a persisted FAILED status, not a 500
        backtest_repository.update_backtest_run(
            backtest.id,
            status=BacktestStatus.FAILED,
            completed_at=datetime.now(timezone.utc),
            results=None,
            error_message=str(exc),
        )
        return backtest_repository.get_backtest(backtest.id)

    backtest_repository.replace_signals(backtest.id, signals)
    backtest_repository.update_backtest_run(
        backtest.id,
        status=BacktestStatus.COMPLETED,
        completed_at=datetime.now(timezone.utc),
        results=results,
        error_message=None,
    )
    return backtest_repository.get_backtest(backtest.id)
