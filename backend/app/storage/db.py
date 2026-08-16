"""SQLite connection + schema for persisted historical bars (v0.1.17).

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
"""


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Opens a new connection to the historical-bars database, creating
    the file/parent directory and the schema (idempotent -- CREATE TABLE
    IF NOT EXISTS / CREATE INDEX IF NOT EXISTS) if this is the first
    call ever made against this path.

    `db_path` is explicit-override-only (used by tests to point at a
    throwaway file -- see tests/test_historical_bar_repository.py);
    real callers never pass it and get config.get_database_path().
    """
    path = Path(db_path) if db_path is not None else Path(config.get_database_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn
