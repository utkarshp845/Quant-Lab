"""Persistent shapes for Research v1 (app/research/, app/storage/
research_repository.py, app/api/research.py): a falsifiable hypothesis
expressed as a Condition (when is a signal true) and an Outcome (did
the following period behave as predicted), the individual
ExperimentEvent observations that produced, and the ExperimentResults
aggregated from them.

Scope, from the spec this was built against: ONE condition, ONE
outcome, per experiment -- no boolean composition of multiple
conditions, no expression language, no multi-symbol universe (`symbol`
is a single ticker, not a list). See app/research/metrics.py for what
"{N}m_return" and "forward_return" actually compute.

Kept a leaf module, like app/models/market_data.py and app/models/
validation.py: this file imports only pydantic, the stdlib, and its
own sibling models -- never app.research or app.api -- so nothing
importing app.models.research takes on research-engine or HTTP-layer
dependencies it does not need. Scope checks that need those (symbol/
timeframe against ALLOWED_SYMBOLS/ALLOWED_TIMEFRAMES, whether a
metric's window lands on a whole bar) live at the API route
(app/api/research.py), the same layer that already owns those checks
for the historical-data routes.
"""

import re
import uuid
from datetime import date, datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator

# Mirrors app/research/metrics.py's own trailing-return regex exactly
# (duplicated, not imported, to keep this module a dependency-free leaf
# -- see the module docstring) -- both must accept precisely the same
# "{N}m_return" shape, since metrics.parse_trailing_return_metric() is
# what actually evaluates whatever a Condition here allowed through.
_TRAILING_RETURN_METRIC_RE = re.compile(r"^(\d+)m_return$")


class ExperimentStatus(str, Enum):
    """An experiment's lifecycle. DRAFT is set by Experiment.new() and
    never re-entered; RUNNING is set the instant POST .../run starts
    executing; COMPLETED/FAILED are the two terminal states a run can
    land in -- see app/api/research.py::run() for exactly what
    distinguishes them (a raised exception during execution -> FAILED,
    everything else -> COMPLETED, even a run that found zero
    qualifying signals)."""

    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ConditionOperator(str, Enum):
    """The five comparisons v1 supports for both Condition and Outcome
    -- see app/research/conditions.py::evaluate() for where these are
    actually applied to a computed metric value."""

    LT = "<"
    LTE = "<="
    EQ = "=="
    GTE = ">="
    GT = ">"


class Condition(BaseModel):
    """v1's only supported shape: metric/operator/threshold -- see the
    module docstring's "no expression language yet" note. `metric` must
    be "{N}m_return" (e.g. "30m_return"): a trailing N-minute return
    ending at the observation being evaluated. See
    app/research/metrics.py::trailing_return() for how that is computed
    and why it never uses a bar past the observation itself.
    """

    metric: str
    operator: ConditionOperator
    threshold: float

    @field_validator("metric")
    @classmethod
    def _metric_is_a_trailing_return(cls, value: str) -> str:
        if not _TRAILING_RETURN_METRIC_RE.match(value):
            raise ValueError(
                f"Unsupported condition metric {value!r}. Expected the form '<minutes>m_return', e.g. '30m_return'."
            )
        return value


class Outcome(BaseModel):
    """v1's only supported outcome metric is "forward_return": the
    return from the signal to `horizon_minutes` later. `horizon_minutes`
    stands in for the spec's "horizon: 60 minutes" -- spelled out in
    minutes so the unit is never ambiguous at a call site."""

    metric: str
    horizon_minutes: int = Field(gt=0)
    operator: ConditionOperator
    threshold: float

    @field_validator("metric")
    @classmethod
    def _metric_is_forward_return(cls, value: str) -> str:
        if value != "forward_return":
            raise ValueError(f"Unsupported outcome metric {value!r}. v1 supports only 'forward_return'.")
        return value


