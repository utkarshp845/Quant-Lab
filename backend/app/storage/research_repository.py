"""The research-experiments repository -- the ONLY module that writes
SQL for Research v1's `experiments`/`experiment_events` tables (see
app/storage/db.py's schema). Same boundary rule as
app/storage/historical_bar_repository.py: nothing outside this file
(the API routes, the engine) touches sqlite3 or a SQL string for either
table.

    app/research/engine.py     bars + Experiment -> ExperimentEvent[] + ExperimentResults (pure, no I/O)
    app/api/research.py        HTTP glue: calls this module + engine.py + historical_bar_repository
    Storage (THIS FILE)        Experiment / ExperimentEvent <-> database rows, and back

Every function here takes/returns Experiment or ExperimentEvent (app/
models/research.py) -- never a raw sqlite3.Row past this file's own
boundary, matching historical_bar_repository.py's own rule.
"""

import json
from datetime import date, datetime
from pathlib import Path

from pydantic import TypeAdapter

from app.models.research import (
    Experiment,
    ExperimentEvent,
    ExperimentLifecycleState,
    ExperimentResults,
    ExperimentStatus,
    FeatureCondition,
    Outcome,
)
from app.storage.db import get_connection

# For (de)serializing `conditions` -- a LIST of FeatureCondition, unlike
# Outcome (exactly one, so Outcome.model_dump_json()/model_validate_json()
# is enough on its own). pydantic's TypeAdapter is the documented way to
# get the same validate/dump behavior for a non-BaseModel type like
# list[FeatureCondition].
_ConditionsAdapter = TypeAdapter(list[FeatureCondition])
_ConditionValuesAdapter = TypeAdapter(dict[str, float | bool])


def save_experiment(experiment: Experiment, *, db_path: str | Path | None = None) -> None:
    """Inserts a brand-new experiment row -- always a DRAFT fresh out of
    Experiment.new(). experiments.id is the primary key, so this is
    only ever called once per experiment; every later change to that
    row goes through update_experiment_run() (execution status) or
    freeze_experiment()/set_oos_partition()/mark_oos_evaluated()/
    mark_archived() (v0.1.30, lifecycle state) below instead."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO experiments
                    (id, name, hypothesis, symbol, start_date, end_date, timeframe, provider,
                     conditions_json, outcome_json, feature_contract_version, status, created_at,
                     completed_at, results_json, error_message, lifecycle_state, oos_partition_id,
                     hypothesis_hash, frozen_at, archived_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment.id,
                    experiment.name,
                    experiment.hypothesis,
                    experiment.symbol,
                    experiment.start_date.isoformat(),
                    experiment.end_date.isoformat(),
                    experiment.timeframe,
                    experiment.provider,
                    _ConditionsAdapter.dump_json(experiment.conditions).decode(),
                    experiment.outcome.model_dump_json(),
                    experiment.feature_contract_version,
                    experiment.status.value,
                    experiment.created_at.isoformat(),
                    experiment.completed_at.isoformat() if experiment.completed_at else None,
                    experiment.results.model_dump_json() if experiment.results else None,
                    experiment.error_message,
                    experiment.lifecycle_state.value,
                    experiment.oos_partition_id,
                    experiment.hypothesis_hash,
                    experiment.frozen_at.isoformat() if experiment.frozen_at else None,
                    experiment.archived_at.isoformat() if experiment.archived_at else None,
                ),
            )
    finally:
        conn.close()


def mark_running(experiment_id: str, *, db_path: str | Path | None = None) -> None:
    """Flips an experiment to RUNNING the instant POST .../run starts
    executing -- a separate write from update_experiment_run() because
    it happens BEFORE the engine has produced anything to persist, and
    touches only `status` (completed_at/results/error_message are
    still whatever the previous run, if any, left them as)."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("UPDATE experiments SET status = ? WHERE id = ?", (ExperimentStatus.RUNNING.value, experiment_id))
    finally:
        conn.close()


def update_experiment_run(
    experiment_id: str,
    *,
    status: ExperimentStatus,
    completed_at: datetime,
    results: ExperimentResults | None,
    error_message: str | None,
    db_path: str | Path | None = None,
) -> None:
    """Records the outcome of one run (COMPLETED with results, or
    FAILED with an error_message -- see app/api/research.py::run()).
    status/completed_at/results_json/error_message are the ONLY columns
    this ever touches; every parameter column set at save_experiment()
    time (symbol/start_date/.../condition_json/outcome_json) is left
    untouched, which is what keeps a completed experiment's parameters
    exactly reproducible (spec section 8)."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                UPDATE experiments
                SET status = ?, completed_at = ?, results_json = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    completed_at.isoformat(),
                    results.model_dump_json() if results else None,
                    error_message,
                    experiment_id,
                ),
            )
    finally:
        conn.close()


def set_oos_partition(experiment_id: str, oos_partition_id: str | None, *, db_path: str | Path | None = None) -> bool:
    """Associates (or clears, if `oos_partition_id` is None) the OOS
    partition a DRAFT experiment reserves (requirement 6: "a DRAFT
    experiment may be associated with a partition"). Restricted to
    `lifecycle_state = 'draft'` in the WHERE clause itself -- defense
    in depth alongside app/api/experiment_freeze.py's own check before
    calling this, the same "the repository re-checks what the route
    already checked" idiom app/oos/access.py's confirm flag applies at
    the function layer, not just the HTTP layer. Returns True if a row
    was actually updated (id exists AND was still draft), False
    otherwise, so the caller can tell "no such experiment" apart from
    "not draft anymore" apart from "succeeded" without a second read.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(
                "UPDATE experiments SET oos_partition_id = ? WHERE id = ? AND lifecycle_state = ?",
                (oos_partition_id, experiment_id, ExperimentLifecycleState.DRAFT.value),
            )
        return cursor.rowcount > 0
    finally:
        conn.close()


def freeze_experiment(
    experiment_id: str,
    *,
    hypothesis_hash: str,
    frozen_at: datetime,
    oos_partition_id: str | None,
    db_path: str | Path | None = None,
) -> bool:
    """The DRAFT -> FROZEN write (requirement 1/2): sets
    lifecycle_state/hypothesis_hash/frozen_at/oos_partition_id together,
    in the one UPDATE, so a reader can never observe a partially-frozen
    row (a hash with no frozen_at, or vice versa). `oos_partition_id`
    is written again here (not just relied upon from a prior
    set_oos_partition() call) so the value captured in the
    ExperimentFreezeSnapshot at the exact same moment (app/research/
    lifecycle.py::build_freeze_snapshot(), called with this same
    experiment) is guaranteed to match what ends up on the live row --
    both come from the identical in-memory Experiment app/api/
    experiment_freeze.py's freeze route already validated. Restricted
    to `lifecycle_state = 'draft'` in the WHERE clause -- the same
    defense-in-depth as set_oos_partition() above; app/research/
    lifecycle.py::validate_transition() is what the route checks
    BEFORE calling this, this is the second, independent check.
    Returns True if the row was actually frozen (was still draft),
    False otherwise."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(
                """
                UPDATE experiments
                SET lifecycle_state = ?, hypothesis_hash = ?, frozen_at = ?, oos_partition_id = ?
                WHERE id = ? AND lifecycle_state = ?
                """,
                (
                    ExperimentLifecycleState.FROZEN.value,
                    hypothesis_hash,
                    frozen_at.isoformat(),
                    oos_partition_id,
                    experiment_id,
                    ExperimentLifecycleState.DRAFT.value,
                ),
            )
        return cursor.rowcount > 0
    finally:
        conn.close()


def mark_oos_evaluated(experiment_id: str, *, oos_evaluated_at: datetime, db_path: str | Path | None = None) -> bool:
    """FROZEN -> OOS_EVALUATED (requirement 1's third transition).
    INFRASTRUCTURE ONLY, per this feature's own scope: no HTTP route
    calls this yet, because the actual OOS-evaluation operation that
    would legitimately call it does not exist in this codebase (a
    future feature's job). Exists so that transition is real,
    persistable, and directly testable today rather than a table entry
    with nothing behind it. `oos_evaluated_at` is accepted but not (yet)
    persisted to its own column -- there is no
    `experiments.oos_evaluated_at` column, deliberately: adding
    storage for a timestamp nothing yet produces would be exactly the
    kind of unused-column speculation this app's schema comments
    elsewhere (e.g. raw_ingestions') avoid. Restricted to
    `lifecycle_state = 'frozen'` in the WHERE clause, matching every
    other lifecycle-write function above."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(
                "UPDATE experiments SET lifecycle_state = ? WHERE id = ? AND lifecycle_state = ?",
                (ExperimentLifecycleState.OOS_EVALUATED.value, experiment_id, ExperimentLifecycleState.FROZEN.value),
            )
        return cursor.rowcount > 0
    finally:
        conn.close()


def mark_archived(experiment_id: str, *, archived_at: datetime, db_path: str | Path | None = None) -> bool:
    """FROZEN -> ARCHIVED or OOS_EVALUATED -> ARCHIVED (requirement 1's
    two archive transitions) -- the WHERE clause allows either source
    state, matching _VALID_TRANSITIONS in app/research/lifecycle.py
    exactly; app/api/experiment_freeze.py's archive route calls
    validate_transition() with the experiment's CURRENT state before
    ever reaching this, so this is the second, defense-in-depth check,
    not the only one."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(
                """
                UPDATE experiments
                SET lifecycle_state = ?, archived_at = ?
                WHERE id = ? AND lifecycle_state IN (?, ?)
                """,
                (
                    ExperimentLifecycleState.ARCHIVED.value,
                    archived_at.isoformat(),
                    experiment_id,
                    ExperimentLifecycleState.FROZEN.value,
                    ExperimentLifecycleState.OOS_EVALUATED.value,
                ),
            )
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_experiment(experiment_id: str, *, db_path: str | Path | None = None) -> Experiment | None:
    """Returns None (never raises) when no experiment has that id --
    the same "absence is a normal, checkable outcome" convention
    historical_bar_repository.get_bars() uses for an empty result."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_experiment(row)


def list_experiments(*, db_path: str | Path | None = None) -> list[Experiment]:
    """Every saved experiment, newest-created first -- the read side of
    "retrieve the completed experiment later" (spec completion
    criterion 9) for a caller that does not already know the id."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT * FROM experiments ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()
    return [_row_to_experiment(row) for row in rows]


def replace_events(experiment_id: str, events: list[ExperimentEvent], *, db_path: str | Path | None = None) -> None:
    """Deletes this experiment's existing events (none, on a first run)
    and inserts `events`, in one transaction, so a caller never observes
    a half-replaced state. Re-running the same experiment against the
    same dataset must produce the same events, not a doubled or tripled
    set -- see the schema comment on experiment_events (app/storage/
    db.py) for why this replaces rather than appends."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM experiment_events WHERE experiment_id = ?", (experiment_id,))
            for event in events:
                conn.execute(
                    """
                    INSERT INTO experiment_events
                        (experiment_id, symbol, signal_timestamp, signal_price, condition_values_json,
                         outcome_timestamp, outcome_price, outcome_value, success)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.experiment_id,
                        event.symbol,
                        event.signal_timestamp.isoformat(),
                        event.signal_price,
                        json.dumps(event.condition_values),
                        event.outcome_timestamp.isoformat(),
                        event.outcome_price,
                        event.outcome_value,
                        1 if event.success else 0,
                    ),
                )
    finally:
        conn.close()


def get_events(experiment_id: str, *, db_path: str | Path | None = None) -> list[ExperimentEvent]:
    """Every stored event for this experiment, oldest-signal-first --
    the individual observations spec section 5 requires stay
    accessible, not just the aggregate ExperimentResults."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM experiment_events WHERE experiment_id = ? ORDER BY signal_timestamp ASC",
            (experiment_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_event(row) for row in rows]


def _row_to_experiment(row) -> Experiment:
    return Experiment(
        id=row["id"],
        name=row["name"],
        hypothesis=row["hypothesis"],
        symbol=row["symbol"],
        start_date=date.fromisoformat(row["start_date"]),
        end_date=date.fromisoformat(row["end_date"]),
        timeframe=row["timeframe"],
        provider=row["provider"],
        conditions=_ConditionsAdapter.validate_json(row["conditions_json"]),
        outcome=Outcome.model_validate_json(row["outcome_json"]),
        feature_contract_version=row["feature_contract_version"],
        status=ExperimentStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        results=ExperimentResults.model_validate_json(row["results_json"]) if row["results_json"] else None,
        error_message=row["error_message"],
        lifecycle_state=ExperimentLifecycleState(row["lifecycle_state"]),
        oos_partition_id=row["oos_partition_id"],
        hypothesis_hash=row["hypothesis_hash"],
        frozen_at=datetime.fromisoformat(row["frozen_at"]) if row["frozen_at"] else None,
        archived_at=datetime.fromisoformat(row["archived_at"]) if row["archived_at"] else None,
    )


def _row_to_event(row) -> ExperimentEvent:
    # `condition_values_json` is NULL only for an event row saved before
    # v0.1.24 that hasn't been through a re-run since -- see the schema
    # comment on experiment_events (app/storage/db.py) for why that's
    # not individually data-migrated. Falling back to {} keeps this a
    # read, not a crash; re-running the experiment (POST .../run)
    # repopulates it correctly.
    condition_values = _ConditionValuesAdapter.validate_json(row["condition_values_json"]) if row["condition_values_json"] else {}
    return ExperimentEvent(
        experiment_id=row["experiment_id"],
        symbol=row["symbol"],
        signal_timestamp=datetime.fromisoformat(row["signal_timestamp"]),
        signal_price=row["signal_price"],
        condition_values=condition_values,
        outcome_timestamp=datetime.fromisoformat(row["outcome_timestamp"]),
        outcome_price=row["outcome_price"],
        outcome_value=row["outcome_value"],
        success=bool(row["success"]),
    )
