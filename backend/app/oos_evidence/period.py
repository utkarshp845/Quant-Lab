"""Pure logic for OOS Evidence Accumulation V1's period-registration
rules (app/oos_evidence/, app/storage/oos_evidence_repository.py,
app/api/oos_evidence.py): given an already-frozen experiment's
immutable ExperimentFreezeSnapshot and an already-created, independent
OOSPartition (app/models/oos_partition.py, created via the existing,
UNMODIFIED `POST /oos/partitions` -- this module never creates a
partition itself), decides whether that partition may be registered as
an ADDITIONAL OOS evaluation period for that experiment.

No I/O here -- matches app/oos/partition.py's and app/research/
lifecycle.py's own "engine" discipline (pure functions over
already-fetched objects; app/api/oos_evidence.py is the only caller
from an HTTP handler).

Symbol/timeframe/provider compatibility AND "the OOS period occurs
strictly after the experiment's own development range, and does not
overlap it" are BOTH already enforced by app.research.lifecycle::
validate_snapshot_partition_linkage() -- the SAME structural check OOS
Evaluation v1 itself applies to a partition before ever evaluating it
(app/oos_evaluation/engine.py::evaluate_oos_for_partition() calls it
again, unconditionally, regardless of what a caller validated at
registration time). That function requires the new partition's own
`development_end` to be >= the experiment's own `end_date` (full
containment of the experiment's development range inside the
partition's development window); since a partition's own
`development_end < holdout_start` is already a structural invariant
(app/models/oos_partition.py's own pydantic validator), containment
ALONE already implies `holdout_start > end_date` -- "strictly after
the development period" falls out for free, not a separate check this
module needs to re-derive. app/api/oos_evidence.py calls
validate_snapshot_partition_linkage() directly (fail-fast, at
registration time, matching app/api/experiment_freeze.py::
associate_oos_partition()'s own "validate fully here too, do not defer
entirely to a later step" precedent) BEFORE calling validate_new_period()
below -- this module's OWN, ADDITIONAL job is everything THAT check
does not cover: cross-period non-overlap, no development/holdout
contamination between two periods reserved for the SAME experiment,
and rejecting a partition already registered for this experiment.
"""

from datetime import datetime

from app.models.experiment_freeze import ExperimentFreezeSnapshot
from app.models.oos_partition import OOSPartition


class OOSPeriodLinkageError(ValueError):
    """Raised by validate_new_period() below -- a ValueError subclass,
    matching this app's existing convention for a rejected-input/
    rejected-state condition an API route is expected to catch and
    translate into a 4xx (see app/oos/partition.py::PartitionLeakageError,
    app/research/lifecycle.py::PartitionLinkageError, the identical
    precedent)."""


