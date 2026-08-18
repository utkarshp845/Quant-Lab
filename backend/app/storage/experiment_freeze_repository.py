"""The experiment-freeze-snapshot repository -- the ONLY module that
writes SQL for the `experiment_freeze_snapshots` table (see
app/storage/db.py's schema). Same boundary rule as
app/storage/research_repository.py: nothing outside this file touches
sqlite3 or a SQL string for this table.

    app/models/experiment_freeze.py   ExperimentFreezeSnapshot <-> its persisted shape
    app/research/lifecycle.py          builds a snapshot (pure, no I/O)
    Storage (THIS FILE)                 ExperimentFreezeSnapshot <-> database row, and back
    app/api/experiment_freeze.py        HTTP glue

Every function here takes/returns ExperimentFreezeSnapshot (app/
models/experiment_freeze.py) -- never a raw sqlite3.Row past this
file's own boundary, matching research_repository.py's own rule.
"""

from datetime import date, datetime
from pathlib import Path

from pydantic import TypeAdapter

from app.models.experiment_freeze import ExperimentFreezeSnapshot
from app.models.research import FeatureCondition, Outcome
from app.storage.db import get_connection

# Same (de)serialization approach as app/storage/research_repository.py's
# own _ConditionsAdapter -- re-declared here, not imported across the
# module boundary, matching this app's existing precedent for a small
# piece of (de)serialization glue two storage modules both need (see
# app/statistical_validation/v2/engine.py's own docstring for the
# identical "duplicate ~10 lines of glue, never a private symbol
# import" choice, and app/storage/db.py's/historical_bar_repository.py's
# "nothing outside this file touches sqlite3 for this table" rule,
# which a private cross-import would quietly violate the spirit of).
_ConditionsAdapter = TypeAdapter(list[FeatureCondition])


def save_snapshot(snapshot: ExperimentFreezeSnapshot, *, db_path: str | Path | None = None) -> None:
    """Inserts the one-and-only freeze snapshot for an experiment.
    `experiment_id` is the primary key, and there is no re-freeze
    operation (app/research/lifecycle.py's state machine only allows
    DRAFT -> FROZEN once), so a plain INSERT is correct -- a second
    call for the same experiment_id is a programming error (the caller
    already violated the lifecycle state machine to get here) and
    should raise sqlite3.IntegrityError, not be silently ignored or
    silently overwrite what "exactly what hypothesis was evaluated"
    (requirement 5) is supposed to answer permanently."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO experiment_freeze_snapshots
                    (experiment_id, hypothesis_hash, name, hypothesis, symbol, timeframe, provider,
                     start_date, end_date, feature_contract_version, conditions_json, outcome_json,
                     oos_partition_id, experiment_created_at, frozen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.experiment_id,
                    snapshot.hypothesis_hash,
                    snapshot.name,
                    snapshot.hypothesis,
                    snapshot.symbol,
                    snapshot.timeframe,
                    snapshot.provider,
                    snapshot.start_date.isoformat(),
                    snapshot.end_date.isoformat(),
                    snapshot.feature_contract_version,
                    _ConditionsAdapter.dump_json(snapshot.conditions).decode(),
                    snapshot.outcome.model_dump_json(),
                    snapshot.oos_partition_id,
                    snapshot.experiment_created_at.isoformat(),
                    snapshot.frozen_at.isoformat(),
                ),
            )
    finally:
        conn.close()


def get_snapshot(experiment_id: str, *, db_path: str | Path | None = None) -> ExperimentFreezeSnapshot | None:
    """Returns None (never raises) when this experiment has never been
    frozen -- the same "absence is a normal, checkable outcome"
    convention app/storage/research_repository.py::get_experiment()
    already uses."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM experiment_freeze_snapshots WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_snapshot(row)


def _row_to_snapshot(row) -> ExperimentFreezeSnapshot:
    return ExperimentFreezeSnapshot(
        experiment_id=row["experiment_id"],
        hypothesis_hash=row["hypothesis_hash"],
        name=row["name"],
        hypothesis=row["hypothesis"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        provider=row["provider"],
        start_date=date.fromisoformat(row["start_date"]),
        end_date=date.fromisoformat(row["end_date"]),
        feature_contract_version=row["feature_contract_version"],
        conditions=_ConditionsAdapter.validate_json(row["conditions_json"]),
        outcome=Outcome.model_validate_json(row["outcome_json"]),
        oos_partition_id=row["oos_partition_id"],
        experiment_created_at=datetime.fromisoformat(row["experiment_created_at"]),
        frozen_at=datetime.fromisoformat(row["frozen_at"]),
    )
