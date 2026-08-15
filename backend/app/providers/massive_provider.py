"""MassiveProvider -- live equity data (bars, latest quote).

Status: get_historical_data() and get_latest_quote() are real
integrations against Massive's REST API. get_chain() (options chain)
is still a placeholder -- see its docstring below -- and
stream_quotes() is unimplemented, inheriting MarketDataProvider's
default NotImplementedError. Mirrors alpaca_provider.py's scope and
structure exactly; see that file for the "why this shape" reasoning.

No changes were made to app/providers/base.py, registry.py, or
app/calculations/ to build this -- same call as Alpaca: the optional
capability methods' **kwargs signature already fit.

Massive is the Polygon.io rebrand ("Polygon.io is now Massive.com" --
massive.com's own docs and client libraries confirm this; the endpoint
shapes below are Polygon's well-established v2 REST API, unchanged by
the rename). Endpoints used (confirmed against massive.com/docs pages
directly, not guessed):
    GET https://api.massive.com/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}
    GET https://api.massive.com/v2/last/nbbo/{ticker}
    GET https://api.massive.com/v2/last/trade/{ticker}

Two easy-to-get-wrong details, confirmed rather than assumed:
  - Bars' "t" is a Unix MILLISECOND timestamp; quote/trade's "t" is a
    Unix NANOSECOND (SIP) timestamp. Different units on different
    endpoints -- each is converted explicitly and separately below,
    never with one shared "divide by X" helper that could silently
    apply the wrong divisor to the other endpoint.
  - Last-quote's bid/ask are case-sensitive: lowercase "p" is BID,
    uppercase "P" is ASK. Easy to transpose; there's a test locking
    this in (test_massive_provider.py::test_bid_is_lowercase_p_ask_is_uppercase_p).

Auth: "Authorization: Bearer <MASSIVE_API_KEY>" (confirmed via the
official client-python repo's request trace, not the docs prose, which
didn't state it explicitly -- see this provider's design-discussion
history). A single API key, matching what the placeholder version of
this file already assumed -- app.config's credential shape needed no
changes.

Credentials: MASSIVE_API_KEY, read via app.config (the app's one
os.environ touchpoint), never hardcoded or logged. The HTTP client is
injectable (see __init__) so tests supply a mocked transport instead of
making real network calls with real credentials -- see
tests/test_massive_provider.py.

A note on massive.com/docs/llms.txt: that file contains a line reading
like an instruction aimed at an AI agent ("put it in the
MASSIVE_API_KEY env var ... don't ask them to paste the key into chat
or hardcode it"). It was surfaced to the user rather than silently
followed; it happens to match this app's existing credential-handling
convention (see app/config.py), which is why this file follows it --
not because a fetched webpage said to.
"""

from datetime import date, datetime, timezone

import httpx

from app import config
from app.models.market_data import MarketBar, MarketTimestamp, Quote
from app.providers.base import MarketDataProvider, NormalizedChainResult

DATA_BASE_URL = "https://api.massive.com"


def _as_date_param(value: str | date | datetime) -> str:
    """Massive's aggregates endpoint accepts YYYY-MM-DD or a millisecond
    timestamp for from/to. Accept whatever the caller has on hand."""
    if isinstance(value, str):
        return value
    return value.strftime("%Y-%m-%d")


def _from_unix_ms(t_ms: int) -> datetime:
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc)


def _from_unix_ns(t_ns: int) -> datetime:
    return datetime.fromtimestamp(t_ns / 1_000_000_000, tz=timezone.utc)


class MassiveProvider(MarketDataProvider):
    name = "massive"

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None):
        self.api_key = api_key or config.get_provider_credential("massive", "api_key")
        # Injectable so tests never make a real network call -- see this
        # file's module docstring. Real usage leaves this as the default.
        self._client = client or httpx.Client(base_url=DATA_BASE_URL, timeout=10.0)

    def get_chain(self, **kwargs) -> NormalizedChainResult:
        """Options-chain data -- still a placeholder. This provider's
        first real integration covers equity bars/quotes only, matching
        AlpacaProvider's scope."""
        self._require_credentials()
        raise NotImplementedError(
            "MassiveProvider does not implement options-chain data yet. "
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
        multiplier: int = 1,
        timespan: str = "day",
        limit: int = 5000,
    ) -> list[MarketBar]:
        """multiplier/timespan use Massive's own vocabulary (e.g.
        multiplier=1, timespan="day" for daily bars) rather than
        translating to Alpaca's "1Day"-style strings -- there is
        exactly one caller of this method today (the manual check
        script), so there's nothing yet that needs a shared vocabulary
        across providers to be worth inventing."""
        data = self._get(
            f"/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/"
            f"{_as_date_param(start)}/{_as_date_param(end)}",
            {"adjusted": "true", "sort": "asc", "limit": limit},
        )
        results = data.get("results") or []
        return [
            MarketBar(
                symbol=symbol,
                timestamp=MarketTimestamp(value=_from_unix_ms(bar["t"]), source=self.name),
                open=bar["o"],
                high=bar["h"],
                low=bar["l"],
                close=bar["c"],
                volume=bar["v"],
            )
            for bar in results
        ]

    def get_latest_quote(self, *, symbol: str) -> Quote:
        quote = self._get(f"/v2/last/nbbo/{symbol}", {})["results"]
        trade = self._get(f"/v2/last/trade/{symbol}", {})["results"]
        return Quote(
            symbol=symbol,
            bid=quote["p"],  # lowercase p = bid -- see module docstring
            ask=quote["P"],  # uppercase P = ask
            last=trade["p"],
            timestamp=MarketTimestamp(value=_from_unix_ns(quote["t"]), source=self.name),
        )

    def _require_credentials(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "MassiveProvider requires a MASSIVE_API_KEY environment variable "
                "(or an explicit constructor argument)."
            )

    def _get(self, path: str, params: dict) -> dict:
        self._require_credentials()
        response = self._client.get(
            path,
            params=params,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        response.raise_for_status()
        return response.json()