class ExperimentResults(BaseModel):
    """Aggregate statistics over an experiment's events (app/research/
    aggregation.py). Every numeric field is Optional and None -- never
    0.0, never NaN -- when there are zero events (nothing to average),
    and std_dev_outcome is specifically None for exactly one event too
    (a sample standard deviation of one observation is undefined, not
    zero) -- see aggregation.py::MIN_OBSERVATIONS_FOR_STDEV. None is
    this app's existing convention for "cannot honestly compute this",
    the same rule app/ingestion/value_parsing.py applies elsewhere.
    """

    total_events: int
    successful_events: int
    failed_events: int
    success_rate: float | None
    average_outcome: float | None
    median_outcome: float | None
    min_outcome: float | None
    max_outcome: float | None
    std_dev_outcome: float | None


class ExperimentEvent(BaseModel):
    """One qualifying signal and its measured outcome -- the spec's "do
    not only store aggregate statistics" requirement, in code. Every
    field here is either observed directly (signal_price, outcome_price)
    or derived once, deterministically, from the same bar series the
    experiment ran against (condition_value, outcome_value, success) --
    nothing here is re-derived differently by a later reader.

    `signal_price` is the close of the bar AT the signal timestamp --
    the price available at the moment the condition became true, since
    a bar's close is the last price known as of that bar's timestamp
    (see app/research/metrics.py::trailing_return(), which uses close
    prices throughout for the same reason).
    """

    experiment_id: str
    symbol: str
    signal_timestamp: datetime
    signal_price: float
    condition_value: float
    outcome_timestamp: datetime
    outcome_price: float
    outcome_value: float
    success: bool


class ExperimentCreateRequest(BaseModel):
    """Body of POST /research/experiments -- everything about an
    experiment except its id/status/timestamps, which the server
    assigns (see Experiment.new()). `provider` is required, not
    defaulted, matching this app's existing convention (see
    app/api/historical_storage.py's STORED-READ route): the storage
    layer's identity key includes provider, so reading "TSLA data"
    without saying which provider's saved copy would be ambiguous.
    """

    name: str
    hypothesis: str
    symbol: str
    start_date: date
    end_date: date
    timeframe: str
    provider: str
    condition: Condition
    outcome: Outcome


class Experiment(BaseModel):
    """The persisted experiment record (app/storage/
    research_repository.py). Every field below except
    status/completed_at/results/error_message is set once, at creation,
    and never changes again -- that is what "a completed experiment
    must preserve the exact parameters used to produce its results"
    (spec section 8) means in code: re-running an experiment (POST
    .../run) only ever updates status/completed_at/results/
    error_message; symbol/start_date/end_date/timeframe/provider/
    condition/outcome are immutable after Experiment.new().
    """

    id: str
    name: str
    hypothesis: str
    symbol: str
    start_date: date
    end_date: date
    timeframe: str
    provider: str
    condition: Condition
    outcome: Outcome
    status: ExperimentStatus
    created_at: datetime
    completed_at: datetime | None = None
    results: ExperimentResults | None = None
    error_message: str | None = None

    @classmethod
    def new(cls, request: ExperimentCreateRequest) -> "Experiment":
        """Assigns the server-owned fields (id/status/created_at) for a
        brand-new DRAFT experiment. Scope validation (symbol/timeframe/
        date-range/metric-window checks) happens at the API route
        BEFORE this is called -- see app/api/research.py -- so any
        Experiment this produces is already known-runnable."""
        return cls(
            id=str(uuid.uuid4()),
            name=request.name,
            hypothesis=request.hypothesis,
            symbol=request.symbol.upper(),
            start_date=request.start_date,
            end_date=request.end_date,
            timeframe=request.timeframe,
            provider=request.provider,
            condition=request.condition,
            outcome=request.outcome,
            status=ExperimentStatus.DRAFT,
            created_at=datetime.now(timezone.utc),
        )


class ExperimentEventsResponse(BaseModel):
    """The full response body of GET /research/experiments/{id}/events
    -- the individual observations (spec section 5), not just a count."""

    experiment_id: str
    event_count: int
    events: list[ExperimentEvent]
