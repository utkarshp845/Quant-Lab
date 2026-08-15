"""MassiveProvider -- placeholder.

Same status as alpaca_provider.py: structure and configuration only,
no live integration yet. See that file's docstring for the reasoning
(NotImplementedError over fake data, credentials via app.config).

Credentials: MASSIVE_API_KEY, read via app.config. Massive's actual
auth scheme (single key vs. key+secret) hasn't been confirmed yet --
this is a best guess based on common REST API conventions, to be
corrected once real integration work starts.
"""

from app import config
from app.providers.base import MarketDataProvider, NormalizedChainResult


class MassiveProvider(MarketDataProvider):
    name = "massive"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or config.get_provider_credential("massive", "api_key")

    def get_chain(self, **kwargs) -> NormalizedChainResult:
        self._require_credentials()
        raise NotImplementedError(
            "MassiveProvider is a placeholder -- live options-chain integration "
            "is not implemented yet. See README's Phase 4 roadmap entry."
        )

    def _require_credentials(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "MassiveProvider requires a MASSIVE_API_KEY environment variable "
                "(or an explicit constructor argument)."
            )
