"""OOS Evidence Accumulation V1's evaluation orchestration
(app/oos_evidence/): given a frozen experiment and an OOS period
already registered for it (app/oos_evidence/period.py,
app/storage/oos_evidence_repository.py), runs the SAME OOS Evaluation
v1 pipeline (app/oos_evaluation/engine.py::evaluate_oos_for_partition(),
reused UNMODIFIED) against that period's own OOS partition, with one
extra guarantee OOS Evaluation v1 itself does not need for its own
single, frozen-time-linked partition: a period that already has a
COMPLETED evaluation may never be evaluated again (requirement:
"previously evaluated OOS periods cannot be evaluated again"). A
FAILED evaluation does NOT count as "evaluated" -- a transient/
pipeline-stage failure can always be retried, any number of times,
until it either succeeds exactly once or a caller gives up; only a
COMPLETED result is terminal for a given period.

Re-exports every exception app.oos_evaluation.engine.
evaluate_oos_for_partition() itself can raise, so app/api/oos_evidence.py
has one single module to import every exception type from.
"""

from pathlib import Path

from app.models.oos_evaluation import OOSEvaluationResult, OOSEvaluationStatus, OOSSignal
from app.oos_evaluation.engine import (
    ExperimentNotFoundForEvaluationError,  # noqa: F401 -- re-exported, see module docstring
    IncompleteProvenanceError,  # noqa: F401
    InvalidLifecycleForEvaluationError,  # noqa: F401
    OOSEvaluationError,
    PartitionNotFoundForEvaluationError,  # noqa: F401
    evaluate_oos_for_partition,
)
from app.storage import oos_evaluation_repository, oos_evidence_repository, research_repository


class PeriodNotRegisteredError(OOSEvaluationError):
    """Raised when `oos_partition_id` has not been registered as an OOS
    period for this experiment (app/oos_evidence/period.py) --
    evaluating an unregistered partition would bypass every one of
    that module's leakage/overlap checks entirely."""


class PeriodAlreadyEvaluatedError(OOSEvaluationError):
    """Raised when this OOS period already has a COMPLETED evaluation
    for this experiment -- see the module docstring. A FAILED
    evaluation does not raise this; only a COMPLETED one is terminal
    per period."""


def evaluate_oos_period(
    experiment_id: str,
    oos_partition_id: str,
    *,
    market_context_symbols: frozenset[str] | set[str] = frozenset(),
    db_path: str | Path | None = None,
) -> tuple[OOSEvaluationResult, list[OOSSignal]]:
    """The one entry point (app/api/oos_evidence.py's POST
    .../oos-periods/{oos_partition_id}/evaluate route). Raises
    ExperimentNotFoundForEvaluationError, PeriodNotRegisteredError, or
    PeriodAlreadyEvaluatedError (this module's own preconditions,
    checked first and cheaply, entirely before any holdout data is
    touched) -- or anything evaluate_oos_for_partition() itself raises
    (invalid lifecycle, incomplete provenance, partition not found,
    partition linkage). All of the above persist NOTHING. Only a
    genuine pipeline-stage failure past every precondition is persisted
    as a FAILED OOSEvaluationResult -- identical in spirit to
    app/oos_evaluation/engine.py::evaluate_oos()'s own precondition/
    pipeline split (see that function's docstring)."""
    experiment = research_repository.get_experiment(experiment_id, db_path=db_path)
    if experiment is None:
        raise ExperimentNotFoundForEvaluationError(f"No experiment with id {experiment_id!r}")

    period = oos_evidence_repository.get_period(experiment_id, oos_partition_id, db_path=db_path)
    if period is None:
        raise PeriodNotRegisteredError(
            f"OOS partition {oos_partition_id!r} is not a registered OOS period for experiment "
            f"{experiment_id!r} -- register it first (POST /research/experiments/{experiment_id}/oos-periods)."
        )

    already_completed = any(
        evaluation.oos_partition_id == oos_partition_id and evaluation.status == OOSEvaluationStatus.COMPLETED
        for evaluation in oos_evaluation_repository.list_evaluations(experiment_id, db_path=db_path)
    )
    if already_completed:
        raise PeriodAlreadyEvaluatedError(
            f"OOS period {oos_partition_id!r} already has a COMPLETED evaluation for experiment "
            f"{experiment_id!r} -- a previously evaluated OOS period cannot be evaluated again."
        )

    return evaluate_oos_for_partition(
        experiment_id, oos_partition_id, market_context_symbols=market_context_symbols, db_path=db_path
    )
