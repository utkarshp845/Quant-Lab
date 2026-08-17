"""SQLite connection + schema for persisted historical bars (v0.1.17;
validation metadata + quarantine table added v0.1.18; raw-ingestion
table added v0.1.19).

Why SQLite, specifically: this is a local, single-user, no-auth
educational tool (see README section 1) -- a server-based RDBMS
(Postgres/MySQL) would mean a second process to install, configure, and
keep alive for a feature that's narrowly scoped to "persist bars, read
them back." SQLite is a real relational database with real constraints
(the UNIQUE index below is enforced by the engine, not application
code) -- it just happens to need no server, ships with every Python
install (the stdlib `sqlite3` module -- no new entry in requirements.txt),
and stores everything in one plain file. This is the same "no
unnecessary infrastructure" judgment app/config.py's docstring already
applies to skipping a settings library until the app outgrows
os.environ.get() -- extended here to skipping a database server and an
ORM until this app outgrows a single table.

Connection lifecycle: a new connection is opened and closed for every
repository call (see historical_bar_repository.py), not held open as a
long-lived singleton. sqlite3 connections aren't safe to share across
threads by default, and FastAPI can run a sync `def` route in a worker
thread pool -- opening short-lived, per-call connections sidesteps that
entirely, and a local SQLite file has no meaningful connection-overhead
cost to amortize for this app's traffic (a single human, clicking
buttons).
"""

import sqlite3
from pathlib import Path

from app import config

# provider+symbol+timeframe+timestamp is the natural identity of a bar
# (see the storage layer's design discussion): the same OHLCV values
# arriving twice for that combination is a duplicate fetch, not a new
# observation, and must not create a second row. Provider stays IN the
# key deliberately -- Alpaca and Massive can (and do, per
# scripts/cross_validate_providers.py) report slightly different O/H/L/C
# for what's nominally "the same" bar, and both are worth keeping.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS historical_bars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (provider, symbol, timeframe, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_historical_bars_lookup
    ON historical_bars (symbol, timeframe, provider, timestamp);

-- v0.1.18: bars that app/ingestion/bar_validation.py rejected outright
-- (impossible OHLCV values, an in-batch duplicate, ...) -- see that
-- module's docstring for the full rule list. Deliberately no UNIQUE
-- constraint: this is an append-only audit log of every rejection a
-- save/ingestion attempt produced, not a deduplicated table -- the
-- same bad bar arriving on a retry is worth logging again, not
-- silently collapsing into one row.
CREATE TABLE IF NOT EXISTS quarantined_bars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    ingested_at TEXT NOT NULL,
    validation_errors TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quarantined_bars_lookup
    ON quarantined_bars (symbol, timeframe, provider, timestamp);

-- v0.1.19: the ORIGINAL provider response / CSV content, captured
-- before any parsing, validation, or normalization happens to it --
-- see app/storage/raw_ingestion_repository.py. Deliberately NOT
-- shaped like historical_bars/quarantined_bars: those are one row per
-- BAR (OHLCV columns); this is one row per INGESTION REQUEST (one
-- provider fetch call, or one CSV upload), holding the response/file
-- as opaque text in `raw_payload` -- forcing it into OHLCV columns
-- would defeat the entire point of a raw stage, which is to preserve
-- whatever shape the source actually used (Alpaca's `t`/`o`/`h`/`l`/
-- `c`/`v` keys, Massive's own key names, a CSV's original column
-- headers and cell text -- not this app's canonical field names).
-- `symbol`/`timeframe`/`source_start`/`source_end` are nullable: a
-- CSV upload may cover more than one symbol, and not every source
-- has a meaningful timeframe. `timeframe` records whatever value the
-- SOURCE itself was actually asked for -- Alpaca's own "1Day"/"1Min"
-- vocabulary, Massive's own "day"/"minute" timespan, this app's "1d"
-- for a CSV upload (which has no provider vocabulary of its own) --
-- not a single normalized value across sources, on purpose: this is a
-- raw/audit column, not a query key other tables join against, so
-- preserving what was truly asked for beats forcing one vocabulary
-- onto every source. No UNIQUE constraint, same reasoning as
-- quarantined_bars: this is an append-only log of every ingestion
-- attempt, not a deduplicated table -- re-fetching the same
-- symbol/range twice (e.g. auto_ingest.py's overlapping lookback
-- window) legitimately produces two raw rows, one per real request
-- actually made.
CREATE TABLE IF NOT EXISTS raw_ingestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    symbol TEXT,
    timeframe TEXT,
    source_start TEXT,
    source_end TEXT,
    ingested_at TEXT NOT NULL,
    content_type TEXT NOT NULL,
    raw_payload TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_raw_ingestions_lookup
    ON raw_ingestions (source, symbol, ingested_at);
"""

# v0.1.18: columns added to an already-shipped table, so CREATE TABLE IF
# NOT EXISTS above (a no-op against an existing v0.1.17 database file)
# isn't enough on its own -- an existing local historical_bars.db from
# before this version needs these columns added in place. Each entry is
# (column name, full "ADD COLUMN ..." clause); _migrate() below only
# runs the ones PRAGMA table_info says are actually missing, so this
# stays a no-op on a database that already has them (including a brand
# new one, where _SCHEMA's CREATE TABLE already included these columns
# from the start -- see historical_bars' definition above... actually
# it doesn't, on purpose: keeping column additions here, in one place,
# rather than duplicated in both _SCHEMA's CREATE TABLE and here, is
# simpler than making sure two copies of "the current schema" never
# drift apart).
_HISTORICAL_BARS_MIGRATIONS: list[tuple[str, str]] = [
    ("validation_status", "ALTER TABLE historical_bars ADD COLUMN validation_status TEXT NOT NULL DEFAULT 'valid'"),
    ("validation_warnings", "ALTER TABLE historical_bars ADD COLUMN validation_warnings TEXT NOT NULL DEFAULT '[]'"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(historical_bars)")}
    for column_name, alter_statement in _HISTORICAL_BARS_MIGRATIONS:
        if column_name not in existing_columns:
            conn.execute(alter_statement)
    conn.commit()


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Opens a new connection to the historical-bars database, creating
    the file/parent directory and the schema (idempotent -- CREATE TABLE
    IF NOT EXISTS / CREATE INDEX IF NOT EXISTS) if this is the first
    call ever made against this path, and migrating an existing v0.1.17
    file forward (see _migrate() above) if it's missing v0.1.18's new
    columns.

    `db_path` is explicit-override-only (used by tests to point at a
    throwaway file -- see tests/test_historical_bar_repository.py);
    real callers never pass it and get config.get_database_path().
    """
    path = Path(db_path) if db_path is not None else Path(config.get_database_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn
