"""Tests for GET /api/market-data/{symbol}/quote.

Three layers: mocked-provider tests exercise every branch of the
exception-mapping logic precisely (404/501/503/502) plus the
best-effort volume enrichment; a second, unmocked class proves that
mapping actually holds against real provider behavior -- CSVProvider
genuinely doesn't implement get_latest_quote (no mocking needed to
prove that), and Alpaca/Massive with no credentials genuinely raise
the RuntimeError this route maps to 503.
"""

from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

import app.api.market_data as market_data_module
from app.main import app
from app.models.market_data import MarketBar, MarketTimestamp, Quote

client = TestClient(app)


def _sample_quote(symbol: str = "TSLA") -> Quote:
    return Quote(
        symbol=symbol,
        bid=340.0,
        ask=340.5,
        last=340.2,
        timestamp=MarketTimestamp(value="2026-08-15T20:00:00Z", source="alpaca"),
    )


def _sample_bar(symbol: str = "TSLA", volume: int = 45_437_145) -> MarketBar:
    return MarketBar(
        symbol=symbol,
        timestamp=MarketTimestamp(value="2026-08-15T04:00:00Z", source="alpaca"),
        open=338.0,
        high=343.0,
        low=337.5,
        close=340.2,
        volume=volume,
    )


class _FakeProvider:
    """Returns a fixed quote (or raises a fixed exception), and
    optionally a fixed list of bars for the volume-enrichment call --
    whichever the test needs, without a real provider or network call
    involved. get_historical_data is intentionally NOT defined unless
    `bars` is passed, so tests can exercise "provider doesn't support
    this at all" (an AttributeError) without special-casing it."""

    def __init__(self, quote: Quote | None = None, exc: Exception | None = None, bars: list | None = None):
        self._quote = quote
        self._exc = exc
        if bars is not None:
            self.get_historical_data = lambda **kwargs: bars

    def get_latest_quote(self, *, symbol: str) -> Quote:
        if self._exc is not None:
            raise self._exc
        return self._quote


