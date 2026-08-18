"""Experiment Freeze & Provenance v1 (v0.1.30): pure logic for the
freeze lifecycle -- state-transition validation, the deterministic
hypothesis hash, OOS-partition linkage compatibility, and building the
immutable freeze snapshot. No I/O here -- matches app/research/
conditions.py's and app/research/metrics.py's own "engine" discipline
(pure functions over already-fetched objects; app/api/
experiment_freeze.py is the only place this module's functions are
actually called from an HTTP handler, and app/storage/
research_repository.py / experiment_freeze_repository.py are the only
places anything here gets persisted).

Reuses app.oos.partition.classify_range() UNMODIFIED for the OOS
partition-linkage check (requirement 6) -- this file is the
"integration" the OOS framework's own docstring anticipated
(app/oos/partition.py::require_development_range()'s own LIMITATION
note), not a reason to touch app/oos/ itself.

v0.1.31 (OOS Evaluation v1): added validate_snapshot_partition_linkage()
-- the same linkage check as validate_partition_linkage(), factored
through a shared `_validate_linkage()` helper, but against an
ExperimentFreezeSnapshot's immutable fields instead of the live
Experiment row. See app/oos_evaluation/engine.py for the caller.
"""

import hashlib
import json
from datetime import datetime, timezone

from app.models.experiment_freeze import ExperimentFreezeSnapshot
from app.models.oos_partition import OOSPartition
from app.models.research import Experiment, ExperimentLifecycleState
from app.oos.partition import DatasetSegment, classify_range


class InvalidLifecycleTransitionError(ValueError):
    """Raised by validate_transition() below for any transition not in
    _VALID_TRANSITIONS -- a ValueError subclass, matching this app's
    existing convention for a rejected-input/rejected-state condition
    an API route is expected to catch and translate into a 4xx (see
    app/oos/partition.py::PartitionLeakageError for the identical
    precedent)."""


class PartitionLinkageError(ValueError):
    """Raised by validate_partition_linkage() below when an experiment
    is not a valid match for a given OOS partition -- symbol/timeframe/
    provider mismatch, or a development range that is not entirely
    contained within the partition's OWN development window (which,
    by construction, also catches any range that touches the holdout
    window at all -- see classify_range()'s own docstring)."""


# The entire state machine (requirement 1), in one place: current state
# -> the set of states a single POST .../freeze or .../archive call may
# move it to. DRAFT's absence as a VALUE anywhere below (only as a KEY)
# means nothing ever transitions back into DRAFT -- freezing is one-way,
# by design (spec: "a frozen experiment represents a hypothesis whose
# definition must not change after the freeze point" -- a reversible
# freeze would defeat that). ARCHIVED has no outgoing transitions at
# all (empty set) -- it is a true terminal state.
_VALID_TRANSITIONS: dict[ExperimentLifecycleState, frozenset[ExperimentLifecycleState]] = {
    ExperimentLifecycleState.DRAFT: frozenset({ExperimentLifecycleState.FROZEN}),
    ExperimentLifecycleState.FROZEN: frozenset(
        {ExperimentLifecycleState.OOS_EVALUATED, ExperimentLifecycleState.ARCHIVED}
    ),
    ExperimentLifecycleState.OOS_EVALUATED: frozenset({ExperimentLifecycleState.ARCHIVED}),
    ExperimentLifecycleState.ARCHIVED: frozenset(),
}


def validate_transition(current: ExperimentLifecycleState, target: ExperimentLifecycleState) -> None:
    """Raises InvalidLifecycleTransitionError unless `current -> target`
    is one of the four transitions requirement 1 lists. The ONE place
    this table is enforced -- app/api/experiment_freeze.py's freeze/
    archive routes, and app/storage/research_repository.py::
    mark_oos_evaluated() (infrastructure, unused by any route yet -- see
    that function's own docstring), all call this before writing
    anything."""
    if target not in _VALID_TRANSITIONS.get(current, frozenset()):
        raise InvalidLifecycleTransitionError(
            f"Cannot transition an experiment from {current.value!r} to {target.value!r}. "
            f"Valid transitions: DRAFT->FROZEN, FROZEN->OOS_EVALUATED, FROZEN->ARCHIVED, "
            f"OOS_EVALUATED->ARCHIVED."
        )


