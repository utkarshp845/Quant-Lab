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

from app.models.research import Experiment, ExperimentEvent, ExperimentResults, ExperimentStatus, FeatureCondition, Outcome
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
    row goes through update_experiment_run() below instead."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO experiments
                    (id, name, hypothesis, symbol, start_date, end_date, timeframe, provider,
                     conditions_json, outcome_json, feature_contract_version, status, created_at,
                     completed_at, results_json, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
