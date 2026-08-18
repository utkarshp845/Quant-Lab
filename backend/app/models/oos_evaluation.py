"""Persistent shapes for OOS Evaluation v1 (v0.1.31, app/oos_evaluation/,
app/storage/oos_evaluation_repository.py, app/api/oos_evaluation.py):
the immutable, append-only record of ONE run of a FROZEN experiment's
hypothesis against its linked OOS partition's HOLDOUT data.

Deliberately reuses app.models.backtesting.BacktestWindowOutcome/
BacktestResults/BacktestWindowResults verbatim rather than re-defining
equivalent shapes -- Backtesting v1's own outcome/aggregate math
(app/backtesting/engine.py::run_backtest(), app/backtesting/
aggregation.py::aggregate_results()) is reused UNMODIFIED to produce
these values (see app/oos_evaluation/engine.py's own docstring for the
full pipeline); duplicating their container shapes here would be
exactly the kind of "duplicate calculation" this feature's own
instructions forbid, even though no math would actually be
re-implemented by doing so -- the shape itself IS part of what "reuse,
don't duplicate" means.

OOSSignal mirrors app.models.backtesting.BacktestSignal's shape
(signal_timestamp/entry_timestamp/entry_price/feature_values/outcomes)
but is its own model: `evaluation_id`, not `backtest_id` -- an OOS
evaluation is not a Backtest row (no `backtests` table entry is ever
created for one), and conflating the two identity concepts would make
"which OOS evaluations exist" and "which backtests exist" impossible to
tell apart by primary key alone.

Kept a leaf module: only pydantic, the stdlib, and
app.models.backtesting (for the three reused shapes above) are
imported here -- never app.oos_evaluation, app.storage, or app.api.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from app.models.backtesting import BacktestResults, BacktestWindowOutcome


class OOSEvaluationStatus(str, Enum):
    """Mirrors ExperimentStatus's/BacktestStatus's own two-terminal-
    outcome shape (app/models/research.py, app/models/backtesting.py):
    COMPLETED even for zero signals found (a legitimate, reportable
    result -- see BacktestWindowResults' own docstring, the identical
    convention applied here), FAILED with `error_message` set for
    anything unexpected during the holdout data pipeline itself (never
    for a precondition rejection -- see app/oos_evaluation/engine.py's
    own docstring for exactly where that line is drawn: a precondition
    failure never reaches the point of creating a result row at all)."""

    COMPLETED = "completed"
    FAILED = "failed"


class OOSSignal(BaseModel):
    """One qualifying, ENTERABLE signal found strictly within the
    holdout window -- app.models.backtesting.BacktestSignal's identical
    per-signal shape, produced by the SAME app.backtesting.engine.
    run_backtest() call, just re-labeled with `evaluation_id` instead of
    `backtest_id` (see the module docstring)."""

    evaluation_id: str
    symbol: str
    timeframe: str
    signal_timestamp: datetime
    entry_timestamp: datetime
    entry_price: float
    feature_values: dict[str, float | bool]
    outcomes: list[BacktestWindowOutcome]


class OOSEvaluationResult(BaseModel):
    """The immutable, append-only record of one OOS evaluation run
    (app/storage/oos_evaluation_repository.py -- `id` is its PRIMARY
    KEY, and there is NO update/replace operation for this table at
    all: re-evaluating the same frozen experiment creates a brand-new
    row with a brand-new `id`, per requirement 6's "do not silently
    replace historical evaluations").

    Every field needed to answer requirement 9's audit questions
    without touching the live, mutable Experiment row:

      - "What hypothesis was tested?" -> hypothesis_hash (+ the
        immutable ExperimentFreezeSnapshot itself, reachable via
        frozen_snapshot_id == experiment_id, see that model's own
        docstring for why those are the same value) and symbol/
        timeframe/provider/feature_contract_version below.
      - "What exact data was held out?" -> oos_partition_id/
        holdout_start/holdout_end.
      - "What exact feature contract was used?" -> feature_contract_version.
      - "What exact hypothesis hash was evaluated?" -> hypothesis_hash.
      - "How many OOS signals occurred?" -> signal_count (and the
        individual OOSSignal rows themselves, app/storage/
        oos_evaluation_repository.py::get_signals()).
      - "What were the outcomes?" -> results.
      - "Was this run before or after freezing?" -> evaluated_at
        compared against frozen_at, both stored on this same row --
        never requires a join back to `experiments` (evaluated_at is
        always >= frozen_at structurally, since evaluation is only
        reachable from a FROZEN-or-later lifecycle state, but storing
        both here means a reader never has to trust that invariant
        blindly to answer the question).

    `frozen_snapshot_id` is literally `ExperimentFreezeSnapshot.
    experiment_id` (its own primary key -- see that model's docstring:
    one snapshot per experiment, ever) -- named distinctly here rather
    than reusing `experiment_id` again, so a reader of this JSON shape
    sees explicitly that "the frozen definition this evaluated" is the
    same concept `provenance`'s own response already exposes, not a
    coincidentally-equal id.

    `outcome_horizon_minutes`/`outcome_window_bars` are BOTH the
    frozen Outcome's single horizon (v0.1.30's Outcome.horizon_minutes,
    unchanged, unmutated) -- `outcome_window_bars` is the SAME horizon
    converted to a bar count via app.research.metrics.bars_for_window()
    (reused, not re-derived) for the ONE window
    app.backtesting.engine.run_backtest() is actually run with (see
    app/oos_evaluation/engine.py's own docstring for why this is
    always exactly one window, never several: requirement 4 forbids
    changing horizons, and the frozen Outcome only ever had one).
    """

    id: str
    experiment_id: str
    hypothesis_hash: str
    frozen_snapshot_id: str
    oos_partition_id: str
    symbol: str
    timeframe: str
    provider: str
    holdout_start: datetime
    holdout_end: datetime
    feature_contract_version: str
    outcome_horizon_minutes: int
    outcome_window_bars: int | None
    signal_count: int
    results: BacktestResults | None
    status: OOSEvaluationStatus
    error_message: str | None
    frozen_at: datetime
    evaluated_at: datetime
