"""OOS / Holdout Partition Framework v1 (v0.1.29): the persisted shape
of a dataset partition -- a declaration that a symbol/timeframe/
provider's already-stored historical bars (app/storage/
historical_bar_repository.py -- no bars are duplicated here, only
referenced by date range) are split into a Development/Research window
and a later, non-overlapping Holdout/Out-of-Sample window.

This is a boundary-setting feature, not a validation engine: no
statistical test, no optimizer, no ML, no strategy construction reads a
holdout window through anything built here yet. What this DOES
establish is: (1) a partition cannot even be constructed with an
invalid or overlapping range (see _validate_range_ordering below -- a
structural guarantee, enforced by pydantic, not a convention someone
has to remember to follow), and (2) app/oos/partition.py's
classify_range()/require_development_range() and app/oos/access.py's
get_development_bars()/get_holdout_bars() are the seam a future
OOS-validation operation, and a future Research/Backtesting/Statistical
Validation integration, would call through -- see both modules' own
docstrings, and README's OOS/Holdout Framework section, for exactly
what is and is not technically prevented today.

Kept a leaf module, matching app/models/research.py's and
app/models/backtesting.py's own discipline: only pydantic and the
stdlib (hashlib, for the deterministic id) are imported here -- never
app.oos, app.storage, or app.api.
"""

import hashlib
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator, model_validator


