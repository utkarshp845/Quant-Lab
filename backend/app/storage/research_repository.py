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

from datetime import date, datetime
from pathlib import Path

from app.models.research import Condition, Experiment, ExperimentEvent, ExperimentResults, ExperimentStatus, Outcome
from app.storage.db import get_connection


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
                     condition_json, outcome_json, status, created_at, completed_at, results_json, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    experiment.condition.model_dump_json(),
                    experiment.outcome.model_dump_json(),
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
                        (experiment_id, symbol, signal_timestamp, signal_price, condition_value,
                         outcome_timestamp, outcome_price, outcome_value, success)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.experiment_id,
                        event.symbol,
                        event.signal_timestamp.isoformat(),
                        event.signal_price,
                        event.condition_value,
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
        condition=Condition.model_validate_json(row["condition_json"]),
        outcome=Outcome.model_validate_json(row["outcome_json"]),
        status=ExperimentStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        results=ExperimentResults.model_validate_json(row["results_json"]) if row["results_json"] else None,
        error_message=row["error_message"],
    )


def _row_to_event(row) -> ExperimentEvent:
    return ExperimentEvent(
        experiment_id=row["experiment_id"],
        symbol=row["symbol"],
        signal_timestamp=datetime.fromisoformat(row["signal_timestamp"]),
        signal_price=row["signal_price"],
        condition_value=row["condition_value"],
        outcome_timestamp=datetime.fromisoformat(row["outcome_timestamp"]),
        outcome_price=row["outcome_price"],
        outcome_value=row["outcome_value"],
        success=bool(row["success"]),
    )
