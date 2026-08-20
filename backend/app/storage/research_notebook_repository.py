"""The Research Notebook v1 repository -- the ONLY module that writes
SQL for `research_observations`/`research_decisions`/
`research_conclusions` (app/storage/db.py's schema). Same boundary rule
as app/storage/research_repository.py: nothing outside this file
touches sqlite3 or a SQL string for any of the three tables.

Every function takes/returns a model from app/models/research_notebook.py
-- never a raw sqlite3.Row past this file's own boundary. All three
entities are write-once (Observation) or append-only (Decision,
Conclusion) -- there is no update/delete function anywhere below,
matching those models' own docstrings.
"""

import json
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter

from app.models.research_notebook import Conclusion, ConclusionState, Observation, ResearchDecision
from app.storage.db import get_connection

_DatetimeListAdapter = TypeAdapter(list[datetime])
_StringListAdapter = TypeAdapter(list[str])


def save_observation(observation: Observation, *, db_path: str | Path | None = None) -> None:
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO research_observations
                    (id, symbol, description, observed_start, observed_end,
                     referenced_bar_timestamps_json, referenced_feature_ids_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.id,
                    observation.symbol,
                    observation.description,
                    observation.observed_start.isoformat(),
                    observation.observed_end.isoformat(),
                    _DatetimeListAdapter.dump_json(observation.referenced_bar_timestamps).decode(),
                    json.dumps(observation.referenced_feature_ids),
                    observation.created_at.isoformat(),
                ),
            )
    finally:
        conn.close()


def get_observation(observation_id: str, *, db_path: str | Path | None = None) -> Observation | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM research_observations WHERE id = ?", (observation_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_observation(row) if row is not None else None


def list_observations(*, symbol: str | None = None, db_path: str | Path | None = None) -> list[Observation]:
    """Every observation, newest first, optionally filtered to one
    symbol -- the read side of "select an existing Observation" (spec
    section 6) for a caller building a hypothesis on top of one."""
    conn = get_connection(db_path)
    try:
        if symbol is not None:
            rows = conn.execute(
                "SELECT * FROM research_observations WHERE symbol = ? ORDER BY created_at DESC", (symbol.upper(),)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM research_observations ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()
    return [_row_to_observation(row) for row in rows]


def _row_to_observation(row) -> Observation:
    return Observation(
        id=row["id"],
        symbol=row["symbol"],
        description=row["description"],
        observed_start=datetime.fromisoformat(row["observed_start"]),
        observed_end=datetime.fromisoformat(row["observed_end"]),
        referenced_bar_timestamps=_DatetimeListAdapter.validate_json(row["referenced_bar_timestamps_json"]),
        referenced_feature_ids=json.loads(row["referenced_feature_ids_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def save_decision(decision: ResearchDecision, *, db_path: str | Path | None = None) -> None:
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO research_decisions
                    (id, design_group_id, decision, reason, selection_criteria_json,
                     information_available_json, outcome_data_available, resulting_experiment_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.id,
                    decision.design_group_id,
                    decision.decision,
                    decision.reason,
                    json.dumps(decision.selection_criteria),
                    json.dumps(decision.information_available),
                    1 if decision.outcome_data_available else 0,
                    decision.resulting_experiment_id,
                    decision.created_at.isoformat(),
                ),
            )
    finally:
        conn.close()


def list_decisions(design_group_id: str, *, db_path: str | Path | None = None) -> list[ResearchDecision]:
    """The full, ordered (oldest first, a readable history) decision
    log for one design group -- spec section 9: "The user should be
    able to inspect the full decision history of an experiment."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM research_decisions WHERE design_group_id = ? ORDER BY created_at ASC", (design_group_id,)
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_decision(row) for row in rows]


def _row_to_decision(row) -> ResearchDecision:
    return ResearchDecision(
        id=row["id"],
        design_group_id=row["design_group_id"],
        decision=row["decision"],
        reason=row["reason"],
        selection_criteria=json.loads(row["selection_criteria_json"]),
        information_available=json.loads(row["information_available_json"]),
        outcome_data_available=bool(row["outcome_data_available"]),
        resulting_experiment_id=row["resulting_experiment_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def save_conclusion(conclusion: Conclusion, *, db_path: str | Path | None = None) -> None:
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO research_conclusions
                    (id, experiment_id, state, statement, references_hypothesis, references_sample,
                     references_baseline, references_outcomes, references_statistical_validation,
                     limitations, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conclusion.id,
                    conclusion.experiment_id,
                    conclusion.state.value,
                    conclusion.statement,
                    conclusion.references_hypothesis,
                    conclusion.references_sample,
                    conclusion.references_baseline,
                    conclusion.references_outcomes,
                    conclusion.references_statistical_validation,
                    conclusion.limitations,
                    conclusion.created_at.isoformat(),
                ),
            )
    finally:
        conn.close()


def list_conclusions(experiment_id: str, *, db_path: str | Path | None = None) -> list[Conclusion]:
    """Every conclusion ever recorded for `experiment_id`, NEWEST
    first -- the first element is "current" by convention (see
    Conclusion's own docstring)."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM research_conclusions WHERE experiment_id = ? ORDER BY created_at DESC", (experiment_id,)
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_conclusion(row) for row in rows]


def _row_to_conclusion(row) -> Conclusion:
    return Conclusion(
        id=row["id"],
        experiment_id=row["experiment_id"],
        state=ConclusionState(row["state"]),
        statement=row["statement"],
        references_hypothesis=row["references_hypothesis"],
        references_sample=row["references_sample"],
        references_baseline=row["references_baseline"],
        references_outcomes=row["references_outcomes"],
        references_statistical_validation=row["references_statistical_validation"],
        limitations=row["limitations"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
