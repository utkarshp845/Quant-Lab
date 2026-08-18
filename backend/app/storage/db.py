"""SQLite connection + schema for persisted historical bars (v0.1.17;
validation metadata + quarantine table added v0.1.18; raw-ingestion
table added v0.1.19; Research v1's experiments/experiment_events tables
added v0.1.20; Feature Engine v1's historical_features table added
v0.1.21; experiments/experiment_events reshaped for feature-based
conditions, v0.1.24; Backtesting v1's backtests/backtest_signals tables
added v0.1.25; OOS / Holdout Partition Framework v1's oos_partitions
table added v0.1.29; Experiment Freeze & Provenance v1's
experiment_freeze_snapshots table, and experiments' lifecycle_state/
oos_partition_id/hypothesis_hash/frozen_at/archived_at columns, added
v0.1.30; OOS Evaluation v1's oos_evaluations/oos_evaluation_signals
tables added v0.1.31; OOS Evidence Accumulation V1's
experiment_oos_periods table added v0.1.33 -- purely additive, no
ALTER TABLE needed anywhere else, since it only LINKS already-existing
experiments/oos_partitions rows rather than adding columns to either;
OOS Statistical Review V1's oos_statistical_reviews table added
v0.1.34 -- also purely additive, since it only WRITES new, immutable
review rows and reads every other table).

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
-- v0.1.30 (Experiment Freeze & Provenance v1): lifecycle_state/
-- oos_partition_id/hypothesis_hash/frozen_at/archived_at added --
-- a SECOND, independent lifecycle axis alongside `status` above (see
-- app/models/research.py::ExperimentLifecycleState's own docstring for
-- why these are separate, not a replacement). `lifecycle_state`
-- defaults to 'draft' so every experiment ever created (including
-- every one that predates this version, via the ALTER TABLE migration
-- below) starts, and stays, exactly as unaffected as before unless
-- someone explicitly freezes it. `hypothesis_hash`/`frozen_at` are
-- NULL until that freeze happens; `oos_partition_id` may be set any
-- time while still 'draft' (see app/api/experiment_freeze.py's
-- associate-partition route) and becomes immutable the same instant
-- freezing does. See experiment_freeze_snapshots below for the actual
-- immutable, point-in-time snapshot -- these five columns describe
-- the LIVE experiment row's current lifecycle position, not a
-- substitute for that snapshot.
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
    error_message TEXT,
    lifecycle_state TEXT NOT NULL DEFAULT 'draft',
    oos_partition_id TEXT,
    hypothesis_hash TEXT,
    frozen_at TEXT,
    archived_at TEXT
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

-- Backtesting v1 (app/backtesting/, app/api/backtesting.py, app/storage/
-- backtest_repository.py): one row per BACKTEST RUN -- a Backtest
-- always references an existing `experiments` row (experiment_id, no
-- foreign-key constraint declared -- this app has none anywhere else
-- either, e.g. experiment_events.experiment_id above; the API route is
-- what enforces the reference actually exists, same as every other
-- lookup in this app). symbol/timeframe/provider/feature_contract_version
-- are copied from the referenced Experiment AT CREATION TIME (see
-- app/models/backtesting.py::Backtest.new()) so a Backtest row is fully
-- self-describing without a join back to `experiments` -- and, like
-- `experiments` itself, immutable after creation except for
-- status/completed_at/results_json/error_message, the identical
-- reproducibility guarantee. windows_json is a JSON list[int] (bar
-- counts, e.g. [5, 15, 30, 60]) -- the same "structured, per-row-sized
-- data as JSON text" pattern experiments.conditions_json already uses,
-- since the window count is caller-configurable, not fixed.
CREATE TABLE IF NOT EXISTS backtests (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    provider TEXT NOT NULL,
    windows_json TEXT NOT NULL,
    feature_contract_version TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    results_json TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_backtests_experiment
    ON backtests (experiment_id);

-- One row per individual, ENTERABLE signal a backtest run found -- see
-- app/models/backtesting.py::BacktestSignal's own docstring for why
-- aggregate statistics alone are never enough here either (the same
-- "do not only store aggregate statistics" requirement Research v1
-- already applies). Re-running a backtest (POST /backtests/{id}/run)
-- DELETEs and replaces this backtest's rows rather than appending --
-- see backtest_repository.replace_signals(), identical reasoning to
-- experiment_events.replace_events() above: a backtest run is a
-- reproducible computation over a fixed dataset, so only the latest
-- run's signals are kept. feature_values_json (feature_id -> observed
-- value) and outcomes_json (a list of one object per measured window --
-- window_bars/outcome_timestamp/forward_return/mfe/mae) are both JSON:
-- feature_values_json for the identical reason
-- experiment_events.condition_values_json already is (a variable-shape
-- map keyed by whichever feature_ids this backtest's conditions
-- reference), and outcomes_json because a signal can have anywhere from
-- one to len(windows) measured outcomes depending on how close it fell
-- to the end of the dataset -- see BacktestSignal's own docstring.
CREATE TABLE IF NOT EXISTS backtest_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    backtest_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    signal_timestamp TEXT NOT NULL,
    entry_timestamp TEXT NOT NULL,
    entry_price REAL NOT NULL,
    feature_values_json TEXT NOT NULL,
    outcomes_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backtest_signals_backtest
    ON backtest_signals (backtest_id, signal_timestamp);

-- OOS / Holdout Partition Framework v1 (app/models/oos_partition.py,
-- app/oos/, app/storage/oos_partition_repository.py,
-- app/api/oos_partitions.py): one row per PARTITION DEFINITION -- a
-- declaration that an existing symbol/timeframe/provider's already-
-- stored `historical_bars` rows are split into a development window
-- and a later, non-overlapping holdout window. No OHLCV data is
-- duplicated onto this table; development_start/development_end/
-- holdout_start/holdout_end are only date-range REFERENCES a reader
-- (app/oos/access.py) applies to `historical_bars` at query time.
--
-- `id` is a DETERMINISTIC hash of provider/symbol/timeframe/the four
-- boundary timestamps (see app/models/oos_partition.py::
-- compute_partition_id()) -- NOT a random uuid4 like `experiments.id`/
-- `backtests.id` -- so creating "the same partition" twice always
-- produces the same primary key, and the UNIQUE-by-PRIMARY-KEY INSERT
-- OR IGNORE in oos_partition_repository.save_partition() makes that
-- idempotent rather than a duplicate row (requirement 5,
-- determinism). Every column here is set once, at creation, and never
-- updated -- there is no "edit a partition" operation (see
-- OOSPartition's own docstring for why a mutable boundary would defeat
-- the point of drawing one).
CREATE TABLE IF NOT EXISTS oos_partitions (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    provider TEXT NOT NULL,
    development_start TEXT NOT NULL,
    development_end TEXT NOT NULL,
    holdout_start TEXT NOT NULL,
    holdout_end TEXT NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oos_partitions_lookup
    ON oos_partitions (symbol, timeframe, provider);

-- Experiment Freeze & Provenance v1 (app/models/experiment_freeze.py,
-- app/research/lifecycle.py, app/storage/
-- experiment_freeze_repository.py, app/api/experiment_freeze.py): one
-- row per EXPERIMENT THAT HAS BEEN FROZEN, ever -- `experiment_id` is
-- the PRIMARY KEY, not an autoincrement id, because there is no
-- re-freeze operation (freezing is one-way, requirement 1's state
-- machine) and therefore never more than one snapshot per experiment.
-- Every column here is a COPY of the frozen experiment's own field
-- values at the instant of freezing -- not a foreign key back to
-- `experiments` for anything except the id itself -- so this row
-- answers "what hypothesis was evaluated" correctly even in the
-- hypothetical case that the live `experiments` row were ever changed
-- or deleted after the freeze (requirement 5: "do not rely solely on
-- the current mutable Experiment row"). `hypothesis_hash` is the
-- deterministic provenance hash (requirement 4) computed from exactly
-- the columns below that define research meaning -- NOT
-- name/hypothesis (free text) and NOT oos_partition_id (see
-- app/research/lifecycle.py::canonicalize_hypothesis()'s own
-- docstring for the full list and why each exclusion is deliberate).
CREATE TABLE IF NOT EXISTS experiment_freeze_snapshots (
    experiment_id TEXT PRIMARY KEY,
    hypothesis_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    provider TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    feature_contract_version TEXT NOT NULL,
    conditions_json TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    oos_partition_id TEXT,
    experiment_created_at TEXT NOT NULL,
    frozen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experiment_freeze_snapshots_hash
    ON experiment_freeze_snapshots (hypothesis_hash);

-- OOS Evaluation v1 (app/models/oos_evaluation.py, app/oos_evaluation/,
-- app/storage/oos_evaluation_repository.py, app/api/oos_evaluation.py):
-- one row per RUN of a frozen experiment's hypothesis against its
-- linked OOS partition's holdout data. `id` is a random id (like
-- `experiments.id`/`backtests.id`, NOT a deterministic hash like
-- `oos_partitions.id`) precisely because running the SAME frozen
-- experiment twice must produce TWO rows, not one -- this table is
-- APPEND-ONLY: there is no UPDATE, no REPLACE, and no DELETE anywhere
-- in app/storage/oos_evaluation_repository.py. Every column here is a
-- copy of a fact already fixed at freeze time (hypothesis_hash,
-- frozen_snapshot_id which equals the frozen experiment's own id,
-- feature_contract_version, symbol/timeframe/provider) or fixed at
-- partition-creation time (oos_partition_id, holdout_start/
-- holdout_end) -- so this row answers "what was evaluated" without
-- ever joining back to the live, mutable `experiments` row.
-- results_json is NULL for a FAILED evaluation (status distinguishes
-- "zero signals found, a legitimate result" from "the pipeline itself
-- raised" -- see app/models/oos_evaluation.py::OOSEvaluationStatus).
CREATE TABLE IF NOT EXISTS oos_evaluations (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    hypothesis_hash TEXT NOT NULL,
    frozen_snapshot_id TEXT NOT NULL,
    oos_partition_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    provider TEXT NOT NULL,
    holdout_start TEXT NOT NULL,
    holdout_end TEXT NOT NULL,
    feature_contract_version TEXT NOT NULL,
    outcome_horizon_minutes INTEGER NOT NULL,
    outcome_window_bars INTEGER,
    signal_count INTEGER NOT NULL,
    results_json TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    frozen_at TEXT NOT NULL,
    evaluated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oos_evaluations_experiment
    ON oos_evaluations (experiment_id, evaluated_at);
CREATE INDEX IF NOT EXISTS idx_oos_evaluations_hash
    ON oos_evaluations (hypothesis_hash);

-- One row per individual, ENTERABLE OOS signal a run of
-- oos_evaluations found -- the SAME "do not only store aggregate
-- statistics" requirement app/storage/db.py's own
-- experiment_events/backtest_signals tables already apply, extended
-- here. Also append-only: an evaluation's signals are inserted once,
-- alongside its parent oos_evaluations row, and never touched again --
-- there is no re-run-in-place for an OOS evaluation (see
-- oos_evaluations' own comment above), so unlike
-- experiment_events/backtest_signals (which DELETE-and-replace on
-- every re-run of the SAME experiment/backtest id), a re-run here
-- simply produces an entirely new evaluation_id with its own, separate
-- signal rows.
CREATE TABLE IF NOT EXISTS oos_evaluation_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    signal_timestamp TEXT NOT NULL,
    entry_timestamp TEXT NOT NULL,
    entry_price REAL NOT NULL,
    feature_values_json TEXT NOT NULL,
    outcomes_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oos_evaluation_signals_evaluation
    ON oos_evaluation_signals (evaluation_id, signal_timestamp);

-- OOS Evidence Accumulation V1 (app/models/oos_evidence.py,
-- app/oos_evidence/, app/storage/oos_evidence_repository.py,
-- app/api/oos_evidence.py): one row per (experiment_id,
-- oos_partition_id) LINK -- "this already-existing, independently
-- created OOSPartition (still created the normal way, via the
-- existing, UNMODIFIED oos_partitions table above) is registered as an
-- additional OOS evaluation period for this already-frozen
-- experiment". No OHLCV data, no partition boundary, and no experiment
-- field is duplicated onto this table beyond what a reader needs to
-- see what a period covers without a join -- symbol/timeframe/
-- provider/oos_start/oos_end (== the referenced partition's own
-- holdout_start/holdout_end) are copied here at registration time,
-- the identical "self-describing row" convention oos_evaluations'
-- own comment above already applies. PRIMARY KEY is the pair itself
-- (never a separate autoincrement id): a partition may be registered
-- for more than one experiment in principle, but never twice for the
-- SAME experiment -- app/oos_evidence/period.py::validate_new_period()
-- is what actually enforces every leakage/overlap rule BEFORE a row
-- is ever written here; this table only records the outcome of an
-- already-validated registration. Every column is set once, at
-- registration, and never updated -- there is no edit endpoint, the
-- same "no mutation after creation" rule this app applies everywhere
-- else a definition, rather than a run's status, is being recorded.
CREATE TABLE IF NOT EXISTS experiment_oos_periods (
    experiment_id TEXT NOT NULL,
    oos_partition_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    provider TEXT NOT NULL,
    oos_start TEXT NOT NULL,
    oos_end TEXT NOT NULL,
    label TEXT,
    registered_at TEXT NOT NULL,
    PRIMARY KEY (experiment_id, oos_partition_id)
);
CREATE INDEX IF NOT EXISTS idx_experiment_oos_periods_experiment
    ON experiment_oos_periods (experiment_id, oos_start);

-- OOS Statistical Review V1 (app/models/oos_statistical_review.py,
-- app/oos_statistical_review/, app/storage/
-- oos_statistical_review_repository.py, app/api/
-- oos_statistical_review.py): one row per REVIEW RUN -- a formal,
-- read-only statistical review of a frozen experiment's own
-- accumulated OOS evidence (oos_evaluations above, both the originally
-- frozen-time-linked partition's own evaluation(s) and every OOS
-- Evidence Accumulation V1 period's own evaluation(s)). `id` is a
-- random id (like `oos_evaluations.id`, NOT a deterministic hash like
-- `oos_partitions.id`) precisely because running the SAME review again
-- against the SAME evidence must produce a NEW row with IDENTICAL
-- analytical content, never overwriting a prior review -- this table
-- is APPEND-ONLY: there is no UPDATE, no REPLACE, and no DELETE
-- anywhere in app/storage/oos_statistical_review_repository.py.
-- `review_json` holds the entire OOSStatisticalReview (every input,
-- every config value, every computed statistic, the verdict, and the
-- reasoning) as one JSON blob -- see that repository module's own
-- comment for why (a complex, nested shape nothing else in this app
-- needs to query into a sub-field of); `experiment_id`/
-- `hypothesis_hash`/`verdict`/`created_at` are pulled out as their own
-- columns because those are the four things a caller actually filters/
-- orders on. This feature never writes to any OTHER table -- no
-- `experiments`, `experiment_freeze_snapshots`, `oos_partitions`,
-- `oos_evaluations`, `oos_evaluation_signals`, `historical_bars`, or
-- `historical_features` row is ever touched by anything in
-- app/oos_statistical_review/ or app/api/oos_statistical_review.py.
CREATE TABLE IF NOT EXISTS oos_statistical_reviews (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    hypothesis_hash TEXT NOT NULL,
    verdict TEXT NOT NULL,
    created_at TEXT NOT NULL,
    review_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oos_statistical_reviews_experiment
    ON oos_statistical_reviews (experiment_id, created_at);
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
    # v0.1.30 (Experiment Freeze & Provenance v1): additive, matching
    # every migration above -- an existing pre-v0.1.30 experiment
    # starts 'draft' (unaffected, per lifecycle_state's own column
    # comment on the CREATE TABLE above) with every other new column
    # NULL until explicitly frozen.
    ("lifecycle_state", "ALTER TABLE experiments ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'draft'"),
    ("oos_partition_id", "ALTER TABLE experiments ADD COLUMN oos_partition_id TEXT"),
    ("hypothesis_hash", "ALTER TABLE experiments ADD COLUMN hypothesis_hash TEXT"),
    ("frozen_at", "ALTER TABLE experiments ADD COLUMN frozen_at TEXT"),
    ("archived_at", "ALTER TABLE experiments ADD COLUMN archived_at TEXT"),
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
    build the table in its current shape under a temporary name, copy
    every row across, drop the old table, rename the new one into
    place. Must run AFTER _migrate_legacy_experiment_conditions() above
    AND after _migrate_table(conn, "experiments", _EXPERIMENTS_MIGRATIONS)
    -- it depends on every row's `conditions_json`/
    `feature_contract_version`/`lifecycle_state` (v0.1.30) already being
    populated by those two steps (the new `experiments` table declares
    the first two NOT NULL, and the rebuild below carries
    `lifecycle_state`/`oos_partition_id`/`hypothesis_hash`/`frozen_at`/
    `archived_at` across too -- they already exist on the OLD-named
    `experiments` table by the time this runs, added by the
    ALTER TABLE calls in _EXPERIMENTS_MIGRATIONS just before, same as
    `conditions_json` always has been). A no-op (checked via
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
                error_message TEXT,
                lifecycle_state TEXT NOT NULL DEFAULT 'draft',
                oos_partition_id TEXT,
                hypothesis_hash TEXT,
                frozen_at TEXT,
                archived_at TEXT
            );
            INSERT INTO experiments_v0124
                (id, name, hypothesis, symbol, start_date, end_date, timeframe, provider,
                 conditions_json, outcome_json, feature_contract_version, status, created_at,
                 completed_at, results_json, error_message, lifecycle_state, oos_partition_id,
                 hypothesis_hash, frozen_at, archived_at)
            SELECT id, name, hypothesis, symbol, start_date, end_date, timeframe, provider,
                   conditions_json, outcome_json, feature_contract_version, status, created_at,
                   completed_at, results_json, error_message, lifecycle_state, oos_partition_id,
                   hypothesis_hash, frozen_at, archived_at
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
