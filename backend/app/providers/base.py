"""The MarketDataProvider interface.

Every source of options data -- a CSV upload today, a live broker API
later (see the README's Phase 4 roadmap) -- implements this interface
and returns the exact same shape: a list of NormalizedOption contracts
(app/models/option_chain.py) plus a little metadata about the fetch.
Nothing downstream of a provider (the calculator, the scanner, a future
signal-generation phase) ever needs to know which one produced its data.

Providers are allowed to differ wildly in HOW they get data -- a CSV
upload is a push (the caller hands over a whole file at once); a live
broker API is a pull keyed by symbol/expiration. That's why
get_chain() takes **kwargs instead of a fixed signature: the INPUT is
provider-specific on purpose. What must never differ is the OUTPUT --
always a NormalizedChainResult, never a provider-specific shape leaking
downstream. That single invariant is the entire point of this file.
"""

from abc import ABC, abstractmethod
from typing import Iterator

from pydantic import BaseModel

from app.models.market_data import MarketBar, Quote
from app.models.option_chain import CsvRowError, NormalizedOption


class NormalizedChainResult(BaseModel):
    """What every provider hands back, regardless of source.

    This is deliberately the same information CsvImportResponse already
    carried in v0.1.1 (see app/models/option_chain.py) -- that response
    model was already provider-shaped before there was a provider
    interface to name it. This class gives that shape a name that
    doesn't imply "came from a CSV," plus a `source` and `metadata`
    field so a provider can identify itself and attach source-specific
    extras (e.g. CSVProvider's detected column mapping) without those
    extras leaking into the fields every provider shares.
    """

    contracts: list[NormalizedOption]
    symbols: list[str]
    expirations_by_symbol: dict[str, list[str]]
    row_errors: list[CsvRowError]
    total_rows: int
    imported_rows: int
    source: str  # provider name, e.g. "csv" -- lets the UI/logs say where data came from
    metadata: dict = {}  # provider-specific extras (CSV: detected_columns/column_mapping)


class MarketDataProvider(ABC):
    """One source of market data, normalized to this app's internal models.

    Implementations: CSVProvider (providers/csv_provider.py) today,
    plus placeholder AlpacaProvider/MassiveProvider/SchwabProvider
    (providers/*_provider.py) awaiting real API integration. A new
    provider is registered in providers/registry.py, without changing
    anything in this file or anything downstream of these methods'
    return values.

    get_chain() is the only REQUIRED method -- it's the only one the
    app actually calls today (CSV import, the scanner). The other
    three are OPTIONAL capabilities: a provider that can't support one
    (CSV has no time series to page through, no live feed to stream)
    raises NotImplementedError with a message saying so, rather than
    faking an empty/static result that would silently mislead a
    caller into thinking it asked for something and got a real answer.
    Forcing every provider to implement all four would mean CSVProvider
    pretending to support historical data and streaming it fundamentally
    can't -- worse than being honest that it can't.
    """

    name: str

    @abstractmethod
    def get_chain(self, **kwargs) -> NormalizedChainResult:
        """Fetch and normalize an options chain. Required.

        Kwargs are provider-specific (CSVProvider takes csv_text=...;
        a future live provider would take symbol=..., expiration=...)
        -- the return type never is.
        """
        ...

    def get_historical_data(self, **kwargs) -> list[MarketBar]:
        """Fetch historical OHLCV bars for an underlying. Optional --
        no current provider needs to implement this; it exists for a
        future backtesting phase (see README Phase 6/7)."""
        raise NotImplementedError(f"{self.name} does not support historical data.")

    def get_latest_quote(self, **kwargs) -> Quote:
        """Fetch a live quote for an underlying (not an option). Optional."""
        raise NotImplementedError(f"{self.name} does not support live quotes.")

    def stream_quotes(self, **kwargs) -> Iterator[Quote]:
        """Stream live quotes for an underlying. Optional -- meaningful
        only for a provider with a real-time feed; CSV and, initially,
        every placeholder provider will raise this."""
        raise NotImplementedError(f"{self.name} does not support streaming quotes.")
