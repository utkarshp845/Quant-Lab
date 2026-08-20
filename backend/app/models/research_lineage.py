"""Output shape for the "why did this event qualify?" lineage view
(spec section 12): RAW MARKET DATA -> SESSION CLASSIFICATION -> FEATURE
CALCULATION -> CONDITION EVALUATION -> EVENT DETECTION -> SIGNAL
TIMESTAMP/PRICE -> FORWARD BARS -> OUTCOME, assembled from
already-persisted rows only -- no value here is recomputed, only
looked up and bundled. app/api/research_lineage.py is the only writer
of this shape (a read-only route; this module owns no table).
"""

from datetime import datetime

from pydantic import BaseModel

from app.models.features import FeatureRecord
from app.models.market_data import HistoricalBar


class LineageConditionEvaluation(BaseModel):
    """One of this event's ANDed FeatureConditions, alongside the
    actual observed value that made it fire (from ExperimentEvent.
    condition_values, app/models/research.py) and the feature's own
    human-readable name/description (app/features/vocabulary.py) --
    "distance below VWAP / ATR" instead of a bare feature_id, per spec
    section 12's worked example."""

    feature_id: str
    feature_name: str
    feature_description: str
    operator: str
    value: float | bool
    value_max: float | None
    observed_value: float | bool


class EventLineage(BaseModel):
    """Everything real, already-persisted data can show about why one
    qualifying event fired and what happened after it. `signal_bar`/
    `outcome_bar` are RAW market data (HistoricalBar); `feature_record`
    is DERIVED (computed by the Feature Engine, never itself raw) --
    kept as clearly separate fields, never merged into one blob, per
    spec section 12's "never make derived values look like raw market
    data" rule. Either bar (or the feature record) can legitimately be
    `None` if the underlying row was deleted/never persisted after the
    event itself was recorded -- shown as missing, never fabricated.
    """

    experiment_id: str
    symbol: str
    timeframe: str
    signal_timestamp: datetime
    signal_bar: HistoricalBar | None
    feature_record: FeatureRecord | None
    condition_evaluations: list[LineageConditionEvaluation]
    outcome_timestamp: datetime
    outcome_bar: HistoricalBar | None
    outcome_value: float
    success: bool