def _overlaps_or_touches(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    """Inclusive overlap-or-touch: matches app/models/oos_partition.py::
    _validate_range_ordering()'s own "touching counts as overlapping"
    rule -- a bar timestamped exactly at a shared boundary would be
    ambiguous about which range it belongs to, so two ranges that only
    touch at one instant are rejected exactly like two that genuinely
    overlap."""
    return start_a <= end_b and start_b <= end_a


def validate_new_period(
    *,
    snapshot: ExperimentFreezeSnapshot,
    new_partition: OOSPartition,
    already_registered_partitions: list[OOSPartition],
) -> None:
    """Raises OOSPeriodLinkageError unless `new_partition` may be
    registered as an additional OOS evaluation period for
    `snapshot`'s experiment. `already_registered_partitions` is every
    OOSPartition already associated with this experiment as an
    evidence-accumulation period -- INCLUDING the experiment's
    originally frozen-time-linked partition (`snapshot.oos_partition_id`),
    if any -- resolved by the caller (app/api/oos_evidence.py) before
    this is called, since this module is a pure leaf and does no I/O of
    its own.

    Checked, in order:

      1. symbol/timeframe/provider compatibility -- redundant with
         validate_snapshot_partition_linkage() (see the module
         docstring: the API route already calls that first), kept here
         too as defense in depth, the same double-check idiom
         app/storage/research_repository.py::set_oos_partition()'s own
         WHERE clause already applies alongside its route's check.
      2. `new_partition` is not already registered for this experiment
         (requirement: "previously evaluated OOS periods cannot be
         evaluated again" starts here -- a partition cannot even be
         registered twice, let alone evaluated twice).
      3. `new_partition`'s HOLDOUT window does not overlap or touch any
         already-registered partition's own HOLDOUT window
         (requirement: "OOS periods must not overlap").
      4. `new_partition`'s HOLDOUT window does not overlap or touch any
         already-registered partition's own DEVELOPMENT window, and
         `new_partition`'s own DEVELOPMENT window does not overlap or
         touch any already-registered partition's HOLDOUT window --
         BOTH directions of "no cross-partition contamination": a
         later period's warm-up must never silently read through an
         earlier period's reserved holdout data, and an earlier
         period's development-side warm-up must never reach into a
         later period's holdout either. (Two DEVELOPMENT windows
         overlapping each other is explicitly NOT flagged -- reusing
         the same underlying development-side context across periods
         for warm-up is normal and expected, not a leak.)

    Nothing here touches development-vs-holdout data CONTENT (no bar is
    read) -- this is boundary validation only, the same "engine"
    discipline app/oos/partition.py's own classify_range()/
    require_development_range() already establish.
    """
    if (
        new_partition.symbol != snapshot.symbol
        or new_partition.timeframe != snapshot.timeframe
        or new_partition.provider != snapshot.provider
    ):
        raise OOSPeriodLinkageError(
            f"OOS partition {new_partition.id!r} ({new_partition.symbol}/{new_partition.timeframe}/"
            f"{new_partition.provider}) is not compatible with frozen experiment {snapshot.experiment_id!r} "
            f"({snapshot.symbol}/{snapshot.timeframe}/{snapshot.provider}) -- symbol, timeframe, and provider "
            "must all match."
        )

    for other in already_registered_partitions:
        if other.id == new_partition.id:
            raise OOSPeriodLinkageError(
                f"OOS partition {new_partition.id!r} is already registered as an OOS period for experiment "
                f"{snapshot.experiment_id!r} -- a previously registered OOS period cannot be registered (or "
                "evaluated) again."
            )

    for other in already_registered_partitions:
        if _overlaps_or_touches(
            new_partition.holdout_start, new_partition.holdout_end, other.holdout_start, other.holdout_end
        ):
            raise OOSPeriodLinkageError(
                f"OOS partition {new_partition.id!r}'s OOS window "
                f"[{new_partition.holdout_start.isoformat()} .. {new_partition.holdout_end.isoformat()}] overlaps "
                f"(or touches) OOS partition {other.id!r}'s own OOS window "
                f"[{other.holdout_start.isoformat()} .. {other.holdout_end.isoformat()}], already registered for "
                f"experiment {snapshot.experiment_id!r} -- OOS periods must not overlap."
            )
        if _overlaps_or_touches(
            new_partition.holdout_start, new_partition.holdout_end, other.development_start, other.development_end
        ):
            raise OOSPeriodLinkageError(
                f"OOS partition {new_partition.id!r}'s OOS window "
                f"[{new_partition.holdout_start.isoformat()} .. {new_partition.holdout_end.isoformat()}] overlaps "
                f"(or touches) OOS partition {other.id!r}'s DEVELOPMENT window "
                f"[{other.development_start.isoformat()} .. {other.development_end.isoformat()}], already "
                f"registered for experiment {snapshot.experiment_id!r} -- cross-partition contamination is "
                "never allowed."
            )
        if _overlaps_or_touches(
            new_partition.development_start, new_partition.development_end, other.holdout_start, other.holdout_end
        ):
            raise OOSPeriodLinkageError(
                f"OOS partition {new_partition.id!r}'s DEVELOPMENT window "
                f"[{new_partition.development_start.isoformat()} .. {new_partition.development_end.isoformat()}] "
                f"overlaps (or touches) OOS partition {other.id!r}'s OOS window "
                f"[{other.holdout_start.isoformat()} .. {other.holdout_end.isoformat()}], already registered for "
                f"experiment {snapshot.experiment_id!r} -- cross-partition contamination is never allowed."
            )
