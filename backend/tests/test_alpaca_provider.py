"""Tests for AlpacaProvider's real equity-data integration (bars, latest quote).

Everything here uses httpx.MockTransport -- no real network call, no
real credentials, ever. Response fixtures are shaped exactly like
Alpaca's published API reference for /v2/stocks/{symbol}/bars,
/v2/stocks/{symbol}/quotes/latest, and /v2/stocks/{symbol}/trades/latest
(see alpaca_provider.py's module docstring for the confirmed shapes).

Covers TSLA and NVDA specifically, per the requested scope, plus the
credentials-required and HTTP-error paths that apply to any symbol.
"""

from datetime import date

import httpx
import pytest

from app.models.market_data import MarketBar, Quote
from app.providers.alpaca_provider import AlpacaProvider


def _bars_response(symbol: str, bars: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"symbol": symbol, "bars": bars, "next_page_token": None, "currency": "USD"})


def _quote_response(symbol: str, bp: float, ap: float, t: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"symbol": symbol, "quote": {"t": t, "ax": "V", "ap": ap, "as": 2, "bx": "V", "bp": bp, "bs": 1, "c": ["R"]}},
    )


def _trade_response(symbol: str, price: float, t: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"symbol": symbol, "trade": {"t": t, "x": "V", "p": price, "s": 100, "c": [" "], "i": 1, "z": "B"}},
    )


TSLA_BARS = [
    {"t": "2026-08-10T04:00:00Z", "o": 245.10, "h": 248.75, "l": 243.20, "c": 247.55, "v": 98_400_000, "n": 500_000, "vw": 246.1},
    {"t": "2026-08-11T04:00:00Z", "o": 247.60, "h": 250.00, "l": 246.00, "c": 249.30, "v": 87_200_000, "n": 480_000, "vw": 248.2},
]

NVDA_BARS = [
    {"t": "2026-08-10T04:00:00Z", "o": 118.40, "h": 120.10, "l": 117.90, "c": 119.85, "v": 210_000_000, "n": 700_000, "vw": 119.0},
]


def _make_provider(handler) -> AlpacaProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(base_url="https://data.alpaca.markets/v2", transport=transport)
    return AlpacaProvider(api_key_id="fake_key", api_secret_key="fake_secret", client=client)


class TestCredentialsRequiredBeforeAnyRequest:
    def test_missing_credentials_raises_before_touching_the_network(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
        monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no HTTP request should be made without credentials")

        transport = httpx.MockTransport(handler)
        client = httpx.Client(base_url="https://data.alpaca.markets/v2", transport=transport)
        provider = AlpacaProvider(client=client)

        with pytest.raises(RuntimeError, match="ALPACA_API_KEY_ID"):
            provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 12))


class TestGetHistoricalData:
    def test_tsla_bars_are_parsed_into_market_bars(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v2/stocks/TSLA/bars"
            return _bars_response("TSLA", TSLA_BARS)

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
        assert bars[0].timestamp.source == "alpaca"
        assert bars[1].close == 249.30

    def test_nvda_bars_are_parsed_into_market_bars(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v2/stocks/NVDA/bars"
            return _bars_response("NVDA", NVDA_BARS)

        provider = _make_provider(handler)
        bars = provider.get_historical_data(symbol="NVDA", start=date(2026, 8, 10), end=date(2026, 8, 10))

        assert len(bars) == 1
        assert bars[0].symbol == "NVDA"
        assert bars[0].close == 119.85

    def test_defaults_to_the_free_iex_feed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["feed"] == "iex"
            return _bars_response("TSLA", [])

        provider = _make_provider(handler)
        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 12))

    def test_feed_can_be_overridden_explicitly(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["feed"] == "sip"
            return _bars_response("TSLA", [])

        provider = _make_provider(handler)
        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 12), feed="sip")

    def test_dates_are_sent_in_yyyy_mm_dd_form(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["start"] == "2026-08-01"
            assert request.url.params["end"] == "2026-08-12"
            return _bars_response("TSLA", [])

        provider = _make_provider(handler)
        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 12))

    def test_empty_bars_returns_an_empty_list_not_an_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _bars_response("TSLA", [])

        provider = _make_provider(handler)
        assert provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1)) == []

    def test_http_error_propagates_instead_of_being_swallowed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "unauthorized"})

        provider = _make_provider(handler)
        with pytest.raises(httpx.HTTPStatusError):
            provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))


class TestGetLatestQuote:
    def test_tsla_quote_combines_bid_ask_and_last_trade(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/quotes/latest"):
                return _quote_response("TSLA", bp=247.50, ap=247.55, t="2026-08-14T20:00:00Z")
            return _trade_response("TSLA", price=247.52, t="2026-08-14T20:00:00Z")

        provider = _make_provider(handler)
        quote = provider.get_latest_quote(symbol="TSLA")

        assert isinstance(quote, Quote)
        assert quote.symbol == "TSLA"
        assert quote.bid == 247.50
        assert quote.ask == 247.55
        assert quote.last == 247.52
        assert quote.timestamp.source == "alpaca"

    def test_nvda_quote_combines_bid_ask_and_last_trade(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/quotes/latest"):
                return _quote_response("NVDA", bp=119.80, ap=119.85, t="2026-08-14T20:00:00Z")
            return _trade_response("NVDA", price=119.83, t="2026-08-14T20:00:00Z")

        provider = _make_provider(handler)
        quote = provider.get_latest_quote(symbol="NVDA")

        assert quote.symbol == "NVDA"
        assert quote.bid == 119.80
        assert quote.last == 119.83

    def test_missing_credentials_raises_before_touching_the_network(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
        monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no HTTP request should be made without credentials")

        transport = httpx.MockTransport(handler)
        client = httpx.Client(base_url="https://data.alpaca.markets/v2", transport=transport)
        provider = AlpacaProvider(client=client)

        with pytest.raises(RuntimeError, match="ALPACA_API_KEY_ID"):
            provider.get_latest_quote(symbol="TSLA")


class TestOptionsChainStillPlaceholder:
    """Confirms this change's scope: equity data is real, options data
    is not, and it says so rather than silently returning nothing."""

    def test_get_chain_still_raises_not_implemented(self):
        provider = AlpacaProvider(api_key_id="fake", api_secret_key="fake")
        with pytest.raises(NotImplementedError, match="options-chain"):
            provider.get_chain()
