"""Persistent shapes for Research v1 (app/research/, app/storage/
research_repository.py, app/api/research.py): a falsifiable hypothesis
expressed as one or more FeatureConditions (ANDed -- when is a signal
true) and an Outcome (did the following period behave as predicted),
the individual ExperimentEvent observations that produced, and the
ExperimentResults aggregated from them.

v0.1.24 (Feature <-> Research integration): a Condition used to be a
free-standing metric/operator/threshold triple, hardcoding its own tiny
metric vocabulary ("{N}m_return" only, parsed by regex). It is now
FeatureCondition -- feature_id/operator/value(/value_max) -- where
`feature_id` references the Feature Engine's own canonical vocabulary
(app/features/vocabulary.py) instead of this module inventing a second,
parallel one. An Experiment now holds `conditions: list[FeatureCondition]`
(AND-combined -- no OR, no nesting; that's still out of v1's scope, the
same "no expression language yet" limit the original spec set, just
extended from exactly one condition to N ANDed ones) and
`feature_contract_version`, captured once at creation from
app.models.features.FEATURE_CONTRACT_VERSION -- see Experiment's own
docstring for what that guarantees. Outcome is UNCHANGED: measuring
"what happened after the signal" via forward_return over historical
bars was never something the Feature Engine's vocabulary needed to
own, and still isn't.

Scope, from the spec this was built against: no boolean composition
beyond AND, no multi-symbol universe (`symbol` is a single ticker, not
a list). See app/research/metrics.py for what "forward_return" actually
computes, and app/research/conditions.py for how a FeatureCondition is
evaluated against an already-computed FeatureRecord.

Kept a leaf module, like app/models/market_data.py and app/models/
features.py: this file imports only pydantic, the stdlib, and its own
sibling leaf model app.models.features (for FEATURE_CONTRACT_VERSION's
default value only -- never app.features, app.research, or app.api).
Scope checks that need those (symbol/timeframe against ALLOWED_SYMBOLS/
ALLOWED_TIMEFRAMES, whether a feature_id is a real, known feature, and
whether an operator is valid for that feature's type) live at the API
route (app/api/research.py), the same layer that already owns those
checks for the historical-data routes.
"""

import uuid
from datetime import date, datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.features import FEATURE_CONTRACT_VERSION


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
    """The five comparisons Outcome supports -- see
    app/research/conditions.py::evaluate() for where these are actually
    applied to a computed outcome value. NOT used by FeatureCondition
    below (see FeatureConditionOperator) -- Outcome's own operator
    vocabulary predates, and is intentionally left untouched by, the
    Feature <-> Research integration (v0.1.24): measuring a forward
    return was never part of what that integration scoped in."""

    LT = "<"
    LTE = "<="
    EQ = "=="
    GTE = ">="
    GT = ">"


class Outcome(BaseModel):
    """v1's only supported outcome metric is "forward_return": the
    return from the signal to `horizon_minutes` later. `horizon_minutes`
    stands in for the spec's "horizon: 60 minutes" -- spelled out in
    minutes so the unit is never ambiguous at a call site. Unchanged by
    v0.1.24 -- see the module docstring."""

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


class FeatureConditionOperator(str, Enum):
    """The operator vocabulary requirement 4 asks for: `>` `<` `>=`
    `<=` `=` `between` for a NUMERIC feature, `=` only for a BOOLEAN one
    (see app/features/vocabulary.py's NUMERIC_OPERATORS/
    BOOLEAN_OPERATORS -- the same six values, defined once there and
    mirrored here as the enum FeatureCondition.operator is actually
    typed against). Deliberately `"="`, not `"=="` -- a different,
    parallel vocabulary from ConditionOperator above (Outcome's), not a
    shared one: unifying them would mean either Outcome silently
    gaining "between" (never asked for) or FeatureCondition silently
    accepting "==" (not what this integration's spec asked for either).
    """

    LT = "<"
    LTE = "<="
    EQ = "="
    GTE = ">="
    GT = ">"
    BETWEEN = "between"


