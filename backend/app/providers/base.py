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

from pydantic import BaseModel

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
    """One source of options-chain data, normalized to NormalizedOption.

    Implementations: CSVProvider (providers/csv_provider.py) today.
    A future live provider (e.g. a SchwabProvider) would live alongside
    it, registered in providers/registry.py, without changing anything
    in this file or anything downstream of get_chain()'s return value.
    """

    name: str

    @abstractmethod
    def get_chain(self, **kwargs) -> NormalizedChainResult:
        """Fetch and normalize a chain.

        Kwargs are provider-specific (CSVProvider takes csv_text=...;
        a future live provider would take symbol=..., expiration=...)
        -- the return type never is.
        """
        ...
