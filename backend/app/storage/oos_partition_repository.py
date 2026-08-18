"""The OOS-partition repository -- the ONLY module that writes SQL for
the `oos_partitions` table (see app/storage/db.py's schema). Same
boundary rule as app/storage/historical_bar_repository.py and
app/storage/research_repository.py: nothing outside this file touches
sqlite3 or a SQL string for this table.

    app/models/oos_partition.py   OOSPartition <-> its persisted shape
    Storage (THIS FILE)           OOSPartition <-> database row, and back
    app/api/oos_partitions.py     HTTP glue

Every function here takes/returns OOSPartition (app/models/
oos_partition.py) -- never a raw sqlite3.Row past this file's own
boundary, matching historical_bar_repository.py's own rule.
"""

from pathlib import Path

from app.models.oos_partition import OOSPartition
from app.storage.db import get_connection


def save_partition(partition: OOSPartition, *, db_path: str | Path | None = None) -> bool:
    """Persists `partition`, IGNORING the insert if a row with the same
    `id` already exists. Because `id` is a deterministic hash of
    provider/symbol/timeframe/the four boundary timestamps (see
    app/models/oos_partition.py::compute_partition_id()), this makes
    "create the same partition twice" idempotent rather than an error
    or a duplicate row -- the original row (including its original
    `created_at`) is left untouched, the same INSERT-OR-IGNORE-preserves-
    the-first-write convention app/storage/historical_bar_repository.py::
    save_bars() already applies to a re-saved bar.

    Returns True if this call actually inserted a new row, False if an
    identical partition already existed (the caller can always re-fetch
    the canonical, persisted record via get_partition(partition.id)
    either way).
    """
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO oos_partitions
                    (id, symbol, timeframe, provider, development_start, development_end,
                     holdout_start, holdout_end, label, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    partition.id,
                    partition.symbol,
                    partition.timeframe,
                    partition.provider,
                    partition.development_start.isoformat(),
                    partition.development_end.isoformat(),
                    partition.holdout_start.isoformat(),
                    partition.holdout_end.isoformat(),
                    partition.label,
                    partition.created_at.isoformat(),
                ),
            )
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_partition(partition_id: str, *, db_path: str | Path | None = None) -> OOSPartition | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM oos_partitions WHERE id = ?", (partition_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_partition(row) if row is not None else None


def list_partitions(
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    provider: str | None = None,
    db_path: str | Path | None = None,
) -> list[OOSPartition]:
    """Every saved partition, newest first -- optionally narrowed to a
    symbol/timeframe/provider, the same optional-filter convention
    app/api/backtesting.py's GET /backtests already applies via
    experiment_id."""
    clauses: list[str] = []
    params: list[str] = []
    if symbol is not None:
        clauses.append("symbol = ?")
        params.append(symbol.upper())
    if timeframe is not None:
        clauses.append("timeframe = ?")
        params.append(timeframe)
    if provider is not None:
        clauses.append("provider = ?")
        params.append(provider)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            f"SELECT * FROM oos_partitions {where} ORDER BY created_at DESC",  # noqa: S608 -- clauses are fixed column-name literals above, never caller-supplied text
            params,
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_partition(row) for row in rows]


def _row_to_partition(row) -> OOSPartition:
    return OOSPartition(
        id=row["id"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        provider=row["provider"],
        development_start=row["development_start"],
        development_end=row["development_end"],
        holdout_start=row["holdout_start"],
        holdout_end=row["holdout_end"],
        label=row["label"],
        created_at=row["created_at"],
    )
