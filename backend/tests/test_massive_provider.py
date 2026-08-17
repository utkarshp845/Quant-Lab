"""Tests for MassiveProvider's real equity-data integration (bars, latest quote).

Everything here uses httpx.MockTransport -- no real network call, no
real credentials, ever. Response fixtures are shaped exactly like
Massive's published API reference for /v2/aggs/ticker/.../range/...,
/v2/last/nbbo/{ticker}, and /v2/last/trade/{ticker} (see
massive_provider.py's module docstring for the confirmed shapes).

Covers TSLA and NVDA specifically, per the requested scope, plus the
two details most likely to silently break if this provider is ever
touched again: bars use millisecond timestamps, quotes/trades use
nanosecond timestamps, and bid ("p") vs. ask ("P") is case-sensitive.

v0.1.19: get_historical_data() now also persists a raw-ingestion row
(see app/storage/raw_ingestion_repository.py) -- isolated_db below
points DATABASE_PATH at a throwaway tmp_path file for every test here,
same as tests/test_historical_storage_api.py, so this file never
writes into a developer's real backend/data/historical_bars.db.
"""

from datetime import date, datetime, timezone

import httpx
import pytest

from app.models.market_data import MarketBar, Quote
from app.providers.massive_provider import MassiveProvider


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_massive_provider.db"))


def _bars_response(ticker: str, bars: list[dict]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"ticker": ticker, "queryCount": len(bars), "resultsCount": len(bars), "adjusted": True, "results": bars, "status": "OK"},
    )


def _quote_response(bid: float, ask: float, t_ns: int) -> httpx.Response:
    return httpx.Response(200, json={"results": {"P": ask, "S": 2, "p": bid, "s": 1, "t": t_ns}, "status": "OK"})


def _trade_response(price: float, t_ns: int) -> httpx.Response:
    return httpx.Response(200, json={"results": {"p": price, "s": 100, "t": t_ns}, "status": "OK"})


TSLA_BARS_MS = [
    {"t": 1786334400000, "o": 245.10, "h": 248.75, "l": 243.20, "c": 247.55, "v": 98_400_000, "vw": 246.1, "n": 500_000},
    {"t": 1786420800000, "o": 247.60, "h": 250.00, "l": 246.00, "c": 249.30, "v": 87_200_000, "vw": 248.2, "n": 480_000},
]

NVDA_BARS_MS = [
    {"t": 1786334400000, "o": 118.40, "h": 120.10, "l": 117.90, "c": 119.85, "v": 210_000_000, "vw": 119.0, "n": 700_000},
]


def _make_provider(handler) -> MassiveProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(base_url="https://api.massive.com", transport=transport)
    return MassiveProvider(api_key="fake_key", client=client)


class TestCredentialsRequiredBeforeAnyRequest:
    def test_missing_credentials_raises_before_touching_the_network(self, monkeypatch):
        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no HTTP request should be made without credentials")

        transport = httpx.MockTransport(handler)
        client = httpx.Client(base_url="https://api.massive.com", transport=transport)
        provider = MassiveProvider(client=client)

        with pytest.raises(RuntimeError, match="MASSIVE_API_KEY"):
            provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 12))

    def test_bearer_header_is_sent(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer fake_key"
            return _bars_response("TSLA", [])

        provider = _make_provider(handler)
        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))


class TestGetHistoricalData:
    def test_tsla_bars_are_parsed_into_market_bars(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v2/aggs/ticker/TSLA/range/1/day/2026-08-10/2026-08-11"
            return _bars_response("TSLA", TSLA_BARS_MS)

        provider = _make_provider(handler)
        bars = provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 10), end=date(2026, 8, 11))

        assert len(bars) == 2
        assert all(isinstance(b, MarketBar) for b in bars)
        assert bars[0].symbol == "TSLA"
        assert bars[0].open == 245.10
        assert bars[0].high == 248.75
        assert bars[0].low == 243.20
        assert bars[0].close == 247.55
        assert bars[0].volume == 98_400_000
        assert bars[0].timestamp.source == "massive"
        assert bars[1].close == 249.30

    def test_nvda_bars_are_parsed_into_market_bars(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v2/aggs/ticker/NVDA/range/1/day/2026-08-10/2026-08-10"
            return _bars_response("NVDA", NVDA_BARS_MS)

        provider = _make_provider(handler)
        bars = provider.get_historical_data(symbol="NVDA", start=date(2026, 8, 10), end=date(2026, 8, 10))

        assert len(bars) == 1
        assert bars[0].symbol == "NVDA"
        assert bars[0].close == 119.85

    def test_bar_timestamp_is_interpreted_as_milliseconds_not_nanoseconds(self):
        """1786334400000 ms -> 2026-08-10T04:00:00 UTC. If this were
        misread as nanoseconds the resulting year would be ~1970's
        neighborhood-of-1976, not 2026 -- a wildly wrong date is exactly
        the kind of silent bug a unit mismatch produces."""

        def handler(request: httpx.Request) -> httpx.Response:
            return _bars_response("TSLA", [TSLA_BARS_MS[0]])

        provider = _make_provider(handler)
        bars = provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 10), end=date(2026, 8, 10))

        assert bars[0].timestamp.value == datetime(2026, 8, 10, 4, 0, 0, tzinfo=timezone.utc)

    def test_default_timespan_is_daily(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/range/1/day/" in str(request.url)
            return _bars_response("TSLA", [])

        provider = _make_provider(handler)
        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))

    def test_multiplier_and_timespan_can_be_overridden(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/range/5/minute/" in str(request.url)
            return _bars_response("TSLA", [])

        provider = _make_provider(handler)
        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1), multiplier=5, timespan="minute")

    def test_fractional_volume_is_rounded_to_the_nearest_share(self):
        """Confirmed against a real account, not hypothetical: Massive's
        "v" can come back as a float (e.g. 27820813.411849), which the
        MarketBar model correctly rejects as a raw int (a share count is
        conceptually whole). This was an actual ValidationError against
        real TSLA data before being fixed -- see massive_provider.py's
        get_historical_data for why round() and not int()/truncation."""

        def handler(request: httpx.Request) -> httpx.Response:
            bar = dict(TSLA_BARS_MS[0], v=27_820_813.411849)
            return _bars_response("TSLA", [bar])

        provider = _make_provider(handler)
        bars = provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 10), end=date(2026, 8, 10))

        assert bars[0].volume == 27_820_813
        assert isinstance(bars[0].volume, int)

    def test_empty_results_returns_an_empty_list_not_an_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _bars_response("TSLA", [])

        provider = _make_provider(handler)
        assert provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1)) == []

    def test_missing_results_key_returns_an_empty_list_not_a_crash(self):
        """Massive/Polygon can omit "results" entirely rather than
        returning an empty list when there's no data for the range."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ticker": "TSLA", "resultsCount": 0, "status": "OK"})

        provider = _make_provider(handler)
        assert provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1)) == []

    def test_http_error_propagates_instead_of_being_swallowed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"status": "ERROR", "error": "Unknown API Key"})

        provider = _make_provider(handler)
        with pytest.raises(httpx.HTTPStatusError):
            provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))


class TestGetHistoricalDataPagination:
    """v0.1.16: get_historical_data() follows next_url until it's absent,
    rather than silently returning only the first page -- an intraday
    timeframe over a real date range can span several pages."""

    def test_follows_next_url_until_exhausted(self):
        bar_1 = dict(TSLA_BARS_MS[0], v=100)
        bar_2 = dict(TSLA_BARS_MS[1], v=200)
        bar_3 = {"t": 1786507200000, "o": 250.0, "h": 252.0, "l": 249.0, "c": 251.0, "v": 300}
        next_url_1 = "https://api.massive.com/v2/aggs/ticker/TSLA/range/1/day/2026-08-10/2026-08-12?cursor=page2"
        next_url_2 = "https://api.massive.com/v2/aggs/ticker/TSLA/range/1/day/2026-08-10/2026-08-12?cursor=page3"
        responses = {
            "/v2/aggs/ticker/TSLA/range/1/day/2026-08-10/2026-08-12": httpx.Response(
                200, json={"results": [bar_1], "next_url": next_url_1, "status": "OK"}
            ),
            next_url_1: httpx.Response(200, json={"results": [bar_2], "next_url": next_url_2, "status": "OK"}),
            next_url_2: httpx.Response(200, json={"results": [bar_3], "status": "OK"}),
        }
        seen_urls = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            seen_urls.append(url)
            key = url if url in responses else request.url.path
            return responses[key]

        provider = _make_provider(handler)
        bars = provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 10), end=date(2026, 8, 12))

        assert [b.volume for b in bars] == [100, 200, 300]
        assert seen_urls == [
            "https://api.massive.com/v2/aggs/ticker/TSLA/range/1/day/2026-08-10/2026-08-12?adjusted=true&sort=asc&limit=5000",
            next_url_1,
            next_url_2,
        ]

    def test_a_single_page_response_makes_exactly_one_request(self):
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return _bars_response("TSLA", TSLA_BARS_MS)

        provider = _make_provider(handler)
        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 10), end=date(2026, 8, 11))

        assert request_count == 1

    def test_exceeding_max_pages_raises_instead_of_looping_forever(self):
        def handler(request: httpx.Request) -> httpx.Response:
            # Always claims there's another page -- simulates a
            # misbehaving upstream that never actually exhausts.
            return httpx.Response(
                200,
                json={"results": [], "next_url": "https://api.massive.com/v2/aggs/ticker/TSLA/x?cursor=loop", "status": "OK"},
            )

        provider = _make_provider(handler)
        with pytest.raises(RuntimeError, match="did not finish paginating"):
            provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))


class TestPlanEntitlement403:
    """Confirmed against a real account: bars work on the free plan,
    but /v2/last/nbbo and /v2/last/trade return 403 NOT_AUTHORIZED on
    it. These lock in that a 403 specifically gets turned into a clear,
    actionable RuntimeError -- not that it's swallowed or hidden."""

    def test_403_on_latest_quote_raises_a_clear_runtime_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={
                    "status": "NOT_AUTHORIZED",
                    "request_id": "abc123",
                    "message": "You are not entitled to this data. Please upgrade your plan at https://massive.com/pricing",
                },
            )

        provider = _make_provider(handler)
        with pytest.raises(RuntimeError, match="not entitled") as exc_info:
            provider.get_latest_quote(symbol="TSLA")

        # The API's own message is surfaced, not swallowed or reworded away.
        assert "upgrade your plan" in str(exc_info.value)
        assert "get_latest_quote" in str(exc_info.value) or "real-time quotes" in str(exc_info.value)

    def test_403_with_non_json_body_still_raises_a_clear_error(self):
        """The friendly-message extraction must not itself crash if the
        403 body isn't JSON (e.g. an upstream proxy error page)."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="<html>Forbidden</html>")

        provider = _make_provider(handler)
        with pytest.raises(RuntimeError, match="not entitled"):
            provider.get_latest_quote(symbol="TSLA")

    def test_non_403_errors_still_raise_the_raw_http_error(self):
        """Only 403 gets the friendly treatment -- other status codes
        (401 bad key, 500 upstream failure, ...) still surface as a
        plain httpx.HTTPStatusError, unchanged from before this fix."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"status": "ERROR", "message": "Unknown API Key"})

        provider = _make_provider(handler)
        with pytest.raises(httpx.HTTPStatusError):
            provider.get_latest_quote(symbol="TSLA")


