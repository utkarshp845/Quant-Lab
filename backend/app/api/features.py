"""API routes for Feature Engine v1:

    POST /features/compute      bars -> engine -> historical_features (persisted)
    GET  /features/{symbol}     historical_features -> FeatureRecord[]
    GET  /features/vocabulary   the canonical feature vocabulary (v0.1.24) -- see
                                 app/features/vocabulary.py. Research's condition
                                 builder populates its feature dropdown from this
                                 route rather than a hardcoded list -- requirement 2
                                 of the Feature <-> Research integration.

Reads ONLY from app.storage.historical_bar_repository -- the existing
normalized historical dataset -- never raw/quarantined data, and never
writes to `historical_bars` at all (this feature's data-integrity
rules mirror Research v1's own: the feature layer reads bars, it never
modifies them).

**Market-context eligibility default lives here, not in the pure
engine** (see app/features/engine.py's own docstring): MARKET_CONTEXT_SYMBOLS
below reuses ALLOWED_SYMBOLS (app/api/historical_data.py) -- today,
TSLA and NVDA, the only two equities this app formally supports end to
end. That is "explicitly configured", per this feature's own rule ("Do
not apply SPY/QQQ context to MCL unless explicitly configured") --
MCL (or any symbol outside this set) simply is not a member, so
compute_and_save_features() below never fetches SPY/QQQ bars or asks
the engine for market context on its behalf, without needing a special
case. A future symbol can be added to market context by editing this
one set, not by touching app/features/ at all.

`symbol` itself is NOT restricted to ALLOWED_SYMBOLS on either route
here, unlike app/api/historical_data.py's live-fetch route: Feature
Engine v1 operates on whatever a symbol's normalized bars already are
in historical_bars (which can include symbols ingested only via CSV,
e.g. MCL -- see app/ingestion/ohlcv_csv.py), not only the two symbols
this app's live-provider routes are scoped to.
"""

from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app.api.historical_data import ALLOWED_SYMBOLS, ALLOWED_TIMEFRAMES
from app.features.engine import compute_features
from app.features.vocabulary import FeatureDefinition, get_feature_vocabulary
from app.models.features import FeatureComputeRequest, FeatureComputeResponse, FeatureRecordsResponse
from app.models.market_data import HistoricalBar
from app.storage import feature_repository, historical_bar_repository

router = APIRouter()

# See the module docstring: the only place this app decides WHICH
# symbols get SPY/QQQ market context. Reusing ALLOWED_SYMBOLS (rather
# than redefining an independent set) means this can never silently
# drift from "the equities this app actually supports" -- if that set
# ever grows, market-context eligibility grows with it automatically,
# which is the intended behavior (the spec's own example symbols,
# TSLA and NVDA, ARE ALLOWED_SYMBOLS today).
MARKET_CONTEXT_SYMBOLS = frozenset(ALLOWED_SYMBOLS)
SPY_SYMBOL = "SPY"
QQQ_SYMBOL = "QQQ"


def _validate_common(timeframe: str, start: date, end: date) -> None:
    if timeframe not in ALLOWED_TIMEFRAMES:
        raise HTTPException(
            status_code=400, detail=f"Unsupported timeframe {timeframe!r}. Allowed: {sorted(ALLOWED_TIMEFRAMES)}"
        )
    if end < start:
        raise HTTPException(status_code=400, detail="end date must not be before start date")


@router.post("/features/compute", response_model=FeatureComputeResponse)
def compute_and_save_features(request: FeatureComputeRequest) -> FeatureComputeResponse:
    _validate_common(request.timeframe, request.start_date, request.end_date)
    symbol = request.symbol.upper()

    bars = historical_bar_repository.get_bars(
        symbol=symbol,
        timeframe=request.timeframe,
        provider=request.provider,
        start=request.start_date,
        end=request.end_date,
    )

    market_context_applied = request.include_market_context and symbol in MARKET_CONTEXT_SYMBOLS
    spy_bars: list[HistoricalBar] = []
    qqq_bars: list[HistoricalBar] = []
    if market_context_applied:
        spy_bars = historical_bar_repository.get_bars(
            symbol=SPY_SYMBOL,
            timeframe=request.timeframe,
            provider=request.provider,
            start=request.start_date,
            end=request.end_date,
        )
        qqq_bars = historical_bar_repository.get_bars(
            symbol=QQQ_SYMBOL,
            timeframe=request.timeframe,
            provider=request.provider,
            start=request.start_date,
            end=request.end_date,
        )

    records = compute_features(
        symbol=symbol,
        timeframe=request.timeframe,
        provider=request.provider,
        bars=bars,
        calculated_at=datetime.now(timezone.utc),
        spy_bars=spy_bars,
        qqq_bars=qqq_bars,
        market_context_symbols=MARKET_CONTEXT_SYMBOLS if market_context_applied else frozenset(),
    )
    feature_repository.save_features(records)

    return FeatureComputeResponse(
        symbol=symbol,
        timeframe=request.timeframe,
        provider=request.provider,
        start=request.start_date,
        end=request.end_date,
        bar_count=len(bars),
        feature_count=len(records),
        market_context_applied=market_context_applied,
        features=records,
    )


@router.get("/features/vocabulary", response_model=list[FeatureDefinition])
def get_vocabulary() -> list[FeatureDefinition]:
    """The canonical feature vocabulary (v0.1.24, app/features/
    vocabulary.py) -- every feature Research's condition builder (or any
    future consumer) can reference by `feature_id`, with its name, type,
    description, supported operators, and Feature Engine contract
    version. A static, in-process list (no database read) -- this
    route exists so nothing outside app/features/ needs its own copy of
    which features exist."""
    return get_feature_vocabulary()


@router.get("/features/{symbol}", response_model=FeatureRecordsResponse)
def get_stored_features(
    symbol: str,
    start: date = Query(..., description="Inclusive start date, e.g. 2026-08-01"),
    end: date = Query(..., description="Inclusive end date, e.g. 2026-08-15"),
    timeframe: str = Query("5m", description="One of: " + ", ".join(sorted(ALLOWED_TIMEFRAMES))),
    provider: str = Query(..., description="Which provider's saved features to read, e.g. alpaca, massive, csv"),
) -> FeatureRecordsResponse:
    _validate_common(timeframe, start, end)
    symbol = symbol.upper()

    features = feature_repository.get_features(symbol=symbol, timeframe=timeframe, provider=provider, start=start, end=end)

    return FeatureRecordsResponse(
        symbol=symbol,
        provider=provider,
        timeframe=timeframe,
        start=start,
        end=end,
        feature_count=len(features),
        features=features,
    )
