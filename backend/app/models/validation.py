"""Shapes for the bar-validation pipeline (v0.1.18): what a bar looked
like AFTER app/ingestion/bar_validation.py has checked it, whether it
passed clean, passed with a flag, or was rejected outright.

Deliberately its OWN module, not defined inside app/ingestion/ (the
validator's home) or app/storage/ (the persister's home): both of
those layers need this shape --
app/ingestion/bar_validation.py produces it, app/storage/ persists it
-- and app/storage/historical_bar_repository.py is contractually
forbidden from importing anything under app.ingestion (see
tests/test_historical_bar_repository.py::TestProviderIndependence,
which greps this repo's storage module's source for exactly that
import). Putting the shared shape in app/models/ (already the neutral,
import-from-anywhere home for HistoricalBar itself) means the
repository can accept a ValidatedBar/RejectedBar without ever knowing
the validator that produced one exists.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from app.models.market_data import HistoricalBar


class ValidationStatus(str, Enum):
    """Where a bar landed after validate_bars() looked at it.

    Only VALID and FLAGGED ever reach the historical_bars table --
    REJECTED bars never get a validation_status row there at all; they
    go to the quarantine table instead (see app/storage/db.py's
    `quarantined_bars`). This enum's REJECTED member exists anyway (on
    RejectedBar.status below) so a caller inspecting a rejected record
    doesn't need a separate, parallel "what happened to this bar" type.
    """

    VALID = "valid"
    FLAGGED = "flagged"
    REJECTED = "rejected"


class ValidatedBar(BaseModel):
    """A bar that survived validation -- structurally sound (positive
    prices, sane OHLC relationships, non-negative volume, a timestamp
    not absurdly in the future) and therefore safe to store. `warnings`
    is non-empty exactly when `status` is FLAGGED: a soft, storable
    anomaly (an unusually large gap since the prior bar in its series,
    an extreme price move, arriving out of timestamp order within its
    own batch) worth keeping on the row for later audit, not a reason
    to reject data that's otherwise internally consistent.
    """

    bar: HistoricalBar
    status: ValidationStatus  # VALID or FLAGGED -- never REJECTED here
    warnings: list[str] = []


class RejectedBar(BaseModel):
    """A bar that failed a hard structural rule -- e.g. `high < low`, a
    non-positive price, negative volume -- and therefore never reaches
    historical_bars at all. `reasons` lists EVERY rule it broke, not
    just the first one found, so a caller (the save route's response,
    the quarantine table, a future review UI) can show the whole
    picture in one place. Kept, not discarded: see
    app/storage/db.py's `quarantined_bars` table -- bad data from a
    provider is something to log and skip, never something to silently
    drop on the floor.
    """

    bar: HistoricalBar
    reasons: list[str]

    @property
    def status(self) -> ValidationStatus:
        return ValidationStatus.REJECTED


class QuarantinedBarRecord(BaseModel):
    """One row read back from `quarantined_bars` (app/storage/db.py) --
    the read-side counterpart to RejectedBar. Not just RejectedBar
    itself: a row read back from the database also has `ingested_at`
    (when the rejection was logged, not carried on RejectedBar at all
    since that value doesn't exist until the row is actually written)
    and calls the same field `validation_errors` rather than `reasons`,
    matching the column name it came from -- see
    app/storage/historical_bar_repository.py::get_quarantined_bars().
    """

    bar: HistoricalBar
    ingested_at: datetime
    validation_errors: list[str]
