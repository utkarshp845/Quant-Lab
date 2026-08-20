"""API route for the pipeline-status aggregation (app/research/
pipeline_status.py):

    GET /research/experiments/{id}/pipeline-status

The only route in this app whose entire job is reading FROM several
other features' repositories (Research, Backtesting, OOS Evidence, OOS
Evaluation, Research Notebook) and handing already-loaded objects to a
pure function -- it writes nothing anywhere, and none of the modules it
reads from are modified by its existence.
"""

from fastapi import APIRouter, HTTPException

from app.models.pipeline_status import PipelineStatusResponse
from app.research.pipeline_status import build_pipeline_status
from app.storage import (
    backtest_repository,
    feature_repository,
    historical_bar_repository,
    oos_evaluation_repository,
    oos_evidence_repository,
    research_notebook_repository,
    research_repository,
)

router = APIRouter()


@router.get("/research/experiments/{experiment_id}/pipeline-status", response_model=PipelineStatusResponse)
def get_pipeline_status(experiment_id: str) -> PipelineStatusResponse:
    experiment = research_repository.get_experiment(experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail=f"No experiment with id {experiment_id!r}")

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
    decisions = (
        research_notebook_repository.list_decisions(experiment.design_group_id) if experiment.design_group_id else []
    )
    backtests = backtest_repository.list_backtests(experiment_id=experiment_id)
    oos_evaluations = oos_evaluation_repository.list_evaluations(experiment_id)
    oos_periods = oos_evidence_repository.list_periods(experiment_id)
    conclusions = research_notebook_repository.list_conclusions(experiment_id)

    return build_pipeline_status(
        experiment,
        bars_count=len(bars),
        features_count=len(feature_records),
        decisions=decisions,
        backtests=backtests,
        oos_evaluations=oos_evaluations,
        oos_period_count=len(oos_periods),
        conclusions=conclusions,
    )
