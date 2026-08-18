"""The OOS-statistical-review repository -- the ONLY module that writes
SQL for `oos_statistical_reviews` (see app/storage/db.py's schema).
Same boundary rule as every other repository in this app: nothing
outside this file touches sqlite3 or a SQL string for this table.

    app/oos_statistical_review/engine.py   builds an OOSStatisticalReview in memory (pure)
    app/api/oos_statistical_review.py       HTTP glue: calls the engine, then this module
    Storage (THIS FILE)                     OOSStatisticalReview <-> database row, and back

APPEND-ONLY, deliberately: there is no update_review()/replace_review()
function anywhere in this file -- running the review again against the
SAME evidence produces a brand-new row (a fresh, random `id`) with
IDENTICAL analytical content, never overwriting or replacing a prior
review, the same append-only precedent app/storage/
oos_evaluation_repository.py and app/storage/oos_evidence_repository.py
already establish for their own tables.

`review_json` holds the ENTIRE OOSStatisticalReview as one JSON blob
(model_dump_json()/model_validate_json()) rather than one column per
field -- unlike `historical_bars`' fixed OHLCV columns, this review's
own shape is complex and nested (per-period results, two dependence-
aware methods, ...) with nothing else in this app needing to query
into an individual sub-field directly; `experiment_id`/`hypothesis_hash`/
`verdict`/`created_at` are pulled out as their own columns ONLY because
those are the four things list_reviews()/a future filtered query would
actually need to select or order by -- the same "structured data as
JSON text alongside named columns for whatever IS queried directly"
pattern `oos_evaluations.results_json` and `experiments.conditions_json`
already use.
"""

from pathlib import Path

from app.models.oos_statistical_review import OOSStatisticalReview
from app.storage.db import get_connection


def save_review(review: OOSStatisticalReview, *, db_path: str | Path | None = None) -> None:
    """Inserts one review row -- `review.id` is always a brand-new
    uuid4 (app/oos_statistical_review/engine.py::build_oos_statistical_review()
    mints a fresh one per call), so this is always a plain INSERT,
    never an upsert."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO oos_statistical_reviews (id, experiment_id, hypothesis_hash, verdict, created_at, review_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    review.id,
                    review.experiment_id,
                    review.hypothesis_hash,
                    review.verdict.value,
                    review.created_at.isoformat(),
                    review.model_dump_json(),
                ),
            )
    finally:
        conn.close()


def get_review(review_id: str, *, db_path: str | Path | None = None) -> OOSStatisticalReview | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM oos_statistical_reviews WHERE id = ?", (review_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_review(row) if row is not None else None


def list_reviews(experiment_id: str, *, db_path: str | Path | None = None) -> list[OOSStatisticalReview]:
    """Every review ever run for `experiment_id`, newest first --
    requirement 11's own "running the same review twice ... never
    replacing a prior review" made queryable: a caller can see the FULL
    history of every review, not just the latest one."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM oos_statistical_reviews WHERE experiment_id = ? ORDER BY created_at DESC", (experiment_id,)
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_review(row) for row in rows]


def _row_to_review(row) -> OOSStatisticalReview:
    return OOSStatisticalReview.model_validate_json(row["review_json"])