def canonicalize_hypothesis(experiment: Experiment) -> dict:
    """The canonical, JSON-serializable representation of exactly the
    fields that define `experiment`'s RESEARCH MEANING (requirement 3's
    list: condition, threshold, feature configuration/contract version,
    timeframe, symbol, provider, research date range, outcome horizons,
    success criteria -- "entry definition" is Research v1's one fixed,
    code-level convention (a signal evaluates at the signal bar's own
    close -- see app/models/research.py::ExperimentEvent's docstring),
    not a per-experiment field, so there is nothing to canonicalize for
    it; see compute_hypothesis_hash()'s own docstring).

    Deliberately EXCLUDED (requirement 4): `id` (database-generated),
    `created_at` (a timestamp), `name`/`hypothesis` (free-text
    metadata -- see ExperimentFreezeSnapshot's own docstring for why),
    and `oos_partition_id` (which partition is RESERVED for evaluation
    is not part of what the hypothesis IS -- two experiments with an
    identical hypothesis but different reserved partitions are still
    testing the identical hypothesis).

    Ordering-independence (requirement 4) for `conditions`: sorted by a
    stable, fully-typed key before serializing, so [A, B] and [B, A]
    (the same AND-combined set, written in a different order) hash
    identically -- order was never semantically meaningful for an
    AND-combination in the first place. Stability across "equivalent
    representations" (requirement 4) comes from json.dumps(sort_keys=True):
    dict key order and value_max=None's presence/absence never
    affect the output, since every FeatureCondition is dumped through
    the identical, complete field set every time.
    """
    conditions = sorted(
        (
            {
                "feature_id": condition.feature_id,
                "operator": condition.operator.value,
                "value": condition.value,
                "value_max": condition.value_max,
            }
            for condition in experiment.conditions
        ),
        key=lambda c: (c["feature_id"], c["operator"], str(c["value"]), str(c["value_max"])),
    )
    return {
        "symbol": experiment.symbol,
        "timeframe": experiment.timeframe,
        "provider": experiment.provider,
        "start_date": experiment.start_date.isoformat(),
        "end_date": experiment.end_date.isoformat(),
        "feature_contract_version": experiment.feature_contract_version,
        "conditions": conditions,
        "outcome": {
            "metric": experiment.outcome.metric,
            "horizon_minutes": experiment.outcome.horizon_minutes,
            "operator": experiment.outcome.operator.value,
            "threshold": experiment.outcome.threshold,
        },
    }


def compute_hypothesis_hash(experiment: Experiment) -> str:
    """SHA-256 of canonicalize_hypothesis()'s output, serialized via
    json.dumps(sort_keys=True, separators=(",", ":")) -- a fixed,
    whitespace-free encoding so two equal dicts always produce the
    identical byte string regardless of Python dict insertion order
    (canonicalize_hypothesis() already builds a fresh, explicitly
    ordered dict literal every call, but sort_keys=True is the actual
    guarantee, not incidental). Changes if, and only if, one of
    canonicalize_hypothesis()'s inputs changes -- see that function's
    own docstring for exactly which fields those are.
    """
    encoded = json.dumps(canonicalize_hypothesis(experiment), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _date_range_as_datetime(start_date, end_date) -> tuple[datetime, datetime]:
    """A calendar `date` range (Research v1's Experiment.start_date/
    end_date convention, and ExperimentFreezeSnapshot's identical
    fields -- app/models/experiment_freeze.py) treated as spanning the
    ENTIRE calendar day at both ends: `start_date` at 00:00:00.000000
    UTC through `end_date` at 23:59:59.999999 UTC -- the same
    whole-day-inclusive convention app/storage/
    historical_bar_repository.py::get_bars() already applies to a
    `date`-bounded query. OOSPartition's own boundaries are full
    `datetime` timestamps (see app/models/oos_partition.py's own
    docstring for why); this is what makes the two comparable at all.
    """
    start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    end = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    return start, end


def _validate_linkage(
    *, entity_label: str, symbol: str, timeframe: str, provider: str, start_date, end_date, partition: OOSPartition
) -> None:
    """The actual requirement-6 check, factored out of `experiment`-vs-
    `partition` typing entirely: both validate_partition_linkage()
    (live Experiment, used at association/freeze time) and
    validate_snapshot_partition_linkage() (immutable
    ExperimentFreezeSnapshot, used at OOS-evaluation time -- see that
    function's own docstring for why evaluation must NOT read the live
    row) delegate here, so the containment/compatibility logic itself
    exists exactly once.

      - "the partition exists" -- NOT this function's job; every caller
        already has a real OOSPartition object only if app/storage/
        oos_partition_repository.py found one (see each route's own
        404).
      - "symbol/provider/timeframe are compatible" -- checked directly
        below.
      - "experiment development range is contained within the
        partition's development range" AND "the experiment does not
        use holdout dates" -- BOTH collapse into the one
        classify_range() call: it returns DatasetSegment.DEVELOPMENT
        only when the ENTIRE range falls inside the partition's
        development window; any range that touches the holdout window
        at all (or falls outside the partition entirely) returns
        something else -- see app/oos/partition.py::classify_range()'s
        own docstring. app/oos/partition.py itself is NOT modified by
        this integration.
    """
    if symbol != partition.symbol or timeframe != partition.timeframe or provider != partition.provider:
        raise PartitionLinkageError(
            f"{entity_label} ({symbol}/{timeframe}/{provider}) is not compatible with partition "
            f"{partition.id!r} ({partition.symbol}/{partition.timeframe}/{partition.provider}) -- symbol, "
            "timeframe, and provider must all match."
        )

    start, end = _date_range_as_datetime(start_date, end_date)
    if classify_range(partition, start, end) != DatasetSegment.DEVELOPMENT:
        raise PartitionLinkageError(
            f"{entity_label}'s date range [{start_date.isoformat()} .. {end_date.isoformat()}] is not entirely "
            f"within partition {partition.id!r}'s development window "
            f"[{partition.development_start.isoformat()} .. {partition.development_end.isoformat()}] -- either "
            f"it falls outside the partition altogether, or it touches the reserved holdout window "
            f"[{partition.holdout_start.isoformat()} .. {partition.holdout_end.isoformat()}], which a "
            "development-side experiment must never do."
        )


def validate_partition_linkage(experiment: Experiment, partition: OOSPartition) -> None:
    """Raises PartitionLinkageError unless `partition` is a valid
    reservation for `experiment`'s CURRENT (live-row) symbol/timeframe/
    provider/date-range -- used at OOS-partition-association time and
    at freeze time (app/api/experiment_freeze.py), when the live row
    and the eventual frozen definition are still guaranteed identical
    (freezing hasn't happened yet). See validate_snapshot_partition_linkage()
    below for the equivalent check against an already-frozen
    experiment's IMMUTABLE snapshot instead -- the one OOS evaluation
    (app/oos_evaluation/) itself must use."""
    _validate_linkage(
        entity_label=f"Experiment {experiment.id!r}",
        symbol=experiment.symbol,
        timeframe=experiment.timeframe,
        provider=experiment.provider,
        start_date=experiment.start_date,
        end_date=experiment.end_date,
        partition=partition,
    )


def validate_snapshot_partition_linkage(snapshot: ExperimentFreezeSnapshot, partition: OOSPartition) -> None:
    """The same requirement-6 check as validate_partition_linkage()
    above, but against an ExperimentFreezeSnapshot's OWN, immutable
    symbol/timeframe/provider/date-range -- never the live `experiments`
    row. OOS Evaluation v1 (app/oos_evaluation/engine.py) calls this,
    not validate_partition_linkage(), specifically because requirement
    2 of that feature forbids reading mutable research-defining fields
    from the current Experiment row during evaluation; the snapshot is
    the only research-defining source of truth once an experiment has
    frozen. In practice the two checks can never disagree today (no
    field either function reads can change after freezing -- see
    Experiment's own docstring), but evaluation uses the snapshot on
    principle, not as an optimization."""
    _validate_linkage(
        entity_label=f"Frozen experiment {snapshot.experiment_id!r}",
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        provider=snapshot.provider,
        start_date=snapshot.start_date,
        end_date=snapshot.end_date,
        partition=partition,
    )


def build_freeze_snapshot(experiment: Experiment, *, hypothesis_hash: str, frozen_at: datetime) -> ExperimentFreezeSnapshot:
    """Pure construction of the immutable freeze snapshot (requirement
    5) from `experiment`'s CURRENT field values -- a plain copy, not a
    reference, so nothing about the persisted snapshot row can ever be
    affected by anything that happens to the `experiments` row
    afterward (see ExperimentFreezeSnapshot's own docstring). Does not
    persist anything itself -- app/storage/
    experiment_freeze_repository.py::save_snapshot() does that; this
    function has no I/O, matching this module's own "pure logic" rule.
    """
    return ExperimentFreezeSnapshot(
        experiment_id=experiment.id,
        hypothesis_hash=hypothesis_hash,
        name=experiment.name,
        hypothesis=experiment.hypothesis,
        symbol=experiment.symbol,
        timeframe=experiment.timeframe,
        provider=experiment.provider,
        start_date=experiment.start_date,
        end_date=experiment.end_date,
        feature_contract_version=experiment.feature_contract_version,
        conditions=experiment.conditions,
        outcome=experiment.outcome,
        oos_partition_id=experiment.oos_partition_id,
        experiment_created_at=experiment.created_at,
        frozen_at=frozen_at,
    )
