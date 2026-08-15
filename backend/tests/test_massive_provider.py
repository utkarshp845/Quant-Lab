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
"""

from datetime import date, datetime, timezone

import httpx
import pytest

from app.models.market_data import MarketBar, Quote
from app.providers.massive_provider import MassiveProvider


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
