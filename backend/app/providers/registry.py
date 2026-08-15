"""The provider registry -- where new MarketDataProviders get plugged in.

Today there's exactly one entry. When a live provider is added (see the
README's Phase 4 roadmap), it gets a new file next to csv_provider.py
and one new line here -- nothing in app/providers/base.py, the API
routes, or anything downstream of NormalizedChainResult needs to
change. That's the whole point of the interface in base.py.
"""

from app.providers.base import MarketDataProvider
from app.providers.csv_provider import CSVProvider

PROVIDERS: dict[str, type[MarketDataProvider]] = {
    "csv": CSVProvider,
}


def get_provider(name: str) -> MarketDataProvider:
    try:
        provider_cls = PROVIDERS[name]
    except KeyError:
        raise ValueError(
            f"Unknown market data provider: {name!r}. Available: {sorted(PROVIDERS)}"
        ) from None
    return provider_cls()
