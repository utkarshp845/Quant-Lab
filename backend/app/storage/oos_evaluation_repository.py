"""The OOS-evaluation repository -- the ONLY module that writes SQL for
`oos_evaluations`/`oos_evaluation_signals` (see app/storage/db.py's
schema). Same boundary rule as every other repository in this app:
nothing outside this file touches sqlite3 or a SQL string for either
table.

    app/oos_evaluation/engine.py   OOSEvaluationResult/OOSSignal (pure, no I/O)
    app/api/oos_evaluation.py       HTTP glue: calls the engine, then this module
    Storage (THIS FILE)             OOSEvaluationResult/OOSSignal <-> database rows, and back

APPEND-ONLY, deliberately: there is no update_evaluation()/
replace_signals() function anywhere in this file, unlike
research_repository.py's replace_events() or backtest_repository.py's
replace_signals() -- see oos_evaluations' own schema comment
(app/storage/db.py) for why re-running the same frozen experiment must
never touch a prior evaluation's rows.
"""

from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter

from app.models.backtesting import BacktestResults, BacktestWindowOutcome
from app.models.oos_evaluation import OOSEvaluationResult, OOSEvaluationStatus, OOSSignal
from app.storage.db import get_connection

_OutcomesAdapter = TypeAdapter(list[BacktestWindowOutcome])
_FeatureValuesAdapter = TypeAdapter(dict[str, float | bool])


def save_evaluation(result: OOSEvaluationResult, signals: list[OOSSignal], *, db_path: str | Path | None = None) -> None:
    """Inserts one evaluation row and every one of its signal rows, in
    a single transaction, so a reader never observes an evaluation row
    with a partial/missing signal set. `result.id` is the primary key
    of `oos_evaluations` -- always a brand-new id (app/oos_evaluation/
    engine.py::evaluate_oos() mints a fresh uuid4 per call), so this is
    always a plain INSERT, never an upsert."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO oos_evaluations
                    (id, experiment_id, hypothesis_hash, frozen_snapshot_id, oos_partition_id, symbol,
                     timeframe, provider, holdout_start, holdout_end, feature_contract_version,
                     outcome_horizon_minutes, outcome_window_bars, signal_count, results_json, status,
                     error_message, frozen_at, evaluated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.id,
                    result.experiment_id,
                    result.hypothesis_hash,
                    result.frozen_snapshot_id,
                    result.oos_partition_id,
                    result.symbol,
                    result.timeframe,
                    result.provider,
                    result.holdout_start.isoformat(),
                    result.holdout_end.isoformat(),
                    result.feature_contract_version,
                    result.outcome_horizon_minutes,
                    result.outcome_window_bars,
                    result.signal_count,
                    result.results.model_dump_json() if result.results else None,
                    result.status.value,
                    result.error_message,
                    result.frozen_at.isoformat(),
                    result.evaluated_at.isoformat(),
                ),
            )
            for signal in signals:
                conn.execute(
                    """
                    INSERT INTO oos_evaluation_signals
                        (evaluation_id, symbol, timeframe, signal_timestamp, entry_timestamp, entry_price,
                         feature_values_json, outcomes_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal.evaluation_id,
                        signal.symbol,
                        signal.timeframe,
                        signal.signal_timestamp.isoformat(),
                        signal.entry_timestamp.isoformat(),
                        signal.entry_price,
                        _FeatureValuesAdapter.dump_json(signal.feature_values).decode(),
                        _OutcomesAdapter.dump_json(signal.outcomes).decode(),
                    ),
                )
    finally:
        conn.close()


def get_evaluation(evaluation_id: str, *, db_path: str | Path | None = None) -> OOSEvaluationResult | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM oos_evaluations WHERE id = ?", (evaluation_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_evaluation(row) if row is not None else None


def list_evaluations(experiment_id: str, *, db_path: str | Path | None = None) -> list[OOSEvaluationResult]:
    """Every evaluation ever run for `experiment_id`, newest first --
    requirement 6's "do not silently replace historical evaluations"
    made queryable: a caller can see the FULL history of every run,
    not just the latest one."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM oos_evaluations WHERE experiment_id = ? ORDER BY evaluated_at DESC", (experiment_id,)
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_evaluation(row) for row in rows]


def get_signals(evaluation_id: str, *, db_path: str | Path | None = None) -> list[OOSSignal]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM oos_evaluation_signals WHERE evaluation_id = ? ORDER BY signal_timestamp ASC",
            (evaluation_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_signal(row) for row in rows]


def _row_to_evaluation(row) -> OOSEvaluationResult:
    return OOSEvaluationResult(
        id=row["id"],
        experiment_id=row["experiment_id"],
        hypothesis_hash=row["hypothesis_hash"],
        frozen_snapshot_id=row["frozen_snapshot_id"],
        oos_partition_id=row["oos_partition_id"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        provider=row["provider"],
        holdout_start=datetime.fromisoformat(row["holdout_start"]),
        holdout_end=datetime.fromisoformat(row["holdout_end"]),
        feature_contract_version=row["feature_contract_version"],
        outcome_horizon_minutes=row["outcome_horizon_minutes"],
        outcome_window_bars=row["outcome_window_bars"],
        signal_count=row["signal_count"],
        results=BacktestResults.model_validate_json(row["results_json"]) if row["results_json"] else None,
        status=OOSEvaluationStatus(row["status"]),
        error_message=row["error_message"],
        frozen_at=datetime.fromisoformat(row["frozen_at"]),
        evaluated_at=datetime.fromisoformat(row["evaluated_at"]),
    )


def _row_to_signal(row) -> OOSSignal:
    return OOSSignal(
        evaluation_id=row["evaluation_id"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        signal_timestamp=datetime.fromisoformat(row["signal_timestamp"]),
        entry_timestamp=datetime.fromisoformat(row["entry_timestamp"]),
        entry_price=row["entry_price"],
        feature_values=_FeatureValuesAdapter.validate_json(row["feature_values_json"]),
        outcomes=_OutcomesAdapter.validate_json(row["outcomes_json"]),
    )
