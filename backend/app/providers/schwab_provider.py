"""SchwabProvider -- placeholder.

Same status as alpaca_provider.py: structure and configuration only,
no live integration yet. See that file's docstring for the reasoning
(NotImplementedError over fake data, credentials via app.config).

Credentials: SCHWAB_CLIENT_ID / SCHWAB_CLIENT_SECRET, read via
app.config. Schwab's actual auth flow is OAuth2 (authorization-code,
with a refresh token), not a static key pair -- a real implementation
will need a token-refresh step this placeholder does not attempt to
model yet; client ID/secret are the minimum needed to even start that
flow.
"""

from app import config
from app.providers.base import MarketDataProvider, NormalizedChainResult


class SchwabProvider(MarketDataProvider):
    name = "schwab"

    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self.client_id = client_id or config.get_provider_credential("schwab", "client_id")
        self.client_secret = client_secret or config.get_provider_credential("schwab", "client_secret")

    def get_chain(self, **kwargs) -> NormalizedChainResult:
        self._require_credentials()
        raise NotImplementedError(
            "SchwabProvider is a placeholder -- live options-chain integration "
            "is not implemented yet. See README's Phase 4 roadmap entry."
        )

    def _require_credentials(self) -> None:
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "SchwabProvider requires SCHWAB_CLIENT_ID and SCHWAB_CLIENT_SECRET "
                "environment variables (or explicit constructor arguments)."
            )