def _to_utc(value: datetime) -> datetime:
    """Normalizes a boundary timestamp to a UTC, tz-aware datetime --
    the same "naive is assumed UTC, never rejected" rule
    app/storage/historical_bar_repository.py::_to_storage_timestamp()
    already applies to a stored bar's own timestamp, so a partition
    boundary and the bars it is compared against are always converted
    the same way."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _validate_range_ordering(
    development_start: datetime,
    development_end: datetime,
    holdout_start: datetime,
    holdout_end: datetime,
) -> None:
    """The core leakage-prevention rule (requirement 1/3): each of the
    two windows must itself be non-degenerate (start < end -- a
    zero-width or inverted window has no bars that could ever fall
    inside it, which is itself a silent way to end up with an "empty
    development" or "empty holdout" partition no caller asked for), AND
    development_end must be STRICTLY before holdout_start. Strict, not
    `<=`: two windows that touch at a single shared instant would let a
    bar timestamped exactly at that boundary be ambiguous about which
    side it's on -- treated the same as an overlap, and rejected.
    Because both windows are independently required to be ordered
    start-before-end, and development_end < holdout_start is enforced
    directly, "the holdout range overlaps the development range" and
    "development_start..development_end is not entirely before
    holdout_start..holdout_end" collapse into this one check -- there
    is no separate overlap case this does not already catch.
    """
    if development_start >= development_end:
        raise ValueError(
            f"development_start ({development_start.isoformat()}) must be strictly before "
            f"development_end ({development_end.isoformat()})."
        )
    if holdout_start >= holdout_end:
        raise ValueError(
            f"holdout_start ({holdout_start.isoformat()}) must be strictly before "
            f"holdout_end ({holdout_end.isoformat()})."
        )
    if development_end >= holdout_start:
        raise ValueError(
            f"development_end ({development_end.isoformat()}) must be strictly before "
            f"holdout_start ({holdout_start.isoformat()}) -- overlapping or touching "
            "development/holdout ranges are rejected."
        )


def compute_partition_id(
    *,
    provider: str,
    symbol: str,
    timeframe: str,
    development_start: datetime,
    development_end: datetime,
    holdout_start: datetime,
    holdout_end: datetime,
) -> str:
    """Requirement 5 (determinism): the SAME provider/symbol/timeframe/
    development range/holdout range must always produce the SAME id --
    never a random uuid4 (contrast app/models/research.py::Experiment.new()
    and app/models/backtesting.py::Backtest.new(), which deliberately DO
    use a random id, because two experiments/backtests with identical
    parameters are still two independent runs a caller may legitimately
    want to keep separate). A dataset partition is different: it is a
    DEFINITION, not a run -- the same definition created twice should be
    recognized as the same partition, not silently duplicated (see
    app/storage/oos_partition_repository.py::save_partition()'s
    INSERT-OR-IGNORE-on-this-id idempotency, which depends on this).

    provider is lower-cased and symbol upper-cased before hashing so
    "Alpaca"/"alpaca" or "tsla"/"TSLA" collide to the same id, matching
    this app's existing case-normalization convention elsewhere
    (Experiment.new() upper-cases symbol the same way). Every timestamp
    is normalized to UTC before hashing, so a caller who passes the
    same instant in a different offset still gets the same id.
    """
    key = "|".join(
        [
            provider.strip().lower(),
            symbol.strip().upper(),
            timeframe.strip(),
            _to_utc(development_start).isoformat(),
            _to_utc(development_end).isoformat(),
            _to_utc(holdout_start).isoformat(),
            _to_utc(holdout_end).isoformat(),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


class OOSPartitionCreateRequest(BaseModel):
    """Body of POST /oos/partitions -- everything about a partition
    except its id/created_at, which the server assigns (see
    OOSPartition.new()). `provider` is required, not defaulted, same
    reasoning as ExperimentCreateRequest.provider (app/models/
    research.py): the underlying bars' identity key includes provider,
    so a partition that didn't say which provider's saved bars it
    covers would be ambiguous about where its data actually came from.

    `label` is optional, free-text, human-facing only (e.g. "2024 walk-
    forward split #1") -- it is NEVER part of the deterministic id (see
    compute_partition_id above), so two partitions with identical
    provider/symbol/timeframe/ranges but different labels are still,
    correctly, treated as the SAME partition.
    """

    symbol: str
    timeframe: str
    provider: str
    development_start: datetime
    development_end: datetime
    holdout_start: datetime
    holdout_end: datetime
    label: str | None = None

    @field_validator("symbol", "timeframe", "provider")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # Requirement 3, "missing partition metadata": pydantic already
        # rejects a genuinely absent field; this additionally rejects
        # the "" / "   " a caller could otherwise slip past that check.
        if not value or not value.strip():
            raise ValueError("must not be blank.")
        return value

    @field_validator("development_start", "development_end", "holdout_start", "holdout_end")
    @classmethod
    def _normalize_utc(cls, value: datetime) -> datetime:
        return _to_utc(value)

    @model_validator(mode="after")
    def _validate_ranges(self) -> "OOSPartitionCreateRequest":
        _validate_range_ordering(self.development_start, self.development_end, self.holdout_start, self.holdout_end)
        return self


class OOSPartition(BaseModel):
    """The persisted partition record (app/storage/
    oos_partition_repository.py). Every field is set once, at creation,
    and never changes -- there is no "edit a partition" or "extend the
    holdout window" operation, deliberately: a partition whose boundary
    could move after the fact would defeat the entire point of drawing
    the line before it is used (requirement: prevent ACCIDENTAL holdout
    use, which a mutable boundary would make trivial -- just move the
    boundary and the outcome you already peeked at becomes
    "development" retroactively).

    Provenance (requirement 4 -- "where did this data come from, what
    period does it represent, which portion is development vs. holdout")
    is answered entirely by this record's own fields: `provider`
    answers "where from", `symbol`/`timeframe` answer "what", and the
    four boundary timestamps answer "which period, and which portion".
    No bars are duplicated onto this record -- it holds a date-range
    REFERENCE into app/storage/historical_bar_repository.py's existing
    `historical_bars` table, read via app/oos/access.py, never a copy.
    """

    id: str
    symbol: str
    timeframe: str
    provider: str
    development_start: datetime
    development_end: datetime
    holdout_start: datetime
    holdout_end: datetime
    label: str | None = None
    created_at: datetime

    @field_validator("development_start", "development_end", "holdout_start", "holdout_end", "created_at")
    @classmethod
    def _normalize_utc(cls, value: datetime) -> datetime:
        return _to_utc(value)

    @model_validator(mode="after")
    def _validate_ranges(self) -> "OOSPartition":
        # Defense in depth: OOSPartitionCreateRequest already enforces
        # this at the API boundary, but OOSPartition itself (e.g. one
        # rebuilt from a database row -- see oos_partition_repository.py)
        # must never be constructible in an invalid state either.
        _validate_range_ordering(self.development_start, self.development_end, self.holdout_start, self.holdout_end)
        return self

    @classmethod
    def new(cls, request: OOSPartitionCreateRequest) -> "OOSPartition":
        """Assigns the server-owned fields (id/created_at) for a
        brand-new partition. Symbol/timeframe scope validation (against
        ALLOWED_SYMBOLS/ALLOWED_TIMEFRAMES) happens at the API route
        BEFORE this is called -- see app/api/oos_partitions.py, the
        same layer that already owns those checks for Research's and
        historical-data's own routes."""
        partition_id = compute_partition_id(
            provider=request.provider,
            symbol=request.symbol,
            timeframe=request.timeframe,
            development_start=request.development_start,
            development_end=request.development_end,
            holdout_start=request.holdout_start,
            holdout_end=request.holdout_end,
        )
        return cls(
            id=partition_id,
            symbol=request.symbol.upper(),
            timeframe=request.timeframe,
            provider=request.provider,
            development_start=request.development_start,
            development_end=request.development_end,
            holdout_start=request.holdout_start,
            holdout_end=request.holdout_end,
            label=request.label,
            created_at=datetime.now(timezone.utc),
        )
