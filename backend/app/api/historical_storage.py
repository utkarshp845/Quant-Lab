"""API routes for persisting and retrieving historical bars (v0.1.17;
validation + quarantine added v0.1.18):

    POST /market-data/history/save               HistoricalBar[] -> validate -> database
    GET  /market-data/{symbol}/history/stored     database -> HistoricalBar[]
    GET  /market-data/{symbol}/history/quarantined  quarantine table -> QuarantinedBarRecord[]

All three routes call app.storage.historical_bar_repository -- neither
this file nor anything upstream of it (the frontend, a future Quant Lab
consumer) ever writes SQL; see that module's docstring for why that
boundary is the actual architectural point here, not a style choice.

SAVE is a deliberate, separate action, not an automatic side effect of
GET /market-data/{symbol}/history (app/api/historical_data.py). It also
does NOT re-fetch from a provider -- it takes the bars the caller
already fetched and hands them straight through
app.ingestion.bar_validation.validate_bars() to the repository. Two
reasons this stays a manual click rather than an automatic side effect
of every fetch, both deliberate: (1) the spec this route was originally
built against explicitly asked for a manual "Save to Database" action,
and (2) re-fetching to save would mean a second, redundant provider
call for data the caller is holding already. (v0.1.18:
app/ingestion/auto_ingest.py is a second, independent caller of this
same validate-then-save path, for the unattended polling case -- see
its docstring. This route itself is unchanged by that; it still only
ever saves bars a caller hands it.)

SAVE now runs every bar through validate_bars() before it reaches the
repository: bars that fail a hard structural rule (impossible OHLCV
values, an in-batch duplicate) are logged to the quarantine table via
save_rejected_bars() and echoed back in the response's `rejected`
list, never inserted into historical_bars at all. Bars that pass, flagged
or not, go to save_validated_bars() -- see SaveBarsResponse's docstring
for the full accounting.

STORED-READ reuses HistoricalBarsResponse -- the identical shape GET
.../history returns -- and validates symbol/timeframe the same way that
route does (ALLOWED_SYMBOLS/ALLOWED_TIMEFRAMES from historical_data.py,
not a second, could-drift copy of that list). `provider` stays a
required query param here too: the storage layer's identity key
includes provider (see app/storage/db.py), so reading "TSLA daily bars"
without saying which provider's copy would be ambiguous the moment more
than one provider's bars for the same symbol/timeframe/period have ever
been saved. QUARANTINED-READ mirrors STORED-READ exactly, against
get_quarantined_bars() instead of get_bars().
"""

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from app.api.historical_data import ALLOWED_SYMBOLS, ALLOWED_TIMEFRAMES
from app.ingestion.bar_validation import validate_bars
from app.models.historical_storage import (
    QuarantinedBarsResponse,
    RejectedBarInfo,
    SaveBarsRequest,
    SaveBarsResponse,
)
from app.models.market_data import HistoricalBarsResponse
from app.models.validation import ValidationStatus
from app.storage import historical_bar_repository

router = APIRouter()


@router.post("/market-data/history/save", response_model=SaveBarsResponse)
def save_historical_bars(payload: SaveBarsRequest) -> SaveBarsResponse:
    validation = validate_bars(payload.bars)

    save_result = historical_bar_repository.save_validated_bars(validation.accepted)
    if validation.rejected:
        historical_bar_repository.save_rejected_bars(validation.rejected)

    flagged = sum(1 for v in validation.accepted if v.status == ValidationStatus.FLAGGED)

    return SaveBarsResponse(
        total=len(payload.bars),
        inserted=save_result.inserted,
        skipped_duplicates=save_result.skipped_duplicates,
        flagged=flagged,
        rejected_invalid=len(validation.rejected),
        rejected=[
            RejectedBarInfo(symbol=r.bar.symbol, timestamp=r.bar.timestamp, reasons=r.reasons)
            for r in validation.rejected
        ],
    )


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


@router.get("/market-data/{symbol}/history/quarantined", response_model=QuarantinedBarsResponse)
def get_quarantined_historical_bars(
    symbol: str,
    start: date = Query(..., description="Inclusive start date, e.g. 2026-08-01"),
    end: date = Query(..., description="Inclusive end date, e.g. 2026-08-15"),
    timeframe: str = Query("1d", description="One of: " + ", ".join(sorted(ALLOWED_TIMEFRAMES))),
    provider: str = Query(..., description="Which provider's rejected bars to read, e.g. alpaca, massive"),
) -> QuarantinedBarsResponse:
    """The audit trail for check #12 (quarantine): every bar
    validate_bars() rejected for this symbol/timeframe/provider/date-
    range, and every reason it failed -- see
    app/ingestion/bar_validation.py's module docstring for the full
    rule list this is reporting against."""
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

    quarantined = historical_bar_repository.get_quarantined_bars(
        symbol=symbol, timeframe=timeframe, provider=provider, start=start, end=end
    )

    return QuarantinedBarsResponse(
        symbol=symbol,
        provider=provider,
        timeframe=timeframe,
        start=start,
        end=end,
        quarantined_count=len(quarantined),
        quarantined_bars=quarantined,
    )
