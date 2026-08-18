"""Pure logic for the OOS / Holdout Partition Framework (v0.1.29):
classifying an arbitrary date range against an already-defined
OOSPartition, and the leakage-guard functions built on top of that
classification. No I/O here -- this module imports only
app.models.oos_partition and the stdlib, the same "engine" discipline
app/research/conditions.py and app/backtesting/engine.py already apply
(pure functions over already-fetched data, never a repository or an
HTTP call themselves).

This is the seam, not the wiring: `require_development_range()` below
is what a future Research/Feature-Engine/Backtesting/Statistical-
Validation integration would call before letting a range reach
holdout-adjacent data -- see that function's own LIMITATIONS note and
the module docstring on app/oos/access.py for the precise, honest
boundary of what is enforced TODAY. UPDATE (Experiment Freeze &
Provenance v1, v0.1.30): `classify_range()` below IS now called, by
app/research/lifecycle.py's OOS-partition-linkage check -- but
`require_development_range()` specifically remains uncalled by
app/research/, app/features/, app/backtesting/, or
app/statistical_validation/ (those engines still have no notion of an
OOSPartition), same limitation as before, just narrower now that one
caller of this module exists.
"""

from datetime import datetime, timezone
from enum import Enum

from app.models.oos_partition import OOSPartition


class DatasetSegment(str, Enum):
    """Which side of a partition a range/bar belongs to. Mirrors
    app/models/oos_partition.py's own development_*/holdout_* naming."""

    DEVELOPMENT = "development"
    HOLDOUT = "holdout"


class PartitionLeakageError(ValueError):
    """Raised when a range crosses, or falls entirely outside, the
    segment an operation requires. A ValueError subclass, not a bare
    Exception, matching this app's existing convention for a rejected-
    input condition (e.g. FeatureCondition's pydantic validators,
    app/research/metrics.py::bars_for_window()) that a caller (an API
    route) is expected to catch and translate into a 4xx."""


def _normalize(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def classify_range(partition: OOSPartition, start: datetime, end: datetime) -> DatasetSegment | None:
    """Returns DatasetSegment.DEVELOPMENT if [start, end] (inclusive)
    falls ENTIRELY within the partition's development window,
    DatasetSegment.HOLDOUT if it falls entirely within the holdout
    window, or None otherwise -- meaning the range is malformed
    (start > end), spans both windows, spans neither (falls outside the
    partition altogether, e.g. before development_start or after
    holdout_end), or straddles the gap between them. None is
    deliberately not further subdivided ("mixed" vs. "outside" vs.
    "malformed") -- every one of those is equally "not safely usable as
    one segment", which is the only distinction any caller of this
    function has needed so far (see require_development_range() below);
    a caller that needs to tell them apart can compare `start`/`end`
    against the partition's own fields directly.
    """
    start, end = _normalize(start), _normalize(end)
    if start > end:
        return None
    if partition.development_start <= start and end <= partition.development_end:
        return DatasetSegment.DEVELOPMENT
    if partition.holdout_start <= start and end <= partition.holdout_end:
        return DatasetSegment.HOLDOUT
    return None


def require_development_range(partition: OOSPartition, start: datetime, end: datetime) -> None:
    """Raises PartitionLeakageError unless [start, end] falls entirely
    within `partition`'s development window -- the check requirement 3
    calls "accidentally creating a development experiment against
    holdout-only data" is meant to prevent.

    LIMITATION (documented, not silently assumed away -- see this
    task's own "if the architecture cannot technically prevent a
    particular leakage path yet, document it clearly" instruction):
    this function is not called by app/api/research.py,
    app/api/backtesting.py, app/api/features.py, or
    app/statistical_validation/ today. Nothing currently stops a
    developer from creating a Research Experiment, a Backtest, or a
    Feature computation whose own date range overlaps or falls entirely
    inside a holdout window defined here -- those engines have no
    notion of an OOSPartition at all yet, by this task's own explicit
    scope ("Do not modify existing Feature Engine, Research Engine,
    Backtesting Engine, or Statistical Validation behavior unless
    absolutely necessary"). This function exists as the ready-to-wire
    seam for that future integration, not as an enforced constraint
    today.
    """
    if classify_range(partition, start, end) != DatasetSegment.DEVELOPMENT:
        raise PartitionLeakageError(
            f"[{_normalize(start).isoformat()} .. {_normalize(end).isoformat()}] is not entirely within "
            f"partition {partition.id!r}'s development window "
            f"[{partition.development_start.isoformat()} .. {partition.development_end.isoformat()}]. "
            "Development-only operations (feature computation, research, experiment creation, "
            "backtesting, statistical validation, hypothesis iteration) must never be given a range "
            f"that touches the holdout window "
            f"[{partition.holdout_start.isoformat()} .. {partition.holdout_end.isoformat()}]."
        )
