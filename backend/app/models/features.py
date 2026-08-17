"""Persistent shapes for Feature Engine v1 (app/features/, app/storage/
feature_repository.py, app/api/features.py): one FeatureRecord per
normalized historical bar, transforming that bar (plus its own trailing
history, and -- for eligible symbols -- SPY/QQQ's bars) into the fixed
feature contract this app was built against.

One sub-model per section of that contract (PRICE/VOLUME/VOLATILITY/
MARKET CONTEXT/PRICE POSITION) -- not one flat 30-field model -- so
each category can be computed and unit-tested independently (see
app/features/price.py, volume.py, volatility.py, market_context.py,
price_position.py, one module per sub-model here). The flattening into
individual SQL columns happens only at the storage boundary (see
app/storage/feature_repository.py), the same "model shape need not
equal column shape" pattern app/storage/historical_bar_repository.py
already uses.

Every leaf feature value is `float | None` -- see the module docstrings
under app/features/ for exactly when each one is None (insufficient
history, a missing bar inside a required window, a zero denominator,
or -- for market context only -- the symbol not being configured for
it). None is this app's existing convention for "cannot honestly
compute this" (see app/models/research.py::ExperimentResults), never a
fabricated 0.0.

Kept a leaf module, like app/models/market_data.py and app/models/
research.py: only pydantic, the stdlib, and app.models.market_data
(for HistoricalBar's `symbol`/`timeframe`/`provider` typing
conventions) are imported here -- never app.features or app.api.
"""

from datetime import date, datetime

from pydantic import BaseModel

# Bumped only if the feature CONTRACT itself changes (a formula, a
# window size, a new/removed feature) -- recorded on every persisted
# row (see FeatureRecord.feature_version below) so a future reader can
# tell "this row reflects the current contract" from "this row was
# computed under an earlier version of it" without re-deriving that
# from `calculated_at` alone.
FEATURE_CONTRACT_VERSION = "v1"


class PriceFeatures(BaseModel):
    """Trailing returns ending at the bar's own close -- see
    app/features/price.py for the exact no-look-ahead, missing-bar-
    aware computation. `return_5m` is `close[t] / close[t - 5m] - 1`,
    literally: the base bar must exist at exactly 5 (15/30/60) minutes
    before this bar's timestamp, not merely "N bars back by position"
    -- a gap in between means None, not a value computed across the
    hole (see app/features/timeframes.py::is_contiguous_window)."""

    return_5m: float | None
    return_15m: float | None
    return_30m: float | None
    return_60m: float | None


class VolumeFeatures(BaseModel):
    """`volume` is the bar's own volume, always known (never None --
    it is not a derived calculation, just carried through). The other
    two ARE derived and can be None -- see app/features/volume.py."""

    volume: int
    relative_volume: float | None
    volume_acceleration: float | None


class VolatilityFeatures(BaseModel):
    """See app/features/volatility.py for the full rolling-window and
    annualization definitions (252 trading days/year, 390 minutes/
    trading day -- documented there)."""

    realized_volatility: float | None
    atr: float | None
    volatility_ratio: float | None
    volatility_percentile: float | None


class MarketContextFeatures(BaseModel):
    """SPY/QQQ returns and this symbol's relative strength against
    each, at the same four horizons as PriceFeatures. Only present at
    all (see FeatureRecord.market_context below, which is the whole
    MarketContextFeatures instance or None) for symbols configured as
    market-context-eligible -- see app/features/market_context.py."""

    spy_return_5m: float | None
    spy_return_15m: float | None
    spy_return_30m: float | None
    spy_return_60m: float | None
    qqq_return_5m: float | None
    qqq_return_15m: float | None
    qqq_return_30m: float | None
    qqq_return_60m: float | None
    relative_strength_spy_5m: float | None
    relative_strength_spy_15m: float | None
    relative_strength_spy_30m: float | None
    relative_strength_spy_60m: float | None
    relative_strength_qqq_5m: float | None
    relative_strength_qqq_15m: float | None
    relative_strength_qqq_30m: float | None
    relative_strength_qqq_60m: float | None


class PricePositionFeatures(BaseModel):
    """Where the current close sits relative to VWAP/SMA20/SMA50 and
    today's own range -- see app/features/price_position.py for the
    session definition (NY-local calendar day) VWAP and the range
    position are computed against."""

    vwap_distance: float | None
    ma20_distance: float | None
    ma50_distance: float | None
    intraday_range_position: float | None


class FeatureRecord(BaseModel):
    """One row: a single normalized historical bar, transformed into
    the full feature contract. `market_context` is the entire
    MarketContextFeatures sub-model, or None -- None means "this
    symbol is not configured for market context at all" (see
    app/features/market_context.py), a different, explicit reason from
    any individual leaf field being None inside a present sub-model
    (which means "computed, but undefined for this observation").

    `calculated_at`/`feature_contract_version` are the "feature-
    calculation metadata" this app's spec asked to be preserved
    alongside `symbol`/`timestamp`/`timeframe` -- when this row was
    computed, and against which version of the contract, both
    independent of the market bar's own `timestamp`.
    """

    symbol: str
    timestamp: datetime
    timeframe: str
    provider: str
    calculated_at: datetime
    feature_contract_version: str = FEATURE_CONTRACT_VERSION

    price: PriceFeatures
    volume: VolumeFeatures
    volatility: VolatilityFeatures
    market_context: MarketContextFeatures | None
    price_position: PricePositionFeatures


class FeatureComputeRequest(BaseModel):
    """Body of POST /features/compute -- app/api/features.py fetches
    the underlying's own bars (and, when eligible, SPY/QQQ's) for
    exactly this symbol/timeframe/provider/date-range and hands them to
    app/features/engine.py; nothing here is fetched from a live
    provider (rule 8/data-integrity: this reads only the already-
    normalized, already-stored historical_bars dataset).

    `include_market_context` defaults to True -- but only symbols
    app/api/features.py has explicitly configured as market-context-
    eligible (ALLOWED_SYMBOLS, i.e. TSLA/NVDA today) actually receive
    it either way; requesting it for MCL is a no-op, not an error, see
    that route's own docstring.
    """

    symbol: str
    start_date: date
    end_date: date
    timeframe: str
    provider: str
    include_market_context: bool = True


class FeatureComputeResponse(BaseModel):
    """What POST /features/compute did -- self-describing, the same
    "echo what was asked for, not just what came back" convention
    HistoricalBarsResponse (app/models/market_data.py) already uses.
    `market_context_applied` says whether this symbol actually was
    market-context-eligible for this run (distinct from whether the
    caller merely requested it via `include_market_context`)."""

    symbol: str
    timeframe: str
    provider: str
    start: date
    end: date
    bar_count: int
    feature_count: int
    market_context_applied: bool
    features: list[FeatureRecord]


class FeatureRecordsResponse(BaseModel):
    """The full response body GET /features/{symbol} returns -- every
    previously-computed-and-saved FeatureRecord for this symbol/
    timeframe/provider/date-range, oldest first."""

    symbol: str
    provider: str
    timeframe: str
    start: date
    end: date
    feature_count: int
    features: list[FeatureRecord]
