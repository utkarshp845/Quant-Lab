"""API routes for Experiment Freeze & Provenance v1 (v0.1.30), layered
on top of Research v1's existing experiment routes
(app/api/research.py, untouched by this file):

    POST /research/experiments/{id}/oos-partition   associate a DRAFT experiment with an OOS partition
    POST /research/experiments/{id}/freeze           DRAFT -> FROZEN (requirement 1/2)
    GET  /research/experiments/{id}/frozen           the immutable freeze snapshot (requirement 5)
    GET  /research/experiments/{id}/provenance       the full reproducibility read view (requirement 7)
    POST /research/experiments/{id}/archive          FROZEN|OOS_EVALUATED -> ARCHIVED

Deliberately does NOT add any endpoint that could mutate a
research-defining field on an already-frozen experiment (requirement
8's own instruction) -- there has never been a generic "edit
experiment" endpoint in this codebase (see app/models/research.py::
Experiment's own docstring), and this file does not add one. The only
mutation this file exposes pre-freeze is the OOS-partition association,
which is deliberately restricted to `lifecycle_state == DRAFT` both
here and, redundantly, in app/storage/research_repository.py::
set_oos_partition()'s own WHERE clause.

No FROZEN -> OOS_EVALUATED route exists here on purpose -- the actual
OOS-evaluation operation that would legitimately trigger it is out of
this feature's scope (see app/storage/research_repository.py::
mark_oos_evaluated()'s own docstring); that transition is
infrastructure, tested directly, not yet reachable over HTTP.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.models.experiment_freeze import (
    ExperimentFreezeSnapshot,
    ExperimentProvenance,
    OOSPartitionLinkRequest,
)
from app.models.research import Experiment, ExperimentLifecycleState
from app.research.lifecycle import (
    InvalidLifecycleTransitionError,
    PartitionLinkageError,
    build_freeze_snapshot,
    compute_hypothesis_hash,
    validate_partition_linkage,
    validate_transition,
)
from app.storage import experiment_freeze_repository, oos_partition_repository, research_repository

router = APIRouter()


def _get_experiment_or_404(experiment_id: str) -> Experiment:
    experiment = research_repository.get_experiment(experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail=f"No experiment with id {experiment_id!r}")
    return experiment


def _get_partition_or_404(partition_id: str):
    partition = oos_partition_repository.get_partition(partition_id)
    if partition is None:
        raise HTTPException(status_code=404, detail=f"No OOS partition with id {partition_id!r}")
    return partition


@router.post("/research/experiments/{experiment_id}/oos-partition", response_model=Experiment)
def associate_oos_partition(experiment_id: str, request: OOSPartitionLinkRequest) -> Experiment:
    """Requirement 6: "a DRAFT experiment may be associated with a
    partition". Validated fully HERE too (not deferred entirely to
    freeze time) -- fail fast on an obviously incompatible pairing
    rather than letting a caller discover it only at POST .../freeze;
    freeze() below re-validates unconditionally regardless, since this
    route is not the only path requirement 6 asks to enforce it at."""
    experiment = _get_experiment_or_404(experiment_id)
    if experiment.lifecycle_state != ExperimentLifecycleState.DRAFT:
        raise HTTPException(
            status_code=409,
            detail=f"Experiment {experiment_id!r} is {experiment.lifecycle_state.value!r}, not draft -- "
            "its OOS partition can only be set (or changed) before freezing.",
        )

    partition = _get_partition_or_404(request.oos_partition_id)
    try:
        validate_partition_linkage(experiment, partition)
    except PartitionLinkageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    updated = research_repository.set_oos_partition(experiment_id, partition.id)
    if not updated:
        # Lost a race with something else changing lifecycle_state
        # between the check above and the write -- surfaced honestly
        # rather than silently reporting success.
        raise HTTPException(
            status_code=409, detail=f"Experiment {experiment_id!r} is no longer draft; OOS partition not set."
        )
    return _get_experiment_or_404(experiment_id)


@router.post("/research/experiments/{experiment_id}/freeze", response_model=Experiment)
def freeze_experiment_route(experiment_id: str) -> Experiment:
    """DRAFT -> FROZEN (requirement 1/2). Computes hypothesis_hash
    (requirement 4) and persists the immutable ExperimentFreezeSnapshot
    (requirement 5) in the SAME logical operation as the lifecycle-state
    write -- if either fails, both are rolled back (see below): a
    frozen experiment with no snapshot, or a snapshot for an experiment
    that never actually froze, would each independently break
    requirement 5's "answer later, without reconstructing it" promise.
    """
    experiment = _get_experiment_or_404(experiment_id)

    try:
        validate_transition(experiment.lifecycle_state, ExperimentLifecycleState.FROZEN)
    except InvalidLifecycleTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if experiment.oos_partition_id is not None:
        partition = _get_partition_or_404(experiment.oos_partition_id)
        try:
            validate_partition_linkage(experiment, partition)
        except PartitionLinkageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    frozen_at = datetime.now(timezone.utc)
    hypothesis_hash = compute_hypothesis_hash(experiment)
    snapshot = build_freeze_snapshot(experiment, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at)

    experiment_freeze_repository.save_snapshot(snapshot)
    updated = research_repository.freeze_experiment(
        experiment_id,
        hypothesis_hash=hypothesis_hash,
        frozen_at=frozen_at,
        oos_partition_id=experiment.oos_partition_id,
    )
    if not updated:
        # Lost a race (something else froze/archived it between the
        # validate_transition() check above and this write). The
        # snapshot row above is now orphaned relative to the live row's
        # actual state -- acceptable: it still accurately describes
        # what WOULD have been evaluated had this call won the race,
        # and experiment_freeze_snapshots' PRIMARY KEY on experiment_id
        # means a genuine retry-after-freeze attempt fails loudly
        # (sqlite3.IntegrityError) rather than silently overwriting it.
        raise HTTPException(status_code=409, detail=f"Experiment {experiment_id!r} is no longer draft; not frozen.")

    return _get_experiment_or_404(experiment_id)


@router.get("/research/experiments/{experiment_id}/frozen", response_model=ExperimentFreezeSnapshot)
def get_frozen_definition(experiment_id: str) -> ExperimentFreezeSnapshot:
    """Requirement 5/8: the raw, immutable snapshot -- not the live
    `experiments` row (see app/models/experiment_freeze.py's own
    docstring for why those are different objects, on purpose)."""
    _get_experiment_or_404(experiment_id)
    snapshot = experiment_freeze_repository.get_snapshot(experiment_id)
    if snapshot is None:
        raise HTTPException(status_code=409, detail=f"Experiment {experiment_id!r} has not been frozen yet.")
    return snapshot


@router.get("/research/experiments/{experiment_id}/provenance", response_model=ExperimentProvenance)
def get_provenance(experiment_id: str) -> ExperimentProvenance:
    """Requirement 7: everything needed to reconstruct what data was
    used, what hypothesis was tested, what feature contract was used,
    and which OOS partition was reserved -- in one response. Reads the
    OOS partition live (app/storage/oos_partition_repository.py) rather
    than caching a copy of it, so this always reflects that partition's
    real, current (immutable, per app/models/oos_partition.py) fields."""
    experiment = _get_experiment_or_404(experiment_id)
    snapshot = experiment_freeze_repository.get_snapshot(experiment_id)
    if snapshot is None:
        raise HTTPException(status_code=409, detail=f"Experiment {experiment_id!r} has not been frozen yet.")

    oos_partition = (
        oos_partition_repository.get_partition(snapshot.oos_partition_id) if snapshot.oos_partition_id else None
    )

    return ExperimentProvenance(
        experiment_id=snapshot.experiment_id,
        lifecycle_state=experiment.lifecycle_state,
        hypothesis_hash=snapshot.hypothesis_hash,
        name=snapshot.name,
        hypothesis=snapshot.hypothesis,
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        provider=snapshot.provider,
        start_date=snapshot.start_date,
        end_date=snapshot.end_date,
        feature_contract_version=snapshot.feature_contract_version,
        conditions=snapshot.conditions,
        outcome=snapshot.outcome,
        oos_partition=oos_partition,
        experiment_created_at=snapshot.experiment_created_at,
        frozen_at=snapshot.frozen_at,
        archived_at=experiment.archived_at,
    )


@router.post("/research/experiments/{experiment_id}/archive", response_model=Experiment)
def archive_experiment(experiment_id: str) -> Experiment:
    """FROZEN -> ARCHIVED or OOS_EVALUATED -> ARCHIVED (requirement 1's
    two archive transitions) -- a DRAFT experiment cannot be archived
    directly (it must freeze first), matching _VALID_TRANSITIONS in
    app/research/lifecycle.py exactly."""
    experiment = _get_experiment_or_404(experiment_id)
    try:
        validate_transition(experiment.lifecycle_state, ExperimentLifecycleState.ARCHIVED)
    except InvalidLifecycleTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    archived_at = datetime.now(timezone.utc)
    updated = research_repository.mark_archived(experiment_id, archived_at=archived_at)
    if not updated:
        raise HTTPException(status_code=409, detail=f"Experiment {experiment_id!r} could not be archived.")
    return _get_experiment_or_404(experiment_id)