class FeatureCondition(BaseModel):
    """v0.1.24's replacement for the old Condition: `feature_id`
    references an entry in app/features/vocabulary.py's
    FEATURE_VOCABULARY (checked at the API route, not here -- see the
    module docstring) rather than this module inventing its own metric
    name. `value` is the comparison target for every operator except
    "between", where it is the LOWER bound and `value_max` the upper
    (inclusive on both ends -- see app/research/conditions.py::
    evaluate_feature_conditions()). A boolean feature always compares
    with `value` as `True`/`False` and operator "=" -- see
    app/features/vocabulary.py's BOOLEAN_OPERATORS.

    Structural shape only is validated here (between needs value_max,
    every other operator must not have one) -- NOT whether `feature_id`
    is a real, known feature, and NOT whether `operator` is one this
    particular feature's type actually supports: both need
    app/features/vocabulary.py, which this leaf module does not import
    (see the module docstring) -- see app/api/research.py's
    _validate_request() for where those two checks actually happen.
    """

    feature_id: str
    operator: FeatureConditionOperator
    value: float | bool
    value_max: float | None = None

    @model_validator(mode="after")
    def _between_shape(self) -> "FeatureCondition":
        if self.operator == FeatureConditionOperator.BETWEEN:
            if isinstance(self.value, bool):
                raise ValueError("operator 'between' requires a numeric value, not a boolean.")
            if self.value_max is None:
                raise ValueError(
                    "operator 'between' requires value_max (value is the lower bound, value_max the upper)."
                )
            if self.value_max <= self.value:
                raise ValueError(f"value_max ({self.value_max}) must be greater than value ({self.value}) for 'between'.")
        elif self.value_max is not None:
            raise ValueError(f"value_max is only valid with operator 'between', not {self.operator.value!r}.")
        return self


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
    or derived once, deterministically, from the same bar/feature series
    the experiment ran against (condition_values, outcome_value,
    success) -- nothing here is re-derived differently by a later
    reader.

    `condition_values` (v0.1.24, replacing the old single
    `condition_value: float`) is feature_id -> the actual observed
    value for EVERY condition that fired, not just one -- an experiment
    can have more than one ANDed FeatureCondition now, and dropping
    down to a single number would silently discard which of several
    conditions contributed what.

    `signal_price` is the close of the bar AT the signal timestamp --
    the price available at the moment the condition became true, since
    a bar's close is the last price known as of that bar's timestamp
    (see app/research/metrics.py::forward_return(), which uses close
    prices throughout for the same reason).
    """

    experiment_id: str
    symbol: str
    signal_timestamp: datetime
    signal_price: float
    condition_values: dict[str, float | bool]
    outcome_timestamp: datetime
    outcome_price: float
    outcome_value: float
    success: bool


class ExperimentCreateRequest(BaseModel):
    """Body of POST /research/experiments -- everything about an
    experiment except its id/status/timestamps/feature_contract_version,
    which the server assigns (see Experiment.new()). `provider` is
    required, not defaulted, matching this app's existing convention
    (see app/api/historical_storage.py's STORED-READ route): the
    storage layer's identity key includes provider, so reading "TSLA
    data" without saying which provider's saved copy would be
    ambiguous.

    `conditions` (v0.1.24, replacing the old singular `condition`) must
    have at least one entry -- an experiment with zero conditions has
    no falsifiable hypothesis to test at all.
    """

    name: str
    hypothesis: str
    symbol: str
    start_date: date
    end_date: date
    timeframe: str
    provider: str
    conditions: list[FeatureCondition] = Field(min_length=1)
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
    conditions/outcome/feature_contract_version are immutable after
    Experiment.new().

    `feature_contract_version` (v0.1.24) is requirement 6's
    reproducibility guarantee: captured once, at creation, from
    app.models.features.FEATURE_CONTRACT_VERSION -- the Feature Engine
    contract version in effect right now. A run (app/api/research.py)
    only evaluates conditions against FeatureRecords whose OWN
    `feature_contract_version` matches this stored value; a
    FeatureRecord computed under a later (formula-changed) contract
    version is treated the same as a missing one -- skipped, not
    silently blended in -- so a future feature-formula change can never
    silently alter what an already-created experiment measures. Bumping
    FEATURE_CONTRACT_VERSION and recomputing features under the new
    contract makes old experiments simply find no (version-matching)
    data until they are duplicated (see the frontend's existing
    "duplicate experiment" flow) against the new contract on purpose.
    """

    id: str
    name: str
    hypothesis: str
    symbol: str
    start_date: date
    end_date: date
    timeframe: str
    provider: str
    conditions: list[FeatureCondition] = Field(min_length=1)
    outcome: Outcome
    feature_contract_version: str
    status: ExperimentStatus
    created_at: datetime
    completed_at: datetime | None = None
    results: ExperimentResults | None = None
    error_message: str | None = None

    @classmethod
    def new(cls, request: ExperimentCreateRequest) -> "Experiment":
        """Assigns the server-owned fields (id/status/created_at/
        feature_contract_version) for a brand-new DRAFT experiment.
        Scope validation (symbol/timeframe/date-range checks, and
        v0.1.24's feature_id/operator-vs-feature-type checks) happens
        at the API route BEFORE this is called -- see
        app/api/research.py -- so any Experiment this produces is
        already known-runnable."""
        return cls(
            id=str(uuid.uuid4()),
            name=request.name,
            hypothesis=request.hypothesis,
            symbol=request.symbol.upper(),
            start_date=request.start_date,
            end_date=request.end_date,
            timeframe=request.timeframe,
            provider=request.provider,
            conditions=request.conditions,
            outcome=request.outcome,
            feature_contract_version=FEATURE_CONTRACT_VERSION,
            status=ExperimentStatus.DRAFT,
            created_at=datetime.now(timezone.utc),
        )


class ExperimentEventsResponse(BaseModel):
    """The full response body of GET /research/experiments/{id}/events
    -- the individual observations (spec section 5), not just a count."""

    experiment_id: str
    event_count: int
    events: list[ExperimentEvent]
