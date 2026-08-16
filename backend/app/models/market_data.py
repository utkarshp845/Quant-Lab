"""Normalized market-data models beyond a single options chain.

NormalizedOption (option_chain.py) already covers "one option contract,
right now" -- that's all the CSV path and today's calculator need, and
it stays exactly as-is; nothing here changes it or what depends on it.

MarketBar and Quote are provider-facing contracts (see providers/base.py's
get_historical_data() / get_latest_quote()) -- Quote has been live since
v0.1.9 via GET /market-data/{symbol}/quote. MarketBar got its first real
caller in v0.1.16 (GET /market-data/{symbol}/history, see
app/api/historical_data.py) -- before that it existed only as the target
shape providers' get_historical_data() already returned. LiveQuote and
HistoricalBar (below) are those two routes' actual HTTP response shapes --
flat supersets assembled by the route itself, never a provider contract.
HistoricalBar (v0.1.17) is also what the storage layer (app/storage/)
persists and loads -- see that module for why a database is just another
source/sink for the same normalized shape, not a reason to invent a
second one.
"""

from datetime import date, datetime

from pydantic import BaseModel


class MarketTimestamp(BaseModel):
    """A point in time, tagged with where the observation came from.

    Separate from a bare datetime so a caller can always ask "was this
    live, delayed, or a CSV snapshot from an hour ago" without having
    to also track a provider name alongside every timestamp by hand.
    """

    value: datetime
    source: str  # provider name, e.g. "csv", "alpaca" -- same convention as NormalizedChainResult.source


class MarketBar(BaseModel):
    """One OHLCV bar for an underlying -- the unit get_historical_data returns.

    This is the provider-facing contract every MarketDataProvider's
    get_historical_data() returns, unchanged since it was first defined --
    see HistoricalBar below for the different, flat shape
    GET /market-data/{symbol}/history (v0.1.16) actually returns to the
    frontend, same split as Quote/LiveQuote.
    """

    symbol: str
    timestamp: MarketTimestamp
    open: float
    high: float
    low: float
    close: float
    volume: int


class HistoricalBar(BaseModel):
    """The normalized, provider-independent historical-bar shape used
    everywhere OUTSIDE a provider implementation (v0.1.16) -- the HTTP
    response GET /market-data/{symbol}/history actually returns, AND
    (v0.1.17) what the storage layer (app/storage/) saves and loads.
    One canonical shape flowing provider -> route -> storage -> UI is
    the whole point: nothing downstream of a provider (this route, the
    storage layer, any future Quant Lab consumer) should have to know
    or care whether a HistoricalBar came from Alpaca, Massive, or the
    database -- see app/storage/historical_bar_repository.py's module
    docstring for the storage half of that rule.

    Flat, the same way LiveQuote flattens Quote: `provider` is promoted
    from MarketBar.timestamp.source to a top-level field, `timestamp` is
    a plain datetime instead of a nested MarketTimestamp, and (v0.1.17)
    `timeframe` is included per-bar -- even though it's constant across
    one fetch/save/load call, a bar's timeframe is as much a part of its
    identity as its provider (see the storage layer's UNIQUE constraint:
    provider+symbol+timeframe+timestamp), so it travels with the bar
    itself rather than living only at the surrounding response's top level.

    Deliberately NOT a change to MarketBar itself, for the same reason
    LiveQuote isn't a change to Quote: MarketBar is what every
    provider's get_historical_data() is tested against (see
    tests/test_alpaca_provider.py etc.); this is purely a shape
    assembled by callers of a provider, never a provider contract.
    """

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    provider: str
    timeframe: str

    @classmethod
    def from_market_bar(cls, bar: MarketBar, *, timeframe: str) -> "HistoricalBar":
        return cls(
            symbol=bar.symbol,
            timestamp=bar.timestamp.value,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            provider=bar.timestamp.source,
            timeframe=timeframe,
        )


class Quote(BaseModel):
    """A live quote for an underlying (not an option) -- get_latest_quote's return type.

    Deliberately does NOT reuse NormalizedOption: that model's fields
    (strike, option_type, delta, ...) don't apply to the underlying
    itself, and stretching one model to cover both would mean either
    fake option fields on a stock quote or optional fields nobody can
    tell apart from "not fetched yet" vs. "doesn't apply here."

    This is the provider-facing contract -- every MarketDataProvider's
    get_latest_quote() returns exactly this shape, unchanged since
    v0.1.9, and it stays that way here (see LiveQuote below for why the
    HTTP response is a different, superset shape rather than this one
    growing new fields every provider would have to fill in).
    """

    symbol: str
    bid: float
    ask: float
    last: float | None = None
    timestamp: MarketTimestamp


class LiveQuote(BaseModel):
    """The normalized shape GET /market-data/{symbol}/quote actually
    returns to the frontend (v0.1.11) -- a flat superset of Quote, built
    by the route, not by a provider:

      - `price` is Quote.last (renamed for a frontend that has no
        reason to know this app's internal "last trade price" naming).
      - `volume` is best-effort and often None: neither Alpaca's nor
        Massive's latest-quote/latest-trade endpoints return cumulative
        daily volume (only a single trade's size) -- the route makes a
        second, best-effort get_historical_data() call (a trailing
        window, not strictly today -- see the route for why: a
        same-day-only query returns nothing on a weekend) to fill this
        in, and a failure there never fails the quote itself (see
        market_data.py's route for exactly which exceptions that
        covers and why).
      - `provider` is Quote.timestamp.source, promoted to a top-level
        field -- the frontend contract asked for it directly rather
        than nested.
      - `timestamp` is flattened to a plain datetime -- the frontend
        doesn't need MarketTimestamp's source field twice.
      - `bid`/`ask` are optional as of v0.1.15: every REST-derived
        LiveQuote (built from a real provider Quote, which always has
        both) still has them, but the streaming route's Massive
        polling fallback (app/streaming/massive_stream.py's
        MassivePollingQuoteStream, used when the account isn't
        entitled to Massive's real-time WebSocket) is built from an
        OHLCV bar, which has no bid/ask at all -- None there, never a
        fabricated number standing in for one, matching this app's
        "never invent a plausible-looking value" rule elsewhere (see
        e.g. app/ingestion/value_parsing.py).

    Deliberately NOT a change to Quote itself: Quote is what all three
    providers' get_latest_quote() are tested against (see
    tests/test_alpaca_provider.py etc.) -- adding volume/provider
    there would mean every provider either fabricates them or leaves
    them None with no HTTP-layer meaning attached. LiveQuote is purely
    an HTTP response shape assembled in app/api/market_data.py (and,
    as of v0.1.12/15, app/api/market_data_stream.py).
    """

    symbol: str
    price: float | None
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    timestamp: datetime
    provider: str


class HistoricalBarsResponse(BaseModel):
    """The full response body GET /market-data/{symbol}/history returns
    (v0.1.16) -- the bars themselves (HistoricalBar, above) plus enough
    of the echoed request to make the response self-describing (what was
    asked for, not just what came back) and a `bar_count` so the
    frontend doesn't need `bars.length` to know how many rows it got.
    """

    symbol: str
    provider: str
    timeframe: str
    start: date
    end: date
    bar_count: int
    bars: list[HistoricalBar]
