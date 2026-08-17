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
  - A bar's "v" (volume) can come back as a float with a fractional
    part (e.g. 27820813.411849), confirmed against a real account --
    not the clean integer Alpaca returns. Rounded, not truncated, when
    building MarketBar (see get_historical_data) -- truncation would
    systematically undercount every bar.

Confirmed against a real account: get_historical_data() works on
Massive's free plan; get_latest_quote() (both /v2/last/nbbo and
/v2/last/trade) returns 403 NOT_AUTHORIZED on the free plan --
real-time quotes need a paid plan. _get() turns that into a clear
RuntimeError naming which capability needs upgrading, rather than
letting a bare httpx.HTTPStatusError surface.

get_historical_data() paginates automatically as of v0.1.16 (see its
docstring): a response can include a `next_url` when more bars exist
beyond one request's `limit` -- exactly what an intraday timeframe over
a real date range needs.

v0.1.19: get_historical_data() also persists every raw page it
receives, via app.storage.raw_ingestion_repository, BEFORE building any
MarketBar from it -- see that module's docstring and
alpaca_provider.py's identical addition. Persisting failures are
swallowed there, never here.

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

import json
from datetime import date, datetime, timezone

import httpx

from app import config
from app.models.market_data import MarketBar, MarketTimestamp, Quote
from app.providers.base import MarketDataProvider, NormalizedChainResult
from app.storage.raw_ingestion_repository import persist_raw_ingestion_safely

DATA_BASE_URL = "https://api.massive.com"

# Safety cap on how many pages get_historical_data() will follow via
# next_url before giving up -- see alpaca_provider.py's _MAX_PAGES for
# the identical reasoning (an intraday timeframe over a wide date range
# can span many pages; this bounds a misbehaving/looping upstream to a
# bounded number of requests).
_MAX_PAGES = 50


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
        translating to Alpaca's "1Day"-style strings -- callers that need
        a shared vocabulary across providers (e.g. the historical-data
        route, app/api/historical_data.py) translate at that layer
        instead of this provider inventing one nobody else used to need.

        Paginates automatically (v0.1.16, same reasoning as
        alpaca_provider.py's get_historical_data): a response can include
        a `next_url` when more results exist beyond `limit` for one
        request -- an intraday timeframe over a real date range can span
        several such pages. Follows `next_url` as-is (it already carries
        the cursor and original query params) until it's absent, or
        `_MAX_PAGES` is hit."""
        path = (
            f"/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/"
            f"{_as_date_param(start)}/{_as_date_param(end)}"
        )
        params = {"adjusted": "true", "sort": "asc", "limit": limit}
        bars: list[MarketBar] = []
        raw_pages: list[dict] = []
        next_url: str | None = None
        for _ in range(_MAX_PAGES):
            # next_url is a fully-qualified URL (cursor + original query
            # params already baked in by Massive) -- pass it through with
            # params=None, not params={}. httpx.Client.get(url, params=X)
            # REPLACES url's existing query string with X whenever X is
            # not None -- even {} -- so passing {} here would silently
            # strip next_url's own ?cursor=... and re-request page 1
            # forever. Confirmed the hard way: see
            # tests/test_massive_provider.py::TestGetHistoricalDataPagination.
            data = self._get(next_url or path, None if next_url else params)
            raw_pages.append(data)  # v0.1.19: the untouched response, before any of the parsing below
            results = data.get("results") or []
            bars.extend(
                MarketBar(
                    symbol=symbol,
                    timestamp=MarketTimestamp(value=_from_unix_ms(bar["t"]), source=self.name),
                    open=bar["o"],
                    high=bar["h"],
                    low=bar["l"],
                    close=bar["c"],
                    # Confirmed against a real account: Massive's "v" can come
                    # back as a float with a fractional part (e.g.
                    # 27820813.411849), not the clean integer Alpaca returns.
                    # MarketBar.volume is (correctly) an int -- a share count
                    # is conceptually whole -- so this provider rounds rather
                    # than relaxing the shared model's type for one source's
                    # quirk. round() over int() specifically: truncating would
                    # systematically undercount every bar by up to ~1 share.
                    volume=round(bar["v"]),
                )
                for bar in results
            )
            next_url = data.get("next_url")
            if not next_url:
                persist_raw_ingestion_safely(
                    source=self.name,
                    symbol=symbol,
                    timeframe=timespan,
                    source_start=start,
                    source_end=end,
                    raw_payload=json.dumps(raw_pages),
                    content_type="json",
                    metadata={"page_count": len(raw_pages), "multiplier": multiplier},
                )
                return bars
        raise RuntimeError(
            f"MassiveProvider: get_historical_data({symbol!r}) did not finish paginating after "
            f"{_MAX_PAGES} pages -- aborting rather than looping indefinitely. Narrow the date "
            "range or timeframe, or raise _MAX_PAGES if this range is genuinely expected to be this large."
        )

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

    def _get(self, path: str, params: dict | None) -> dict:
        """`params=None` (as opposed to `{}`) means "don't touch this
        URL's existing query string" -- see get_historical_data's
        next_url handling for why that distinction matters here."""
        self._require_credentials()
        response = self._client.get(
            path,
            params=params,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        if response.status_code == 403:
            # Confirmed against a real account: historical bars
            # (/v2/aggs/...) work on the free plan, but /v2/last/nbbo
            # and /v2/last/trade come back 403 with a
            # {"status": "NOT_AUTHORIZED", "message": "..."} body --
            # a plan-entitlement gap, not a bug in this provider. Raise
            # something a caller can actually act on instead of a bare
            # httpx.HTTPStatusError.
            try:
                detail = response.json().get("message")
            except ValueError:
                detail = None
            message = f"MassiveProvider: not entitled to {path} on your current Massive plan"
            if detail:
                message += f" -- {detail}"
            message += ". Historical bars (get_historical_data) work on the free plan; " "real-time quotes (get_latest_quote) do not."
            raise RuntimeError(message) from None
        response.raise_for_status()
        return response.json()