class TestGetLatestQuoteRouteMocked:
    def test_successful_quote_returns_200_with_the_normalized_shape(self, monkeypatch):
        monkeypatch.setattr(
            market_data_module,
            "get_provider",
            lambda name: _FakeProvider(quote=_sample_quote(), bars=[_sample_bar()]),
        )

        resp = client.get("/api/market-data/TSLA/quote", params={"provider": "alpaca"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "TSLA"
        assert data["price"] == 340.2  # Quote.last, renamed
        assert data["bid"] == 340.0
        assert data["ask"] == 340.5
        assert data["volume"] == 45_437_145
        assert data["provider"] == "alpaca"  # promoted from Quote.timestamp.source
        assert data["timestamp"] == "2026-08-15T20:00:00Z"  # flattened, not nested

    def test_symbol_is_uppercased_before_reaching_the_provider(self, monkeypatch):
        received = {}

        class Provider:
            def get_latest_quote(self, *, symbol):
                received["symbol"] = symbol
                return _sample_quote(symbol)

        monkeypatch.setattr(market_data_module, "get_provider", lambda name: Provider())
        client.get("/api/market-data/tsla/quote", params={"provider": "alpaca"})

        assert received["symbol"] == "TSLA"

    def test_provider_query_param_is_required(self):
        resp = client.get("/api/market-data/TSLA/quote")
        assert resp.status_code == 422

    def test_unknown_provider_name_returns_404(self, monkeypatch):
        def raise_unknown(name):
            raise ValueError(f"Unknown market data provider: {name!r}. Available: ['alpaca']")

        monkeypatch.setattr(market_data_module, "get_provider", raise_unknown)

        resp = client.get("/api/market-data/TSLA/quote", params={"provider": "robinhood"})
        assert resp.status_code == 404
        assert "robinhood" in resp.json()["detail"]

    def test_provider_without_quote_support_returns_501(self, monkeypatch):
        monkeypatch.setattr(
            market_data_module,
            "get_provider",
            lambda name: _FakeProvider(exc=NotImplementedError("csv does not support live quotes.")),
        )

        resp = client.get("/api/market-data/TSLA/quote", params={"provider": "csv"})
        assert resp.status_code == 501

    def test_missing_credentials_returns_503(self, monkeypatch):
        monkeypatch.setattr(
            market_data_module,
            "get_provider",
            lambda name: _FakeProvider(exc=RuntimeError("AlpacaProvider requires ALPACA_API_KEY_ID ...")),
        )

        resp = client.get("/api/market-data/TSLA/quote", params={"provider": "alpaca"})
        assert resp.status_code == 503
        assert "ALPACA_API_KEY_ID" in resp.json()["detail"]

    def test_upstream_http_error_returns_502(self, monkeypatch):
        request = httpx.Request("GET", "https://data.alpaca.markets/v2/stocks/TSLA/quotes/latest")
        response = httpx.Response(500, request=request, text="internal error")
        exc = httpx.HTTPStatusError("500 error", request=request, response=response)
        monkeypatch.setattr(market_data_module, "get_provider", lambda name: _FakeProvider(exc=exc))

        resp = client.get("/api/market-data/TSLA/quote", params={"provider": "alpaca"})
        assert resp.status_code == 502
        assert "alpaca" in resp.json()["detail"]


class TestVolumeEnrichmentIsAlwaysBestEffort:
    """Volume was specified as "if available" -- these prove that no
    way for the enrichment call to fail ever turns a successful quote
    into a failed request. A quote succeeding but volume enrichment
    failing must always still be a 200 with volume: null."""

    def test_volume_is_populated_when_the_provider_has_bars(self, monkeypatch):
        monkeypatch.setattr(
            market_data_module,
            "get_provider",
            lambda name: _FakeProvider(quote=_sample_quote(), bars=[_sample_bar(volume=1_234_567)]),
        )
        resp = client.get("/api/market-data/TSLA/quote", params={"provider": "alpaca"})
        assert resp.status_code == 200
        assert resp.json()["volume"] == 1_234_567

    def test_volume_is_null_when_provider_has_no_historical_data_method(self, monkeypatch):
        """No `bars=` passed -- _FakeProvider has no get_historical_data
        at all, so the route's call raises AttributeError, caught by
        the deliberately-broad except in _best_effort_todays_volume."""
        monkeypatch.setattr(market_data_module, "get_provider", lambda name: _FakeProvider(quote=_sample_quote()))
        resp = client.get("/api/market-data/TSLA/quote", params={"provider": "alpaca"})
        assert resp.status_code == 200
        assert resp.json()["volume"] is None

    def test_volume_is_null_when_historical_data_returns_no_bars(self, monkeypatch):
        monkeypatch.setattr(
            market_data_module, "get_provider", lambda name: _FakeProvider(quote=_sample_quote(), bars=[])
        )
        resp = client.get("/api/market-data/TSLA/quote", params={"provider": "alpaca"})
        assert resp.status_code == 200
        assert resp.json()["volume"] is None

    def test_volume_is_null_when_historical_data_itself_raises(self, monkeypatch):
        class Provider:
            def get_latest_quote(self, *, symbol):
                return _sample_quote(symbol)

            def get_historical_data(self, **kwargs):
                raise RuntimeError("upstream failure fetching bars")

        monkeypatch.setattr(market_data_module, "get_provider", lambda name: Provider())
        resp = client.get("/api/market-data/TSLA/quote", params={"provider": "alpaca"})
        assert resp.status_code == 200  # the quote itself still succeeds
        assert resp.json()["volume"] is None

    def test_a_trailing_window_ending_today_is_passed_to_historical_data(self, monkeypatch):
        """Not strictly start=end=today -- confirmed empirically that
        returns nothing on a weekend, which would make volume look
        unavailable every Saturday/Sunday even though the quote itself
        still works. A trailing window's last bar is the most recent
        completed session instead."""
        received = {}

        class Provider:
            def get_latest_quote(self, *, symbol):
                return _sample_quote(symbol)

            def get_historical_data(self, **kwargs):
                received.update(kwargs)
                return [_sample_bar()]

        monkeypatch.setattr(market_data_module, "get_provider", lambda name: Provider())
        client.get("/api/market-data/TSLA/quote", params={"provider": "alpaca"})

        assert received["end"] == date.today()
        assert received["start"] < received["end"]
        assert received["symbol"] == "TSLA"

    def test_uses_the_last_bar_when_multiple_are_returned(self, monkeypatch):
        """A trailing window can return several bars -- the most
        recent one (last in the list) is what "current volume" should
        mean, not the oldest."""
        older_bar = _sample_bar(volume=1_000_000)
        newer_bar = _sample_bar(volume=2_000_000)

        monkeypatch.setattr(
            market_data_module,
            "get_provider",
            lambda name: _FakeProvider(quote=_sample_quote(), bars=[older_bar, newer_bar]),
        )
        resp = client.get("/api/market-data/TSLA/quote", params={"provider": "alpaca"})
        assert resp.json()["volume"] == 2_000_000


class TestGetLatestQuoteRouteAgainstRealRegistry:
    """No monkeypatching of get_provider here -- these prove the
    exception mapping holds against the actual registry and actual
    provider classes, not just test doubles standing in for them."""

    def test_csv_provider_genuinely_returns_501(self):
        resp = client.get("/api/market-data/TSLA/quote", params={"provider": "csv"})
        assert resp.status_code == 501

    def test_unknown_provider_name_genuinely_returns_404(self):
        resp = client.get("/api/market-data/TSLA/quote", params={"provider": "robinhood"})
        assert resp.status_code == 404

    def test_alpaca_without_credentials_genuinely_returns_503(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
        monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)

        resp = client.get("/api/market-data/TSLA/quote", params={"provider": "alpaca"})
        assert resp.status_code == 503

    def test_massive_without_credentials_genuinely_returns_503(self, monkeypatch):
        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)

        resp = client.get("/api/market-data/TSLA/quote", params={"provider": "massive"})
        assert resp.status_code == 503
