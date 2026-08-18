"""The OOS-evidence-period repository -- the ONLY module that writes
SQL for `experiment_oos_periods` (see app/storage/db.py's schema).
Same boundary rule as every other repository in this app: nothing
outside this file touches sqlite3 or a SQL string for this table.

    app/models/oos_evidence.py         OOSPeriod <-> its persisted shape
    app/oos_evidence/period.py         pure validation, BEFORE a row ever reaches this file
    Storage (THIS FILE)                OOSPeriod <-> database row, and back
    app/api/oos_evidence.py            HTTP glue

No update/replace function exists here -- a period, once registered,
never changes (app/models/oos_evidence.py::OOSPeriod's own docstring:
"no mutation after creation").
"""

from datetime import datetime
from pathlib import Path

from app.models.oos_evidence import OOSPeriod
from app.storage.db import get_connection


def save_period(period: OOSPeriod, *, db_path: str | Path | None = None) -> bool:
    """Persists `period`, IGNORING the insert if a (experiment_id,
    oos_partition_id) row already exists -- the same INSERT-OR-IGNORE
    idempotency convention app/storage/oos_partition_repository.py::
    save_partition() already applies to the partition ROW itself,
    applied here to the LINK row instead. app/oos_evidence/period.py::
    validate_new_period() is what actually rejects a genuine duplicate
    registration attempt with a clear error BEFORE this is ever called
    (app/api/oos_evidence.py) -- this idempotent INSERT OR IGNORE is
    the storage layer's own defense-in-depth, not the primary guard.

    Returns True if this call actually inserted a new row, False if an
    identical (experiment_id, oos_partition_id) pair already existed.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO experiment_oos_periods
                    (experiment_id, oos_partition_id, symbol, timeframe, provider, oos_start, oos_end, label, registered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    period.experiment_id,
                    period.oos_partition_id,
                    period.symbol,
                    period.timeframe,
                    period.provider,
                    period.oos_start.isoformat(),
                    period.oos_end.isoformat(),
                    period.label,
                    period.registered_at.isoformat(),
                ),
            )
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_period(experiment_id: str, oos_partition_id: str, *, db_path: str | Path | None = None) -> OOSPeriod | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM experiment_oos_periods WHERE experiment_id = ? AND oos_partition_id = ?",
            (experiment_id, oos_partition_id),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_period(row) if row is not None else None


def list_periods(experiment_id: str, *, db_path: str | Path | None = None) -> list[OOSPeriod]:
    """Every OOS period ever registered for `experiment_id`, earliest-
    OOS-window first -- the full accumulation history, not just the
    latest one (matching app/storage/oos_evaluation_repository.py::
    list_evaluations()'s own "do not silently replace historical
    evaluations" precedent, applied here to registrations instead)."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM experiment_oos_periods WHERE experiment_id = ? ORDER BY oos_start ASC",
            (experiment_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_period(row) for row in rows]


def _row_to_period(row) -> OOSPeriod:
    return OOSPeriod(
        id=row["oos_partition_id"],
        experiment_id=row["experiment_id"],
        oos_partition_id=row["oos_partition_id"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        provider=row["provider"],
        oos_start=datetime.fromisoformat(row["oos_start"]),
        oos_end=datetime.fromisoformat(row["oos_end"]),
        label=row["label"],
        registered_at=datetime.fromisoformat(row["registered_at"]),
    )
