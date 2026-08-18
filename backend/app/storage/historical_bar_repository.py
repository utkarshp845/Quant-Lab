"""The historical-bars repository -- the ONLY module in this app that
writes SQL for market data (v0.1.17).

    Provider (app/providers/*.py)   API response -> HistoricalBar
    Storage (THIS FILE)             HistoricalBar -> database row, and back
    API routes (app/api/historical_storage.py)   call save_bars() / get_bars(),
                                     never touch sqlite3 or a SQL string themselves

That boundary is the actual point of this file, not a stylistic
preference: app/api/historical_storage.py, and any future Quant Lab or
scanner code that reads stored bars, must not be able to tell whether a
HistoricalBar came from Alpaca, Massive, a CSV, or this database --
they all produce the identical HistoricalBar shape (app/models/
market_data.py), and this module is the only place a database
row-vs-object translation happens at all.

Every function here takes/returns HistoricalBar (or a small result
type for save_bars) -- never a raw sqlite3.Row past this file's own
boundary, and never a dict standing in for one.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.models.market_data import HistoricalBar
from app.models.validation import QuarantinedBarRecord, RejectedBar, ValidatedBar
from app.storage.db import get_connection


@dataclass
class SaveResult:
    """What save_bars() actually did -- not just "ok", so the caller
    (and, ultimately, the UI's "storage status" line) can distinguish
    "saved 10 new bars" from "all 10 were already there" from "saved 7
    of 10, 3 were dupes" -- three genuinely different outcomes a bare
    boolean or bar count would collapse into one."""

    total: int
    inserted: int
    skipped_duplicates: int


def save_bars(bars: list[HistoricalBar], *, db_path: str | Path | None = None) -> SaveResult:
    """Persists `bars`, skipping any that already exist (same provider+
    symbol+timeframe+timestamp -- the table's UNIQUE constraint, not an
    application-level check, is what actually prevents the duplicate;
    this function just counts what the constraint did). Never raises on
    a duplicate -- re-saving the same fetch is expected, ordinary usage
    (see the UI's "Save to Database" button), not an error condition.

    `created_at` is set here, once, to when the SAVE happened -- not the
    bar's own timestamp (already stored separately) and not backfilled
    if the row already existed (INSERT OR IGNORE never touches an
    existing row's created_at).
    """
    if not bars:
        return SaveResult(total=0, inserted=0, skipped_duplicates=0)

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        inserted = 0
        with conn:
            for bar in bars:
                # status="valid", warnings=[] -- this function is the
                # pre-v0.1.18 entry point, for callers (direct repository
                # tests, any future caller with no validation step of its
                # own) that hand it bars with no ValidationStatus opinion
                # attached at all. save_validated_bars() below is what
                # app/api/historical_storage.py and
                # app/ingestion/auto_ingest.py actually call, once every
                # bar has been through app/ingestion/bar_validation.py.
                inserted += _insert_bar(conn, bar, status="valid", warnings=[], now=now)
    finally:
        conn.close()

    return SaveResult(total=len(bars), inserted=inserted, skipped_duplicates=len(bars) - inserted)


def save_validated_bars(validated_bars: list[ValidatedBar], *, db_path: str | Path | None = None) -> SaveResult:
    """Same INSERT OR IGNORE behavior as save_bars() above (re-saving an
    already-stored bar is a no-op, not an error), but for bars that have
    already been through app/ingestion/bar_validation.py -- each row's
    `validation_status`/`validation_warnings` columns record what that
    step found (VALID and empty, or FLAGGED with the reasons why), so a
    later reader of the table can tell "this bar looked fine" apart
    from "this bar was stored anyway, but flag it for review" without
    re-deriving that from the raw OHLCV values.

    Only ValidatedBar in -- a RejectedBar has no business reaching this
    table at all; see save_rejected_bars() for where those go instead.
    """
    if not validated_bars:
        return SaveResult(total=0, inserted=0, skipped_duplicates=0)

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        inserted = 0
        with conn:
            for validated in validated_bars:
                inserted += _insert_bar(conn, validated.bar, status=validated.status.value, warnings=validated.warnings, now=now)
    finally:
        conn.close()

    return SaveResult(total=len(validated_bars), inserted=inserted, skipped_duplicates=len(validated_bars) - inserted)


def save_rejected_bars(rejected_bars: list[RejectedBar], *, db_path: str | Path | None = None) -> int:
    """Logs every bar app/ingestion/bar_validation.py hard-rejected to
    `quarantined_bars` -- an append-only audit trail (see db.py's
    schema comment for why there's deliberately no UNIQUE constraint
    here, unlike historical_bars) of what was rejected, when, and why.
    Returns how many rows were written -- always len(rejected_bars);
    there is no dedup here to make "how many were skipped" a
    meaningful question the way it is for save_bars().
    """
    if not rejected_bars:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        with conn:
            for rejected in rejected_bars:
                bar = rejected.bar
                conn.execute(
                    """
                    INSERT INTO quarantined_bars
                        (provider, symbol, timeframe, timestamp, open, high, low, close, volume, ingested_at, validation_errors)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        bar.provider,
                        bar.symbol,
                        bar.timeframe,
                        _to_storage_timestamp(bar.timestamp),
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        now,
                        json.dumps(rejected.reasons),
                    ),
                )
    finally:
        conn.close()

    return len(rejected_bars)


def _insert_bar(conn, bar: HistoricalBar, *, status: str, warnings: list[str], now: str) -> int:
    """Shared INSERT OR IGNORE, used by both save_bars() and
    save_validated_bars() -- one place writing this SQL, whether or not
    the caller has a ValidationStatus opinion about the bar. Returns 1
    for a real insert, 0 when the UNIQUE constraint silently ignored a
    duplicate (see TestSaveBars::test_duplicate_bars_are_not_inserted_twice)."""
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO historical_bars
            (provider, symbol, timeframe, timestamp, open, high, low, close, volume, created_at,
             validation_status, validation_warnings)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bar.provider,
            bar.symbol,
            bar.timeframe,
            _to_storage_timestamp(bar.timestamp),
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
            now,
            status,
            json.dumps(warnings),
        ),
    )
    return cursor.rowcount


def get_bars(
    *,
    symbol: str,
    timeframe: str,
    provider: str,
    start: date,
    end: date,
    db_path: str | Path | None = None,
) -> list[HistoricalBar]:
    """Returns previously-saved bars for `symbol`/`timeframe`/`provider`
    whose timestamp falls within [start, end] (both inclusive dates,
    matching GET /market-data/{symbol}/history's own start/end
    semantics), ordered oldest-first. Never contacts a provider -- this
    is the whole point: once bars are saved, they're readable with no
    network call and no API credentials involved at all.

    `provider` is required, not optional: the table's identity key
    includes provider (see db.py's schema comment for why), so the same
    symbol/timeframe/period can have independently-saved Alpaca rows
    and Massive rows that must not be silently merged into one
    ambiguous result set.
    """
    symbol = symbol.upper()
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt_exclusive = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT symbol, timeframe, provider, timestamp, open, high, low, close, volume
            FROM historical_bars
            WHERE symbol = ? AND timeframe = ? AND provider = ?
              AND timestamp >= ? AND timestamp < ?
            ORDER BY timestamp ASC
            """,
            (symbol, timeframe, provider, start_dt.isoformat(), end_dt_exclusive.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    return [
        HistoricalBar(
            symbol=row["symbol"],
            timeframe=row["timeframe"],
            provider=row["provider"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
        )
        for row in rows
    ]


def get_bars_in_range(
    *,
    symbol: str,
    timeframe: str,
    provider: str,
    start: datetime,
    end: datetime,
    db_path: str | Path | None = None,
) -> list[HistoricalBar]:
    """Same read as get_bars() above, but bounded by full timestamps
    (both inclusive) rather than whole calendar dates. Added for the
    OOS / Holdout Partition Framework (v0.1.29, app/oos/access.py),
    whose partition boundaries are timestamps, not dates -- a 1-minute
    or 5-minute timeframe partition needs an intraday cut point (e.g.
    "development ends at 2025-06-30T15:59:00Z"), which get_bars()'s
    whole-day granularity cannot express. get_bars() itself is
    unmodified and remains the right call for every existing caller
    (Research, Backtesting), whose own start_date/end_date are
    calendar dates, not timestamps.

    A naive (no tzinfo) `start`/`end` is assumed UTC, same convention
    as _to_storage_timestamp() below (what every stored bar's own
    timestamp was already normalized through).
    """
    symbol = symbol.upper()
    start_ts = _to_storage_timestamp(start)
    end_ts = _to_storage_timestamp(end)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT symbol, timeframe, provider, timestamp, open, high, low, close, volume
            FROM historical_bars
            WHERE symbol = ? AND timeframe = ? AND provider = ?
              AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
            """,
            (symbol, timeframe, provider, start_ts, end_ts),
        ).fetchall()
    finally:
        conn.close()

    return [
        HistoricalBar(
            symbol=row["symbol"],
            timeframe=row["timeframe"],
            provider=row["provider"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
        )
        for row in rows
    ]


def get_quarantined_bars(
    *,
    symbol: str,
    timeframe: str,
    provider: str,
    start: date,
    end: date,
    db_path: str | Path | None = None,
) -> list[QuarantinedBarRecord]:
    """The read side of save_rejected_bars() -- the audit trail check
    #12 asked for: every bar that was rejected for this symbol/
    timeframe/provider/date-range, and why, oldest-first. Same filter
    shape as get_bars() (date range is inclusive of `start`/`end`),
    deliberately: this is the same "read what was persisted" pattern
    applied to the quarantine table instead of historical_bars.
    """
    symbol = symbol.upper()
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt_exclusive = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT symbol, timeframe, provider, timestamp, open, high, low, close, volume,
                   ingested_at, validation_errors
            FROM quarantined_bars
            WHERE symbol = ? AND timeframe = ? AND provider = ?
              AND timestamp >= ? AND timestamp < ?
            ORDER BY timestamp ASC
            """,
            (symbol, timeframe, provider, start_dt.isoformat(), end_dt_exclusive.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    return [
        QuarantinedBarRecord(
            bar=HistoricalBar(
                symbol=row["symbol"],
                timeframe=row["timeframe"],
                provider=row["provider"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            ),
            ingested_at=datetime.fromisoformat(row["ingested_at"]),
            validation_errors=json.loads(row["validation_errors"]),
        )
        for row in rows
    ]


def _to_storage_timestamp(value: datetime) -> str:
    """Normalizes to a UTC, fixed-offset ISO-8601 string before storing
    as TEXT -- so timestamp comparisons in get_bars()'s WHERE clause
    (plain string comparison, no SQLite date functions) stay correct
    lexicographically. A naive datetime (no tzinfo) is assumed UTC
    rather than rejected -- HistoricalBar.timestamp is always tz-aware
    in practice (every provider attaches one), but a defensive default
    beats a crash for a value from an untrusted-in-principle caller."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
