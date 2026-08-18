"""API routes for OOS Statistical Review V1 (app/oos_statistical_review/):

    POST /research/experiments/{id}/oos-statistical-review     build (and persist) a new review from
                                                                 the experiment's own COMPLETED OOS evidence
    GET  /research/experiments/{id}/oos-statistical-reviews    every review ever run for this experiment
    GET  /research/oos-statistical-reviews/{review_id}          one review

Path convention matches every other route already scoped under an
experiment in this codebase (app/api/research.py, app/api/
experiment_freeze.py, app/api/oos_evaluation.py, app/api/
oos_evidence.py all use `/research/experiments/{id}/...`) and every
already-existing route that looks a review/evaluation up by its OWN id
alone (`/research/oos-evaluations/{evaluation_id}`, mirrored here as
`/research/oos-statistical-reviews/{review_id}`).

The POST route takes NO body -- the identical "nothing here is
caller-configurable" convention app/api/oos_evaluation.py's own POST
.../oos-evaluate already established (see that file's own docstring):
every review-defining fact (which evaluations participate, the
hypothesis, the OOS periods, the resampling seed/n_resamples/ci_level/
block_length_multiplier/power) comes from the frozen snapshot, the
already-persisted OOS evidence, and this feature's own fixed, immutable
defaults -- never from a request body. This closes the "don't choose
whichever method (or seed) gives the more favorable result" loophole
at the API boundary, not just in the engine.

Never writes to `experiments`, `experiment_freeze_snapshots`,
`oos_partitions`, `oos_evaluations`, `oos_evaluation_signals`,
`experiment_oos_periods`, `historical_bars`, or `historical_features`
-- this feature's only write, anywhere, is a brand-new
`oos_statistical_reviews` row (app.storage.oos_statistical_review_repository.
save_review(), append-only). No lifecycle transition is ever attempted
here either -- an OOS Statistical Review is pure analysis on top of an
already-`FROZEN`/`OOS_EVALUATED`/`ARCHIVED` experiment's own evidence,
never a reason to move its lifecycle_state anywhere.
"""

from fastapi import APIRouter, HTTPException

from app.models.oos_statistical_review import OOSStatisticalReview
from app.oos_statistical_review.baseline import BaselineConstructionError
from app.oos_statistical_review.engine import (
    ExperimentNeverFrozenError,
    ExperimentNotFoundForReviewError,
    ProvenanceMismatchError,
    build_oos_statistical_review,
)
from app.storage import oos_statistical_review_repository, research_repository

router = APIRouter()


@router.post("/research/experiments/{experiment_id}/oos-statistical-review", response_model=OOSStatisticalReview)
def run_oos_statistical_review(experiment_id: str) -> OOSStatisticalReview:
    """Builds and persists one review. Precondition failures
    (experiment missing, never frozen, or a provenance mismatch across
    COMPLETED evaluations) are mapped to a specific status code and
    persist NOTHING. A `BaselineConstructionError` (the OOS-scoped
    baseline could not be safely built for some already-COMPLETED
    evaluation -- e.g. its partition no longer exists) also persists
    nothing, per this feature's own "fail with a clear error rather
    than silently substituting" instruction. Too little evidence to run
    a formal test is NOT an error -- it is a legitimate, persisted
    `INSUFFICIENT_DATA` verdict (see app/oos_statistical_review/
    engine.py's own docstring)."""
    try:
        review = build_oos_statistical_review(experiment_id)
    except ExperimentNotFoundForReviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExperimentNeverFrozenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ProvenanceMismatchError, BaselineConstructionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    oos_statistical_review_repository.save_review(review)
    return review


@router.get("/research/experiments/{experiment_id}/oos-statistical-reviews", response_model=list[OOSStatisticalReview])
def list_oos_statistical_reviews(experiment_id: str) -> list[OOSStatisticalReview]:
    if research_repository.get_experiment(experiment_id) is None:
        raise HTTPException(status_code=404, detail=f"No experiment with id {experiment_id!r}")
    return oos_statistical_review_repository.list_reviews(experiment_id)


@router.get("/research/oos-statistical-reviews/{review_id}", response_model=OOSStatisticalReview)
def get_oos_statistical_review(review_id: str) -> OOSStatisticalReview:
    review = oos_statistical_review_repository.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail=f"No OOS statistical review with id {review_id!r}")
    return review
