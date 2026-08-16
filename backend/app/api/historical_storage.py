"""API routes for persisting and retrieving historical bars (v0.1.17):

    POST /market-data/history/save            HistoricalBar[] -> database
    GET  /market-data/{symbol}/history/stored  database -> HistoricalBar[]

Both routes call app.storage.historical_bar_repository -- neither this
file nor anything upstream of it (the frontend, a future Quant Lab
consumer) ever writes SQL; see that module's docstring for why that
boundary is the actual architectural point here, not a style choice.

SAVE is a deliberate, separate action, not an automatic side effect of
GET /market-data/{symbol}/history (app/api/historical_data.py). It also
does NOT re-fetch from a provider -- it takes the bars the caller
already fetched and hands them straight to the repository. Two reasons,
both deliberate: (1) the spec this route was built against explicitly
asked for a manual "Save to Database" action rather than saving every
fetch automatically, and (2) re-fetching to save would mean a second,
redundant provider call (a second rate-limit hit, a second chance to
disagree with the first fetch) for data the caller is holding already.

STORED-READ reuses HistoricalBarsResponse -- the identical shape GET
.../history returns -- and validates symbol/timeframe the same way that
route does (ALLOWED_SYMBOLS/ALLOWED_TIMEFRAMES from historical_data.py,
not a second, could-drift copy of that list). `provider` stays a
required query param here too: the storage layer's identity key
includes provider (see app/storage/db.py), so reading "TSLA daily bars"
without saying which provider's copy would be ambiguous the moment more
than one provider's bars for the same symbol/timeframe/period have ever
been saved.
"""

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from app.api.historical_data import ALLOWED_SYMBOLS, ALLOWED_TIMEFRAMES
from app.models.historical_storage import SaveBarsRequest, SaveBarsResponse
from app.models.market_data import HistoricalBarsResponse
from app.storage import historical_bar_repository

router = APIRouter()


@router.post("/market-data/history/save", response_model=SaveBarsResponse)
def save_historical_bars(payload: SaveBarsRequest) -> SaveBarsResponse:
    result = historical_bar_repository.save_bars(payload.bars)
    return SaveBarsResponse(total=result.total, inserted=result.inserted, skipped_duplicates=result.skipped_duplicates)


@router.get("/market-data/{symbol}/history/stored", response_model=HistoricalBarsResponse)
def get_stored_historical_bars(
    symbol: str,
    start: date = Query(..., description="Inclusive start date, e.g. 2026-08-01"),
    end: date = Query(..., description="Inclusive end date, e.g. 2026-08-15"),
    timeframe: str = Query("1d", description="One of: " + ", ".join(sorted(ALLOWED_TIMEFRAMES))),
    provider: str = Query(..., description="Which provider's saved bars to read, e.g. alpaca, massive"),
) -> HistoricalBarsResponse:
    symbol = symbol.upper()
    if symbol not in ALLOWED_SYMBOLS:
        raise HTTPException(
            status_code=400, detail=f"Symbol {symbol!r} is not supported yet. Allowed: {sorted(ALLOWED_SYMBOLS)}"
        )
    if timeframe not in ALLOWED_TIMEFRAMES:
        raise HTTPException(
            status_code=400, detail=f"Unsupported timeframe {timeframe!r}. Allowed: {sorted(ALLOWED_TIMEFRAMES)}"
        )
    if end < start:
        raise HTTPException(status_code=400, detail="end date must not be before start date")

    bars = historical_bar_repository.get_bars(symbol=symbol, timeframe=timeframe, provider=provider, start=start, end=end)

    return HistoricalBarsResponse(
        symbol=symbol,
        provider=provider,
        timeframe=timeframe,
        start=start,
        end=end,
        bar_count=len(bars),
        bars=bars,
    )
