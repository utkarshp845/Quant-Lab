"""SQLite connection + schema for persisted historical bars (v0.1.17;
validation metadata + quarantine table added v0.1.18; raw-ingestion
table added v0.1.19; Research v1's experiments/experiment_events tables
added v0.1.20; Feature Engine v1's historical_features table added
v0.1.21; experiments/experiment_events reshaped for feature-based
conditions, v0.1.24).

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

import json
import re
import sqlite3
from pathlib import Path

from app import config
from app.models.features import FEATURE_CONTRACT_VERSION

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

-- Research v1 (app/research/, app/api/research.py, app/storage/
-- research_repository.py): one row per EXPERIMENT -- see
-- experiment_events below for the individual per-signal observations.
-- conditions_json/outcome_json hold list[FeatureCondition]/Outcome
-- (app/models/research.py) as opaque JSON, the same "structured data
-- as JSON text alongside named columns for whatever IS queried
-- directly" pattern raw_ingestions' own `metadata` column already uses
-- above -- there is exactly one Outcome per experiment, and the
-- ANDed conditions list has no fixed size, so a second normalized
-- table for either would be pure overhead with no query this app
-- actually needs. Every column here except
-- status/completed_at/results_json/error_message is set once, at
-- creation, and never updated again -- see Experiment's own docstring
-- for why that is the entire reproducibility guarantee: re-running an
-- experiment can never change what it was asked to measure.
--
-- v0.1.24 (Feature <-> Research integration): condition_json (a single
-- Condition: metric/operator/threshold) became conditions_json (a JSON
-- LIST of FeatureCondition: feature_id/operator/value), and
-- feature_contract_version was added -- requirement 6's reproducibility
-- guarantee (see Experiment's own docstring: a run only evaluates
-- against FeatureRecords whose OWN feature_contract_version matches
-- this stored value). The old condition_json column is left in place,
-- unused by any code path after this version, rather than dropped --
-- same "additive-only migration, never drop a column" precedent
-- _HISTORICAL_BARS_MIGRATIONS below already established; see
-- _migrate_legacy_experiment_conditions() for how an EXISTING
-- database's old-shape rows are converted forward automatically
-- (every pre-v0.1.24 Condition was always a "{N}m_return" trailing
-- return, which maps losslessly onto the new "price.return_{N}m"
-- feature_id -- not a guess).
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    symbol TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    provider TEXT NOT NULL,
    conditions_json TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    feature_contract_version TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    results_json TEXT,
    error_message TEXT
);

-- One row per QUALIFYING SIGNAL a run of an experiment found -- see
-- app/models/research.py::ExperimentEvent's docstring for why
-- aggregate statistics alone are never enough (spec section 5: "do not
-- only store aggregate statistics"). Re-running an experiment (POST
-- /research/experiments/{id}/run) DELETEs and replaces this
-- experiment's rows rather than appending a second copy -- see
-- research_repository.replace_events() -- so this always holds "the
-- events from the most recently completed run", not an ever-growing
-- log. That is a deliberate departure from quarantined_bars'/
-- raw_ingestions' append-only design above: those log real-world
-- events (a rejection, an ingestion attempt) that each genuinely
-- happened again on a retry; an experiment run is a reproducible
-- computation over a fixed dataset that should look identical every
-- time it executes, so keeping only the latest run's events is what
-- makes "re-run and get the same result" a meaningful, checkable
-- property instead of an ever-growing pile of duplicates.
-- v0.1.24: condition_value (a single REAL) became condition_values_json
-- (a JSON object, feature_id -> observed value -- see ExperimentEvent's
-- own docstring for why one number was no longer enough once an
-- experiment can have more than one ANDed condition). The old
-- condition_value column is left in place, unused, same reasoning as
-- experiments.condition_json above. An EXISTING database's old rows
-- are NOT individually converted (unlike experiments.conditions_json):
-- replace_events() deletes and re-inserts every one of an experiment's
-- events on every run, so simply re-running an experiment (POST
-- .../run) is what repopulates condition_values_json correctly -- see
-- research_repository._row_to_event()'s NULL-safe fallback for a row
-- that hasn't been re-run since this version shipped.
CREATE TABLE IF NOT EXISTS experiment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_timestamp TEXT NOT NULL,
    signal_price REAL NOT NULL,
    condition_values_json TEXT NOT NULL,
    outcome_timestamp TEXT NOT NULL,
    outcome_price REAL NOT NULL,
    outcome_value REAL NOT NULL,
    success INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experiment_events_experiment
    ON experiment_events (experiment_id, signal_timestamp);

-- Feature Engine v1 (app/features/, app/api/features.py, app/storage/
-- feature_repository.py): one row per (symbol, timeframe, provider,
-- timestamp) -- the SAME identity key as historical_bars above, since
-- a FeatureRecord is a 1:1 transform of one normalized bar, not an
-- independent event. Named REAL columns, one per leaf feature in the
-- fixed v1 contract (app/models/features.py), rather than a JSON blob
-- -- unlike experiments'/outcomes' per-experiment-defined shape
-- (necessarily JSON, see the comment above), this contract is fixed
-- and identical for every row, so explicit typed columns match
-- historical_bars' own OHLCV-as-columns convention and let a future
-- consumer filter/query on individual feature values directly in SQL.
-- Every feature column is nullable: "if insufficient historical data
-- exists, return null rather than zero" (this feature's rule 3)
-- applies uniformly, including to `market_context_applicable`'s own
-- sub-columns when that symbol IS configured for market context but a
-- specific value could not be computed. `market_context_applicable`
-- itself is NOT nullable -- it distinguishes "this symbol is not
-- configured for market context at all" (0, every spy_/qqq_/
-- relative_strength_ column NULL for a structural reason) from
-- "configured, but this particular value could not be computed" (1,
-- with some of those same columns NULL for a data reason) -- two
-- different meanings a bare NULL alone could not tell apart.
-- Recomputing features for a symbol/timeframe/provider/timestamp
-- REPLACES the existing row (see feature_repository.save_features()) --
-- unlike historical_bars' INSERT OR IGNORE, which deliberately
-- preserves the first-ever ingested value: a feature row is entirely
-- DERIVED from historical_bars, so a recompute (a bug fix, a formula
-- change) should overwrite stale derived data, the same reasoning
-- experiment_events' replace-not-append design above already applies.
CREATE TABLE IF NOT EXISTS historical_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    provider TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    calculated_at TEXT NOT NULL,
    feature_contract_version TEXT NOT NULL,
    market_context_applicable INTEGER NOT NULL,
    -- PRICE
    return_5m REAL,
    return_15m REAL,
    return_30m REAL,
    return_60m REAL,
    -- VOLUME
    volume INTEGER NOT NULL,
    relative_volume REAL,
    volume_acceleration REAL,
    -- VOLATILITY
    realized_volatility REAL,
    atr REAL,
    volatility_ratio REAL,
    volatility_percentile REAL,
    -- MARKET CONTEXT
    spy_return_5m REAL,
    spy_return_15m REAL,
    spy_return_30m REAL,
    spy_return_60m REAL,
    qqq_return_5m REAL,
    qqq_return_15m REAL,
    qqq_return_30m REAL,
    qqq_return_60m REAL,
    relative_strength_spy_5m REAL,
    relative_strength_spy_15m REAL,
    relative_strength_spy_30m REAL,
    relative_strength_spy_60m REAL,
    relative_strength_qqq_5m REAL,
    relative_strength_qqq_15m REAL,
    relative_strength_qqq_30m REAL,
    relative_strength_qqq_60m REAL,
    -- PRICE POSITION
    vwap_distance REAL,
    ma20_distance REAL,
    ma50_distance REAL,
    intraday_range_position REAL,
    UNIQUE (symbol, timeframe, provider, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_historical_features_lookup
    ON historical_features (symbol, timeframe, provider, timestamp);
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

# v0.1.24 (Feature <-> Research integration): same additive-only pattern
# as _HISTORICAL_BARS_MIGRATIONS above, applied to `experiments`.
# `conditions_json` is added NULLABLE here (an existing row has no
# value to backfill it with directly -- see
# _migrate_legacy_experiment_conditions() below, which fills it in from
# the old `condition_json` column right after this runs) even though a
# BRAND NEW database's `experiments` table (created fresh from _SCHEMA
# above) declares it NOT NULL -- that's fine: PRAGMA table_info only
# reports a column "missing" here for an EXISTING table that predates
# it, and a fresh table already has it from CREATE TABLE, so this ALTER
# never runs against one. `feature_contract_version` gets a real
# default (the CURRENT contract version) since every pre-v0.1.24
# experiment's conditions genuinely were computed against exactly that
# contract's `return_{N}m` features (see the data migration below).
_EXPERIMENTS_MIGRATIONS: list[tuple[str, str]] = [
    ("conditions_json", "ALTER TABLE experiments ADD COLUMN conditions_json TEXT"),
    (
        "feature_contract_version",
        f"ALTER TABLE experiments ADD COLUMN feature_contract_version TEXT NOT NULL DEFAULT '{FEATURE_CONTRACT_VERSION}'",
    ),
]

# v0.1.24: `condition_values_json` added nullable -- unlike
# `experiments.conditions_json`, existing `experiment_events` rows are
# NOT individually data-migrated (see the schema comment on
# experiment_events above for why: replace_events() already deletes
# and recreates every one of an experiment's events on every run, so a
# manual per-row conversion here would just be overwritten the next
# time anyone re-runs that experiment anyway).
_EXPERIMENT_EVENTS_MIGRATIONS: list[tuple[str, str]] = [
    ("condition_values_json", "ALTER TABLE experiment_events ADD COLUMN condition_values_json TEXT"),
]


def _migrate_table(conn: sqlite3.Connection, table: str, migrations: list[tuple[str, str]]) -> None:
    existing_columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for column_name, alter_statement in migrations:
        if column_name not in existing_columns:
            conn.execute(alter_statement)


def _migrate_legacy_experiment_conditions(conn: sqlite3.Connection) -> None:
    """One-time DATA migration (v0.1.24, not just a schema one): every
    experiment created before this version has exactly one Condition,
    always shaped {"metric": "{N}m_return", "operator": ..., "threshold": ...}
    -- app/models/research.py's OLD field validator rejected anything
    else, so this is a real, deterministic conversion, never a guess.
    "{N}m_return" maps losslessly onto the new "price.return_{N}m"
    feature_id (see app/features/vocabulary.py -- PriceFeatures.return_Nm
    is computed by the exact same trailing-return formula the old
    Condition evaluated directly on bars). Runs only against rows where
    `conditions_json` is still NULL (never migrated) -- a no-op on a
    database that already went through this once, or a brand-new one
    that never had an old-shape row to begin with.

    Guarded on `condition_json` actually existing as a column at all:
    a brand-new database's `experiments` table (created fresh from
    _SCHEMA above) never had that column in the first place -- it's
    only ever present on a database that predates v0.1.24.
    """
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(experiments)")}
    if "condition_json" not in existing_columns:
        return

    rows = conn.execute(
        "SELECT id, condition_json FROM experiments WHERE conditions_json IS NULL AND condition_json IS NOT NULL"
    ).fetchall()
    for row in rows:
        old_condition = json.loads(row["condition_json"])
        match = re.match(r"^(\d+)m_return$", old_condition["metric"])
        if not match:
            continue  # unrecognized old shape (should not happen -- the old validator only ever allowed this one) -- leave NULL rather than guess
        operator = "=" if old_condition["operator"] == "==" else old_condition["operator"]
        new_conditions = [
            {"feature_id": f"price.return_{match.group(1)}m", "operator": operator, "value": old_condition["threshold"]}
        ]
        conn.execute("UPDATE experiments SET conditions_json = ? WHERE id = ?", (json.dumps(new_conditions), row["id"]))


def _drop_legacy_not_null_columns(conn: sqlite3.Connection) -> None:
    """v0.1.24: the additive ALTER TABLE migrations above are NOT enough
    on their own to make an existing pre-v0.1.24 database usable --
    `experiments.condition_json` and `experiment_events.condition_value`
    are still declared NOT NULL on such a database (ALTER TABLE ADD
    COLUMN cannot remove a constraint on an EXISTING column), and
    current code no longer supplies either one on INSERT (both were
    REPLACED, not merely joined by a new column -- see the schema
    comments above). Confirmed by a real run against a real pre-v0.1.24
    database: creating a new experiment raised `sqlite3.IntegrityError:
    NOT NULL constraint failed: experiments.condition_json` before this
    existed.

    SQLite has no ALTER TABLE ... ALTER COLUMN / DROP NOT NULL -- this
    is SQLite's own documented procedure for a constraint change:
    build the table in its current (v0.1.24) shape under a temporary
    name, copy every row across, drop the old table, rename the new one
    into place. Must run AFTER _migrate_legacy_experiment_conditions()
    above -- it depends on every row's `conditions_json`/
    `feature_contract_version` already being populated (the new
    `experiments` table declares both NOT NULL). A no-op (checked via
    PRAGMA table_info, same guard as every migration in this file) on a
    database that's already been rebuilt once, or a brand-new one that
    was never in the old shape to begin with.
    """
    experiments_columns = {row["name"] for row in conn.execute("PRAGMA table_info(experiments)")}
    if "condition_json" in experiments_columns:
        conn.executescript(
            """
            CREATE TABLE experiments_v0124 (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                hypothesis TEXT NOT NULL,
                symbol TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                provider TEXT NOT NULL,
                conditions_json TEXT NOT NULL,
                outcome_json TEXT NOT NULL,
                feature_contract_version TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                results_json TEXT,
                error_message TEXT
            );
            INSERT INTO experiments_v0124
                (id, name, hypothesis, symbol, start_date, end_date, timeframe, provider,
                 conditions_json, outcome_json, feature_contract_version, status, created_at,
                 completed_at, results_json, error_message)
            SELECT id, name, hypothesis, symbol, start_date, end_date, timeframe, provider,
                   conditions_json, outcome_json, feature_contract_version, status, created_at,
                   completed_at, results_json, error_message
            FROM experiments;
            DROP TABLE experiments;
            ALTER TABLE experiments_v0124 RENAME TO experiments;
            """
        )

    events_columns = {row["name"] for row in conn.execute("PRAGMA table_info(experiment_events)")}
    if "condition_value" in events_columns:
        conn.executescript(
            """
            CREATE TABLE experiment_events_v0124 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                signal_timestamp TEXT NOT NULL,
                signal_price REAL NOT NULL,
                condition_values_json TEXT NOT NULL,
                outcome_timestamp TEXT NOT NULL,
                outcome_price REAL NOT NULL,
                outcome_value REAL NOT NULL,
                success INTEGER NOT NULL
            );
            INSERT INTO experiment_events_v0124
                (id, experiment_id, symbol, signal_timestamp, signal_price, condition_values_json,
                 outcome_timestamp, outcome_price, outcome_value, success)
            SELECT id, experiment_id, symbol, signal_timestamp, signal_price,
                   COALESCE(condition_values_json, '{}'),
                   outcome_timestamp, outcome_price, outcome_value, success
            FROM experiment_events;
            DROP TABLE experiment_events;
            ALTER TABLE experiment_events_v0124 RENAME TO experiment_events;
            CREATE INDEX IF NOT EXISTS idx_experiment_events_experiment
                ON experiment_events (experiment_id, signal_timestamp);
            """
        )


def _migrate(conn: sqlite3.Connection) -> None:
    _migrate_table(conn, "historical_bars", _HISTORICAL_BARS_MIGRATIONS)
    _migrate_table(conn, "experiments", _EXPERIMENTS_MIGRATIONS)
    _migrate_table(conn, "experiment_events", _EXPERIMENT_EVENTS_MIGRATIONS)
    _migrate_legacy_experiment_conditions(conn)
    _drop_legacy_not_null_columns(conn)
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
