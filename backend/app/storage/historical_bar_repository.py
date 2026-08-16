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

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.models.market_data import HistoricalBar
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
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO historical_bars
                        (provider, symbol, timeframe, timestamp, open, high, low, close, volume, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
                # INSERT OR IGNORE: rowcount is 1 for a real insert, 0 when
                # the UNIQUE constraint silently ignored a duplicate --
                # see TestSaveBars::test_duplicate_bars_are_not_inserted_twice.
                inserted += cursor.rowcount
    finally:
        conn.close()

    return SaveResult(total=len(bars), inserted=inserted, skipped_duplicates=len(bars) - inserted)


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
