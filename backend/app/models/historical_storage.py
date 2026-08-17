"""Request/response shapes for the historical-bar storage routes
(v0.1.17, app/api/historical_storage.py).

GET /market-data/{symbol}/history/stored deliberately reuses
HistoricalBarsResponse (app/models/market_data.py) as-is, not a
separate "stored" response shape -- the whole point of persisting
HistoricalBar rows verbatim is that a caller (the frontend, a future
Quant Lab consumer) sees the exact same shape whether the bars came
from a live provider fetch or a database read. Only the SAVE side needs
its own models, below.
"""

from datetime import date, datetime

from pydantic import BaseModel

from app.models.market_data import HistoricalBar
from app.models.validation import QuarantinedBarRecord


class SaveBarsRequest(BaseModel):
    """Body of POST /market-data/history/save -- the bars themselves,
    already fetched (GET .../history) and already normalized. This
    route deliberately does NOT take symbol/start/end/provider and
    re-fetch from a provider itself: saving is a separate, deliberate
    action on data the caller already has in hand (see this app's
    "Save to Database" button), not an automatic side effect of every
    fetch -- see app/api/historical_storage.py's module docstring.
    """

    bars: list[HistoricalBar]


class RejectedBarInfo(BaseModel):
    """One bar POST /market-data/history/save's validation step
    (app/ingestion/bar_validation.py) rejected outright -- echoed back
    in the response, not just logged to `quarantined_bars`, so the
    caller that submitted a bad batch can see exactly which bars and
    why without a separate query to GET .../history/quarantined."""

    symbol: str
    timestamp: datetime
    reasons: list[str]


class SaveBarsResponse(BaseModel):
    """What POST /market-data/history/save actually did. `total` is
    every bar in the request; `inserted`/`skipped_duplicates` account
    for what happened to the bars that passed validation (mirrors
    app.storage.historical_bar_repository.SaveResult exactly), and
    (v0.1.18) `flagged`/`rejected_invalid`/`rejected` account for what
    app/ingestion/bar_validation.py found before those bars ever
    reached the repository -- five different, all-honest outcomes a
    bare success/failure boolean would hide:
    inserted + skipped_duplicates + rejected_invalid == total (a
    flagged bar is still either inserted or a duplicate -- FLAGGED
    is a stricter status than "counted", not a separate bucket).
    """

    total: int
    inserted: int
    skipped_duplicates: int
    flagged: int = 0
    rejected_invalid: int = 0
    rejected: list[RejectedBarInfo] = []


class QuarantinedBarsResponse(BaseModel):
    """The full response body GET /market-data/{symbol}/history/quarantined
    returns -- the `quarantined_bars` audit trail (app/storage/db.py)
    for this symbol/timeframe/provider/date-range, same self-describing
    shape as HistoricalBarsResponse (app/models/market_data.py): what
    was asked for, plus how many rows came back, not just the rows
    themselves."""

    symbol: str
    provider: str
    timeframe: str
    start: date
    end: date
    quarantined_count: int
    quarantined_bars: list[QuarantinedBarRecord]
