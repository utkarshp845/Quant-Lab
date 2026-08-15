"""The provider registry -- where MarketDataProviders get plugged in.

Adding a new provider means one new file next to csv_provider.py and
one new line in PROVIDERS below -- nothing in app/providers/base.py,
the API routes, or anything downstream of a provider's return values
needs to change. That's the whole point of the interface in base.py.
"""

from app import config
from app.providers.alpaca_provider import AlpacaProvider
from app.providers.base import MarketDataProvider
from app.providers.csv_provider import CSVProvider
from app.providers.massive_provider import MassiveProvider
from app.providers.schwab_provider import SchwabProvider

PROVIDERS: dict[str, type[MarketDataProvider]] = {
    "csv": CSVProvider,
    "alpaca": AlpacaProvider,
    "massive": MassiveProvider,
    "schwab": SchwabProvider,
}


def get_provider(name: str) -> MarketDataProvider:
    """Explicitly resolve a provider by name. Use this when the caller
    knows exactly which provider it needs (e.g. the CSV upload route
    always wants "csv", regardless of MARKET_DATA_PROVIDER)."""
    try:
        provider_cls = PROVIDERS[name]
    except KeyError:
        raise ValueError(
            f"Unknown market data provider: {name!r}. Available: {sorted(PROVIDERS)}"
        ) from None
    return provider_cls()


def get_default_provider() -> MarketDataProvider:
    """Resolve whichever provider MARKET_DATA_PROVIDER (see app.config)
    selects, defaulting to "csv" if unset. Use this for any future
    entry point that should be configurable rather than hardcoded to
    one source -- e.g. a live-data route the calculator or scanner
    eventually calls without caring which provider backs it."""
    return get_provider(config.get_configured_provider_name())
