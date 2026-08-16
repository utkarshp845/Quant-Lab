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

from pydantic import BaseModel

from app.models.market_data import HistoricalBar


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


class SaveBarsResponse(BaseModel):
    """What POST /market-data/history/save actually did -- mirrors
    app.storage.historical_bar_repository.SaveResult exactly, so a
    caller can tell "saved N new bars" apart from "all N were already
    stored" apart from "saved some, skipped some" -- three different,
    both-honest outcomes a bare success/failure boolean would hide.
    """

    total: int
    inserted: int
    skipped_duplicates: int
