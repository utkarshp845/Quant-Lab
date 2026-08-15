"""AlpacaProvider -- placeholder.

Structure and configuration only; no live API integration yet (see
README Phase 4 and the roadmap discussion around this file's
addition). get_chain() raises NotImplementedError rather than
returning empty/fake data, so a caller that mistakenly selects this
provider before it's built gets a clear error, not a silently wrong
answer.

Credentials: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY, read via
app.config (the app's one os.environ touchpoint) with a default of
None so missing credentials are explicit, not fabricated. Constructor
args override the env-var default, mainly so tests can pass fake
credentials without touching the environment.
"""

from app import config
from app.providers.base import MarketDataProvider, NormalizedChainResult


class AlpacaProvider(MarketDataProvider):
    name = "alpaca"

    def __init__(self, api_key_id: str | None = None, api_secret_key: str | None = None):
        self.api_key_id = api_key_id or config.get_provider_credential("alpaca", "api_key_id")
        self.api_secret_key = api_secret_key or config.get_provider_credential("alpaca", "api_secret_key")

    def get_chain(self, **kwargs) -> NormalizedChainResult:
        self._require_credentials()
        raise NotImplementedError(
            "AlpacaProvider is a placeholder -- live options-chain integration "
            "is not implemented yet. See README's Phase 4 roadmap entry."
        )

    def _require_credentials(self) -> None:
        if not self.api_key_id or not self.api_secret_key:
            raise RuntimeError(
                "AlpacaProvider requires ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY "
                "environment variables (or explicit constructor arguments)."
            )
