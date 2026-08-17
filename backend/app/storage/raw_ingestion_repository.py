"""The raw-ingestion repository (v0.1.19) -- persists the ORIGINAL
provider response / CSV content, before any parsing, validation, or
normalization happens to it. See app/storage/db.py's `raw_ingestions`
schema comment for why this is one row per ingestion REQUEST (a
provider fetch call, a CSV upload), not one row per bar, and why it
deliberately does not reuse historical_bars'/quarantined_bars' OHLCV
columns.

    Provider/CSV (app/providers/*.py, app/api/historical_comparison.py)
      -> persist_raw_ingestion_safely()   (THIS FILE -- never raises)
      -> existing parsing/validation/normalization/storage, unchanged

This module is the only place that writes to `raw_ingestions`, mirroring
historical_bar_repository.py's "one file, one table" convention. Two
entry points, both used for the SAME write, on purpose:

  save_raw_ingestion()          the honest function: writes the row,
                                 raises on a real database error, same
                                 as save_bars()/save_validated_bars().
  persist_raw_ingestion_safely()  what every real call site actually
                                 calls: wraps save_raw_ingestion() in a
                                 try/except that logs and returns None
                                 on failure instead of raising.

That split matters here specifically, more than it does elsewhere in
this app: raw storage is a NEW, additive stage bolted onto an existing,
already-shipped, already-tested pipeline (fetch -> validate -> normalize
-> store). A raw-storage failure (a full disk, a locked file, a
permissions error) must never be able to break a fetch that would
otherwise have succeeded -- see this module's docstring section in the
verification report for why "the old pipeline still works exactly as
before" was a hard requirement, not a nice-to-have.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from app.storage.db import get_connection

logger = logging.getLogger("app.storage.raw_ingestion_repository")


@dataclass
class RawIngestionRecord:
    """One row read back from `raw_ingestions` -- the audit/reprocessing
    read shape. `raw_payload` is exactly what was received: a JSON
    string (one array per provider fetch call, each element one raw
    page of that call's response) for `content_type="json"`, or the
    original CSV text verbatim for `content_type="csv"` -- never
    re-parsed or reshaped by this repository."""

    batch_id: str
    source: str
    symbol: str | None
    timeframe: str | None
    source_start: str | None
    source_end: str | None
    ingested_at: datetime
    content_type: str
    raw_payload: str
    metadata: dict


def save_raw_ingestion(
    *,
    source: str,
    symbol: str | None,
    timeframe: str | None,
    source_start: date | datetime | str | None,
    source_end: date | datetime | str | None,
    raw_payload: str,
    content_type: str,
    metadata: dict | None = None,
    db_path: str | Path | None = None,
) -> str:
    """Persists one raw-ingestion row and returns its generated
    `batch_id` -- an opaque id whose only job is letting a human
    correlate this row back to "the request that produced it" (see
    this module's docstring); nothing else in the schema depends on it
    being any particular format. Raises on a real database error --
    callers that must never fail because of this (every real call site
    in this app) go through persist_raw_ingestion_safely() below
    instead.

    `source_start`/`source_end` accept whatever the caller already has
    on hand (a date, a datetime, or a pre-formatted string, or None
    when not applicable) and are stored as plain text -- this table is
    an audit log, not something get_bars()-style date-range queries run
    against, so there's no reason to force a single normalized format
    here the way historical_bars' `timestamp` column does.
    """
    batch_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO raw_ingestions
                    (batch_id, source, symbol, timeframe, source_start, source_end,
                     ingested_at, content_type, raw_payload, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    source,
                    symbol,
                    timeframe,
                    _stringify(source_start),
                    _stringify(source_end),
                    now,
                    content_type,
                    raw_payload,
                    json.dumps(metadata or {}),
                ),
            )
    finally:
        conn.close()

    return batch_id


def persist_raw_ingestion_safely(
    *,
    source: str,
    symbol: str | None,
    timeframe: str | None,
    source_start: date | datetime | str | None,
    source_end: date | datetime | str | None,
    raw_payload: str,
    content_type: str,
    metadata: dict | None = None,
    db_path: str | Path | None = None,
) -> str | None:
    """What every real ingestion call site (app/providers/alpaca_provider.py,
    massive_provider.py, app/api/historical_comparison.py) actually
    calls: identical to save_raw_ingestion() above, except it NEVER
    raises. Returns the batch_id on success, or None (logged, not
    thrown) if persisting failed for any reason -- a raw-storage
    problem is never allowed to stop the existing fetch/parse/validate/
    normalize/store pipeline this wraps from doing its job.
    """
    try:
        return save_raw_ingestion(
            source=source,
            symbol=symbol,
            timeframe=timeframe,
            source_start=source_start,
            source_end=source_end,
            raw_payload=raw_payload,
            content_type=content_type,
            metadata=metadata,
            db_path=db_path,
        )
    except Exception:  # noqa: BLE001 -- deliberate: see module + function docstrings for why this must never propagate
        logger.exception(
            "failed to persist raw ingestion (source=%s symbol=%s) -- continuing without it", source, symbol
        )
        return None


def get_raw_ingestions(
    *,
    source: str,
    symbol: str | None = None,
    db_path: str | Path | None = None,
) -> list[RawIngestionRecord]:
    """The audit/reprocessing read path: every raw-ingestion row for
    `source` (optionally narrowed to `symbol`), oldest first -- "what
    did this provider/file literally give us," independent of whatever
    validation/normalization later decided about any bar inside it.
    Because raw storage happens BEFORE validation (see this module's
    docstring), a row is here even for a request whose bars were later
    entirely rejected -- exactly the "what did we get when this record
    failed" question a quarantine entry alone can't answer.
    """
    conn = get_connection(db_path)
    try:
        if symbol is None:
            rows = conn.execute(
                "SELECT * FROM raw_ingestions WHERE source = ? ORDER BY ingested_at ASC",
                (source,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM raw_ingestions WHERE source = ? AND symbol = ? ORDER BY ingested_at ASC",
                (source, symbol.upper()),
            ).fetchall()
    finally:
        conn.close()

    return [
        RawIngestionRecord(
            batch_id=row["batch_id"],
            source=row["source"],
            symbol=row["symbol"],
            timeframe=row["timeframe"],
            source_start=row["source_start"],
            source_end=row["source_end"],
            ingested_at=datetime.fromisoformat(row["ingested_at"]),
            content_type=row["content_type"],
            raw_payload=row["raw_payload"],
            metadata=json.loads(row["metadata"]),
        )
        for row in rows
    ]


def _stringify(value: date | datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()
