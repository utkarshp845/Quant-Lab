"""AlpacaProvider -- live equity data (bars, latest quote).

Status: get_historical_data() and get_latest_quote() are real
integrations against Alpaca's Market Data API v2. get_chain()
(options chain) is still a placeholder -- see its docstring below --
and stream_quotes() is unimplemented, inheriting MarketDataProvider's
default NotImplementedError.

No changes were made to app/providers/base.py, registry.py, or
anything in app/calculations/ to build this: the optional capability
methods' loose `**kwargs` signature (see base.py) already accommodated
symbol/start/end/timeframe-style arguments without modification, which
is the "genuine interface deficiency, or don't touch it" call made
before writing this file.

Endpoints used (confirmed against Alpaca's published API reference,
not guessed):
    GET https://data.alpaca.markets/v2/stocks/{symbol}/bars
    GET https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest
    GET https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest

Feed defaults to "iex" (the free tier's real-time feed) rather than
Alpaca's own default of "sip" (requires the paid Algo Trader Plus
plan) -- so this works against a free account out of the box. Pass
feed="sip" explicitly once/if that plan is active.

Credentials: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY, read via
app.config (the app's one os.environ touchpoint), never hardcoded or
logged. The HTTP client is injectable (see __init__) specifically so
tests can supply a mocked transport instead of making real network
calls with real credentials -- see tests/test_alpaca_provider.py.
"""

from datetime import date, datetime

import httpx

from app import config
from app.models.market_data import MarketBar, MarketTimestamp, Quote
from app.providers.base import MarketDataProvider, NormalizedChainResult

DATA_BASE_URL = "https://data.alpaca.markets/v2"
DEFAULT_FEED = "iex"  # the free tier; "sip" needs a paid subscription


def _as_date_param(value: str | date | datetime) -> str:
    """Alpaca accepts RFC-3339 or plain YYYY-MM-DD for start/end. Accept
    whatever the caller has on hand (a date, a datetime, or an
    already-formatted string) rather than forcing one type."""
    if isinstance(value, str):
        return value
    return value.strftime("%Y-%m-%d")


class AlpacaProvider(MarketDataProvider):
    name = "alpaca"

    def __init__(
        self,
        api_key_id: str | None = None,
        api_secret_key: str | None = None,
        client: httpx.Client | None = None,
    ):
        self.api_key_id = api_key_id or config.get_provider_credential("alpaca", "api_key_id")
        self.api_secret_key = api_secret_key or config.get_provider_credential("alpaca", "api_secret_key")
        # Injectable so tests never make a real network call -- see this
        # file's module docstring. Real usage leaves this as the default.
        self._client = client or httpx.Client(base_url=DATA_BASE_URL, timeout=10.0)

    def get_chain(self, **kwargs) -> NormalizedChainResult:
        """Options-chain data -- still a placeholder. This provider's
        first real integration (this change) covers equity bars/quotes
        only, per the scope agreed before writing this file."""
        self._require_credentials()
        raise NotImplementedError(
            "AlpacaProvider does not implement options-chain data yet. "
            "get_historical_data() and get_latest_quote() (equity bars/quotes) "
            "are implemented; get_chain() is still a placeholder. "
            "See README's Phase 4 roadmap entry."
        )

    def get_historical_data(
        self,
        *,
        symbol: str,
        start: str | date | datetime,
        end: str | date | datetime,
        timeframe: str = "1Day",
        feed: str = DEFAULT_FEED,
        limit: int = 1000,
    ) -> list[MarketBar]:
        data = self._get(
            f"/stocks/{symbol}/bars",
            {
                "timeframe": timeframe,
                "start": _as_date_param(start),
                "end": _as_date_param(end),
                "feed": feed,
                "limit": limit,
            },
        )
        bars = data.get("bars") or []
        return [
            MarketBar(
                symbol=symbol,
                timestamp=MarketTimestamp(value=bar["t"], source=self.name),
                open=bar["o"],
                high=bar["h"],
                low=bar["l"],
                close=bar["c"],
                volume=bar["v"],
            )
            for bar in bars
        ]

    def get_latest_quote(self, *, symbol: str, feed: str = DEFAULT_FEED) -> Quote:
        quote = self._get(f"/stocks/{symbol}/quotes/latest", {"feed": feed})["quote"]
        trade = self._get(f"/stocks/{symbol}/trades/latest", {"feed": feed})["trade"]
        return Quote(
            symbol=symbol,
            bid=quote["bp"],
            ask=quote["ap"],
            last=trade["p"],
            timestamp=MarketTimestamp(value=quote["t"], source=self.name),
        )

    def _require_credentials(self) -> None:
        if not self.api_key_id or not self.api_secret_key:
            raise RuntimeError(
                "AlpacaProvider requires ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY "
                "environment variables (or explicit constructor arguments)."
            )

    def _get(self, path: str, params: dict) -> dict:
        self._require_credentials()
        response = self._client.get(
            path,
            params=params,
            headers={
                "APCA-API-KEY-ID": self.api_key_id,
                "APCA-API-SECRET-KEY": self.api_secret_key,
            },
        )
        response.raise_for_status()
        return response.json()