class TestGetLatestQuote:
    def test_bid_is_lowercase_p_ask_is_uppercase_p(self):
        """Locks in the case-sensitive field mapping documented in
        massive_provider.py -- a transposed p/P would silently swap
        bid and ask with no error, so this is checked explicitly with
        deliberately distinct values."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "/last/nbbo/" in str(request.url):
                return _quote_response(bid=247.50, ask=247.55, t_ns=1786478400000000000)
            return _trade_response(price=247.52, t_ns=1786478400000000000)

        provider = _make_provider(handler)
        quote = provider.get_latest_quote(symbol="TSLA")

        assert isinstance(quote, Quote)
        assert quote.bid == 247.50
        assert quote.ask == 247.55
        assert quote.bid < quote.ask

    def test_tsla_quote_combines_bid_ask_and_last_trade(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "/last/nbbo/" in str(request.url):
                return _quote_response(bid=247.50, ask=247.55, t_ns=1786478400000000000)
            return _trade_response(price=247.52, t_ns=1786478400000000000)

        provider = _make_provider(handler)
        quote = provider.get_latest_quote(symbol="TSLA")

        assert quote.symbol == "TSLA"
        assert quote.last == 247.52
        assert quote.timestamp.source == "massive"

    def test_nvda_quote_combines_bid_ask_and_last_trade(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "/last/nbbo/" in str(request.url):
                return _quote_response(bid=119.80, ask=119.85, t_ns=1786478400000000000)
            return _trade_response(price=119.83, t_ns=1786478400000000000)

        provider = _make_provider(handler)
        quote = provider.get_latest_quote(symbol="NVDA")

        assert quote.symbol == "NVDA"
        assert quote.bid == 119.80
        assert quote.last == 119.83

    def test_quote_timestamp_is_interpreted_as_nanoseconds_not_milliseconds(self):
        """1786478400000000000 ns -> 2026-08-11T20:00:00 UTC."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "/last/nbbo/" in str(request.url):
                return _quote_response(bid=1.0, ask=1.1, t_ns=1786478400000000000)
            return _trade_response(price=1.05, t_ns=1786478400000000000)

        provider = _make_provider(handler)
        quote = provider.get_latest_quote(symbol="TSLA")

        assert quote.timestamp.value == datetime(2026, 8, 11, 20, 0, 0, tzinfo=timezone.utc)

    def test_missing_credentials_raises_before_touching_the_network(self, monkeypatch):
        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no HTTP request should be made without credentials")

        transport = httpx.MockTransport(handler)
        client = httpx.Client(base_url="https://api.massive.com", transport=transport)
        provider = MassiveProvider(client=client)

        with pytest.raises(RuntimeError, match="MASSIVE_API_KEY"):
            provider.get_latest_quote(symbol="TSLA")


class TestOptionsChainStillPlaceholder:
    """Confirms this change's scope: equity data is real, options data
    is not, and it says so rather than silently returning nothing."""

    def test_get_chain_still_raises_not_implemented(self):
        provider = MassiveProvider(api_key="fake")
        with pytest.raises(NotImplementedError, match="options-chain"):
            provider.get_chain()
