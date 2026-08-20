"""API route for the "why did this event qualify?" lineage view (spec
section 12):

    GET /research/experiments/{id}/lineage?signal_timestamp=<iso8601>

Read-only assembly of already-persisted rows -- the signal bar and
outcome bar (app.storage.historical_bar_repository, unmodified), the
feature record at the signal timestamp (app.storage.feature_repository,
unmodified), and the event's own condition_values (app.storage.
research_repository, unmodified) -- annotated with each fired
condition's human-readable name/description
(app.features.vocabulary.get_feature_definition(), unmodified). No
value is recomputed; a missing bar/feature record is reported as
`null`, never guessed.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.features.vocabulary import get_feature_definition
from app.models.research_lineage import EventLineage, LineageConditionEvaluation
from app.storage import feature_repository, historical_bar_repository, research_repository

router = APIRouter()


def _single_bar(*, symbol: str, timeframe: str, provider: str, timestamp: datetime):
    bars = historical_bar_repository.get_bars_in_range(
        symbol=symbol, timeframe=timeframe, provider=provider, start=timestamp, end=timestamp
    )
    return bars[0] if bars else None


def _single_feature_record(*, symbol: str, timeframe: str, provider: str, timestamp: datetime):
    records = feature_repository.get_features(
        symbol=symbol, timeframe=timeframe, provider=provider, start=timestamp.date(), end=timestamp.date()
    )
    for record in records:
        if record.timestamp == timestamp:
            return record
    return None


@router.get("/research/experiments/{experiment_id}/lineage", response_model=EventLineage)
def get_event_lineage(experiment_id: str, signal_timestamp: datetime = Query(...)) -> EventLineage:
    experiment = research_repository.get_experiment(experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail=f"No experiment with id {experiment_id!r}")

    events = research_repository.get_events(experiment_id)
    event = next((e for e in events if e.signal_timestamp == signal_timestamp), None)
    if event is None:
        raise HTTPException(
            status_code=404,
            detail=f"No event at signal_timestamp={signal_timestamp.isoformat()!r} for experiment {experiment_id!r}. "
            "Timestamps must match an existing event exactly -- see GET .../events for the real list.",
        )

    signal_bar = _single_bar(
        symbol=experiment.symbol, timeframe=experiment.timeframe, provider=experiment.provider,
        timestamp=event.signal_timestamp,
    )
    outcome_bar = _single_bar(
        symbol=experiment.symbol, timeframe=experiment.timeframe, provider=experiment.provider,
        timestamp=event.outcome_timestamp,
    )
    feature_record = _single_feature_record(
        symbol=experiment.symbol, timeframe=experiment.timeframe, provider=experiment.provider,
        timestamp=event.signal_timestamp,
    )

    condition_evaluations = []
    for condition in experiment.conditions:
        observed = event.condition_values.get(condition.feature_id)
        if observed is None:
            continue  # should not happen for a real, persisted qualifying event -- defensive, not assumed
        definition = get_feature_definition(condition.feature_id)
        condition_evaluations.append(
            LineageConditionEvaluation(
                feature_id=condition.feature_id,
                feature_name=definition.name,
                feature_description=definition.description,
                operator=condition.operator.value,
                value=condition.value,
                value_max=condition.value_max,
                observed_value=observed,
            )
        )

    return EventLineage(
        experiment_id=experiment_id,
        symbol=experiment.symbol,
        timeframe=experiment.timeframe,
        signal_timestamp=event.signal_timestamp,
        signal_bar=signal_bar,
        feature_record=feature_record,
        condition_evaluations=condition_evaluations,
        outcome_timestamp=event.outcome_timestamp,
        outcome_bar=outcome_bar,
        outcome_value=event.outcome_value,
        success=event.success,
    )
