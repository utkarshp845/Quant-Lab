"""Persistent shapes for Backtesting v1 (app/backtesting/, app/storage/
backtest_repository.py, app/api/backtesting.py): a Backtest walks an
EXISTING Research v1 Experiment's already-persisted bars/features
chronologically, forward-only, and answers exactly one question --
"when this research condition occurred historically, what happened
afterward?" -- at several configurable forward-looking horizons
(measured in BARS, not minutes -- see the module docstring on
app/backtesting/engine.py for why that is a deliberate, simpler unit
than Outcome.horizon_minutes).

Backtesting v1 is explicitly NOT a second, competing way to define a
hypothesis: `Backtest.experiment_id` references an already-created
`Experiment` (app/models/research.py) -- its `conditions` are what get
evaluated, its `feature_contract_version` is what a run's
FeatureRecords are matched against (the same reproducibility guarantee
Experiment itself already established) -- neither is redefined or
duplicated here. What Backtesting v1 adds on top is: (1) a strict
next-bar-open entry rule instead of measuring from the signal bar's own
close, (2) more than one forward horizon per run, evaluated
simultaneously, and (3) MFE/MAE (the best/worst paper excursion inside
the window), which Research's Outcome never computed at all.

One BacktestSignal per qualifying, ENTERABLE occurrence (a signal whose
condition fired AND that had a next bar to enter at -- see
BacktestSignal's own docstring), each carrying one BacktestWindowOutcome
per configured window that could actually be measured within the
dataset. BacktestResults aggregates across all signals, one
BacktestWindowResults per configured window -- a window with zero
measurable signals still appears in the response (unlike a signal that
was never created at all), with every statistic explicitly None, the
same "None, never a fabricated 0.0" convention app/models/research.py's
ExperimentResults already established.

Kept a leaf module: only pydantic, the stdlib, and app.models.research
(for FeatureCondition's typing convention, referenced only in
docstrings/back-links -- this module deliberately does NOT import
app.research or app.features, matching app/models/research.py's own
leaf-module discipline) are used here.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator

# The four forward windows the spec asks for, expressed in BARS (not
# minutes -- see app/backtesting/engine.py's module docstring). A
# BacktestCreateRequest may still ask for a different set (any positive,
# distinct bar counts), but this is what a caller gets if it leaves
# `windows` unset.
DEFAULT_WINDOWS: tuple[int, ...] = (5, 15, 30, 60)


class BacktestStatus(str, Enum):
    """Mirrors app/models/research.py::ExperimentStatus exactly -- same
    four-state lifecycle, same meaning of each state, applied to a
    Backtest run instead of an Experiment run."""

    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class BacktestWindowOutcome(BaseModel):
    """One signal's measured outcome at ONE forward window -- the
    per-horizon numbers app/research/metrics.py::forward_return() never
    computed (MFE/MAE) plus the same forward_return concept, now
    measured from the next-bar-open ENTRY price rather than the signal
    bar's own close (see BacktestSignal.entry_price).

    `forward_return` is close-to-entry: (outcome_bar.close -
    entry_price) / entry_price, where outcome_bar is entry_index +
    window_bars.

    `mfe` (Maximum Favorable Excursion) and `mae` (Maximum Adverse
    Excursion) are computed from every bar's HIGH/LOW (not just closes)
    across the entire entry-to-outcome window (inclusive of both ends):
    mfe = max((bar.high - entry_price) / entry_price), mae =
    min((bar.low - entry_price) / entry_price) -- signed, not absolute
    value, so mfe is normally >= 0 (the best paper gain reached) and mae
    is normally <= 0 (the worst paper drawdown reached), and both can be
    read directly as "how far in my favor / against me did this ever
    get, at most, within the window" without a separate sign convention
    to remember.
    """

    window_bars: int = Field(gt=0)
    outcome_timestamp: datetime
    forward_return: float
    mfe: float
    mae: float


class BacktestSignal(BaseModel):
    """One ENTERABLE occurrence of the referenced Experiment's
    conditions becoming true -- the spec's "persist each individual
    signal/event so results are fully inspectable" requirement, in
    code. "Enterable" means bar t's condition fired AND bar t+1 (the
    next bar) exists in the dataset to enter at -- a condition firing on
    the very last bar of the queried range produces no BacktestSignal at
    all (there is no next-bar-open to enter at, and inventing one would
    be exactly the look-ahead this feature exists to prevent).

    `feature_values` is the same feature_id -> observed-value mapping
    app/research/conditions.py::evaluate_feature_conditions() already
    returns for Research's own ExperimentEvent.condition_values --
    reused here verbatim (see app/backtesting/engine.py), not
    recomputed under a different name.

    `signal_timestamp` is bar t's own timestamp (when the condition
    became true); `entry_timestamp`/`entry_price` are bar t+1's
    timestamp/open (see the module docstring: next-bar-open entry is
    what prevents look-ahead bias here). `outcomes` holds one
    BacktestWindowOutcome per configured window that had enough forward
    bars remaining in the dataset to measure -- a window too close to
    the end of the queried range is simply absent from this list, never
    filled with a fabricated value (the same "skip, don't fabricate"
    rule app/research/engine.py already applies to a signal whose
    forward_return window falls outside the dataset).
    """

    backtest_id: str
    experiment_id: str
    symbol: str
    timeframe: str
    signal_timestamp: datetime
    entry_timestamp: datetime
    entry_price: float
    feature_values: dict[str, float | bool]
    outcomes: list[BacktestWindowOutcome]


class BacktestWindowResults(BaseModel):
    """Aggregate statistics for ONE configured window, across every
    BacktestSignal that had a measurable outcome there (app/backtesting/
    aggregation.py). Present for every window in Backtest.windows even
    when `signal_count` is 0 -- a window that never had a single
    measurable outcome is a legitimate, reportable result (the
    hypothesis's forward-looking data simply never reached that far in
    this dataset), not an omitted key.

    `win_rate`/`mean_return`/etc. are None -- never 0.0 -- when
    `signal_count` is 0, and `std_dev_return` is specifically None for
    exactly one signal too (undefined, not zero) -- the identical
    convention app/research/aggregation.py already applies to
    ExperimentResults, applied here per-window instead of once per
    experiment. A "win" is simply forward_return > 0 -- Backtesting v1
    has no separate success threshold of its own (unlike Research's
    Outcome.threshold): the spec's question is "what happened
    afterward", and a positive forward return is what "happened" means
    here, not a hypothesis-specific bar to clear.
    """

    window_bars: int
    signal_count: int
    win_count: int
    win_rate: float | None
    mean_return: float | None
    median_return: float | None
    std_dev_return: float | None
    best_return: float | None
    worst_return: float | None
    mean_mfe: float | None
    mean_mae: float | None


class BacktestResults(BaseModel):
    """One BacktestWindowResults per window configured on the parent
    Backtest, in the same order as Backtest.windows."""

    windows: list[BacktestWindowResults]


class BacktestCreateRequest(BaseModel):
    """Body of POST /backtests -- "Select an existing Research
    experiment" (the spec's first requirement) is the ENTIRE input:
    `experiment_id` alone, plus an optional `windows` override.
    Everything else a Backtest needs (symbol, timeframe, provider, date
    range, conditions, feature_contract_version) is read off the
    referenced Experiment at creation time -- see Backtest.new() --
    never re-entered or re-derived here, so a Backtest can never
    silently drift from the Experiment it claims to measure.

    `windows` are forward horizons in BARS (see the module docstring),
    defaulting to the spec's own (5, 15, 30, 60) if omitted. Must be
    distinct positive integers -- a duplicate or non-positive window has
    no honest, distinguishable meaning as its own aggregate row.
    """

    experiment_id: str
    windows: list[int] = Field(default_factory=lambda: list(DEFAULT_WINDOWS))

    @field_validator("windows")
    @classmethod
    def _windows_are_distinct_and_positive(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("windows must contain at least one forward-bar count.")
        if any(window <= 0 for window in value):
            raise ValueError("Every window must be a positive number of bars.")
        if len(set(value)) != len(value):
            raise ValueError(f"windows must be distinct, got {value}.")
        return sorted(value)


class Backtest(BaseModel):
    """The persisted backtest record (app/storage/backtest_repository.py).
    `experiment_id`/`symbol`/`timeframe`/`provider`/`windows`/
    `feature_contract_version` are set once, at creation (from the
    referenced Experiment, plus the requested/defaulted `windows`), and
    never change again -- re-running (POST /backtests/{id}/run) only
    ever updates status/completed_at/results/error_message, the same
    "a completed run must preserve the exact parameters that produced
    it" guarantee Experiment itself already makes.

    `feature_contract_version` is captured from the referenced
    Experiment's OWN stored value at Backtest-creation time (not
    re-read from the live FEATURE_CONTRACT_VERSION at run time) -- a
    Backtest measures what its Experiment measures, under the exact
    contract version that Experiment was created against, even if the
    Feature Engine's contract has since moved on. A run only evaluates
    FeatureRecords whose own feature_contract_version matches this
    value, identical to how app/research/engine.py::run_experiment()
    already treats a mismatched FeatureRecord as absent.
    """

    id: str
    experiment_id: str
    symbol: str
    timeframe: str
    provider: str
    windows: list[int]
    feature_contract_version: str
    status: BacktestStatus
    created_at: datetime
    completed_at: datetime | None = None
    results: BacktestResults | None = None
    error_message: str | None = None

    @classmethod
    def new(cls, *, experiment_id: str, symbol: str, timeframe: str, provider: str, windows: list[int], feature_contract_version: str) -> "Backtest":
        """Assigns the server-owned fields (id/status/created_at) for a
        brand-new DRAFT backtest. Everything else is passed in already
        resolved -- see app/api/backtesting.py, which reads
        symbol/timeframe/provider/feature_contract_version off the
        referenced Experiment BEFORE calling this, so this constructor
        itself never has to know about app.storage.research_repository."""
        return cls(
            id=str(uuid.uuid4()),
            experiment_id=experiment_id,
            symbol=symbol,
            timeframe=timeframe,
            provider=provider,
            windows=windows,
            feature_contract_version=feature_contract_version,
            status=BacktestStatus.DRAFT,
            created_at=datetime.now(timezone.utc),
        )


class BacktestSignalsResponse(BaseModel):
    """The full response body of GET /backtests/{id}/signals -- the
    individual, inspectable signals (spec: "persist each individual
    signal/event"), not just the aggregate BacktestResults."""

    backtest_id: str
    signal_count: int
    signals: list[BacktestSignal]
