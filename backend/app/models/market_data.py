"""Normalized market-data models beyond a single options chain.

NormalizedOption (option_chain.py) already covers "one option contract,
right now" -- that's all the CSV path and today's calculator need, and
it stays exactly as-is; nothing here changes it or what depends on it.

These three models exist for provider capabilities nothing in the app
calls yet (MarketDataProvider.get_historical_data / get_latest_quote /
stream_quotes, see providers/base.py) -- a live provider (Alpaca, etc.)
will need them once it actually implements those methods. They are
additive: no existing code imports this module.
"""

from datetime import datetime

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

    Nothing in the app constructs one of these yet; it exists so a
    future provider's get_historical_data() has a normalized return
    type to target from day one, instead of each provider inventing
    its own bar shape that the (not-yet-built) backtesting phase would
    have to special-case per source.
    """

    symbol: str
    timestamp: MarketTimestamp
    open: float
    high: float
    low: float
    close: float
    volume: int


class Quote(BaseModel):
    """A live quote for an underlying (not an option) -- get_latest_quote's return type.

    Deliberately does NOT reuse NormalizedOption: that model's fields
    (strike, option_type, delta, ...) don't apply to the underlying
    itself, and stretching one model to cover both would mean either
    fake option fields on a stock quote or optional fields nobody can
    tell apart from "not fetched yet" vs. "doesn't apply here."
    """

    symbol: str
    bid: float
    ask: float
    last: float | None = None
    timestamp: MarketTimestamp
