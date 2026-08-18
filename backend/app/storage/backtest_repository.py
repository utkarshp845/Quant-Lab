"""The backtests repository -- the ONLY module that writes SQL for
Backtesting v1's `backtests`/`backtest_signals` tables (see app/storage/
db.py's schema). Same boundary rule as app/storage/research_repository.py:
nothing outside this file (the API routes, the engine) touches sqlite3
or a SQL string for either table.

    app/backtesting/engine.py     bars + Experiment conditions -> BacktestSignal[] + BacktestResults (pure, no I/O)
    app/api/backtesting.py        HTTP glue: calls this module + engine.py + historical_bar_repository/feature_repository/research_repository
    Storage (THIS FILE)           Backtest / BacktestSignal <-> database rows, and back

Every function here takes/returns Backtest or BacktestSignal (app/
models/backtesting.py) -- never a raw sqlite3.Row past this file's own
boundary, matching research_repository.py's own rule.
"""

import json
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter

from app.models.backtesting import Backtest, BacktestResults, BacktestSignal, BacktestStatus, BacktestWindowOutcome
from app.storage.db import get_connection

_WindowsAdapter = TypeAdapter(list[int])
_FeatureValuesAdapter = TypeAdapter(dict[str, float | bool])
_OutcomesAdapter = TypeAdapter(list[BacktestWindowOutcome])


def save_backtest(backtest: Backtest, *, db_path: str | Path | None = None) -> None:
    """Inserts a brand-new backtest row -- always a DRAFT fresh out of
    Backtest.new(). backtests.id is the primary key, so this is only
    ever called once per backtest; every later change to that row goes
    through update_backtest_run() below instead."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO backtests
                    (id, experiment_id, symbol, timeframe, provider, windows_json,
                     feature_contract_version, status, created_at, completed_at, results_json, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    backtest.id,
                    backtest.experiment_id,
                    backtest.symbol,
                    backtest.timeframe,
                    backtest.provider,
                    _WindowsAdapter.dump_json(backtest.windows).decode(),
                    backtest.feature_contract_version,
                    backtest.status.value,
                    backtest.created_at.isoformat(),
                    backtest.completed_at.isoformat() if backtest.completed_at else None,
                    backtest.results.model_dump_json() if backtest.results else None,
                    backtest.error_message,
                ),
            )
    finally:
        conn.close()


def mark_running(backtest_id: str, *, db_path: str | Path | None = None) -> None:
    """Flips a backtest to RUNNING the instant POST .../run starts
    executing -- see research_repository.mark_running()'s own docstring
    for why this is a separate write from update_backtest_run()."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("UPDATE backtests SET status = ? WHERE id = ?", (BacktestStatus.RUNNING.value, backtest_id))
    finally:
        conn.close()


def update_backtest_run(
    backtest_id: str,
    *,
    status: BacktestStatus,
    completed_at: datetime,
    results: BacktestResults | None,
    error_message: str | None,
    db_path: str | Path | None = None,
) -> None:
    """Records the outcome of one run (COMPLETED with results, or
    FAILED with an error_message -- see app/api/backtesting.py::run()).
    status/completed_at/results_json/error_message are the ONLY columns
    this ever touches -- every parameter column set at save_backtest()
    time is left untouched, keeping a completed backtest's parameters
    exactly reproducible."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                UPDATE backtests
                SET status = ?, completed_at = ?, results_json = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    completed_at.isoformat(),
                    results.model_dump_json() if results else None,
                    error_message,
                    backtest_id,
                ),
            )
    finally:
        conn.close()


def get_backtest(backtest_id: str, *, db_path: str | Path | None = None) -> Backtest | None:
    """Returns None (never raises) when no backtest has that id -- the
    same "absence is a normal, checkable outcome" convention every
    other repository in this app uses."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM backtests WHERE id = ?", (backtest_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_backtest(row)


def list_backtests(*, experiment_id: str | None = None, db_path: str | Path | None = None) -> list[Backtest]:
    """Every saved backtest, newest-created first -- optionally filtered
    to one experiment's own backtests (an experiment may have more than
    one backtest run against it, e.g. with different window sets)."""
    conn = get_connection(db_path)
    try:
        if experiment_id is not None:
            rows = conn.execute(
                "SELECT * FROM backtests WHERE experiment_id = ? ORDER BY created_at DESC", (experiment_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM backtests ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()
    return [_row_to_backtest(row) for row in rows]


def replace_signals(backtest_id: str, signals: list[BacktestSignal], *, db_path: str | Path | None = None) -> None:
    """Deletes this backtest's existing signals (none, on a first run)
    and inserts `signals`, in one transaction, so a caller never
    observes a half-replaced state. Re-running the same backtest against
    the same dataset must produce the same signals, not a doubled or
    tripled set -- identical reasoning to
    research_repository.replace_events()."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM backtest_signals WHERE backtest_id = ?", (backtest_id,))
            for signal in signals:
                conn.execute(
                    """
                    INSERT INTO backtest_signals
                        (backtest_id, experiment_id, symbol, timeframe, signal_timestamp, entry_timestamp,
                         entry_price, feature_values_json, outcomes_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal.backtest_id,
                        signal.experiment_id,
                        signal.symbol,
                        signal.timeframe,
                        signal.signal_timestamp.isoformat(),
                        signal.entry_timestamp.isoformat(),
                        signal.entry_price,
                        json.dumps(signal.feature_values),
                        _OutcomesAdapter.dump_json(signal.outcomes).decode(),
                    ),
                )
    finally:
        conn.close()


def get_signals(backtest_id: str, *, db_path: str | Path | None = None) -> list[BacktestSignal]:
    """Every stored signal for this backtest, oldest-signal-first -- the
    individual observations that make results fully inspectable, not
    just the aggregate BacktestResults."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM backtest_signals WHERE backtest_id = ? ORDER BY signal_timestamp ASC",
            (backtest_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_signal(row) for row in rows]


def _row_to_backtest(row) -> Backtest:
    return Backtest(
        id=row["id"],
        experiment_id=row["experiment_id"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        provider=row["provider"],
        windows=_WindowsAdapter.validate_json(row["windows_json"]),
        feature_contract_version=row["feature_contract_version"],
        status=BacktestStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        results=BacktestResults.model_validate_json(row["results_json"]) if row["results_json"] else None,
        error_message=row["error_message"],
    )


def _row_to_signal(row) -> BacktestSignal:
    return BacktestSignal(
        backtest_id=row["backtest_id"],
        experiment_id=row["experiment_id"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        signal_timestamp=datetime.fromisoformat(row["signal_timestamp"]),
        entry_timestamp=datetime.fromisoformat(row["entry_timestamp"]),
        entry_price=row["entry_price"],
        feature_values=_FeatureValuesAdapter.validate_json(row["feature_values_json"]),
        outcomes=_OutcomesAdapter.validate_json(row["outcomes_json"]),
    )
