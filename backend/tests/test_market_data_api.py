"""Tests for GET /api/market-data/{symbol}/quote.

Two layers: mocked-provider tests exercise every branch of the
exception-mapping logic precisely (404/501/503/502), and a second,
unmocked class proves that mapping actually holds against real
provider behavior -- CSVProvider genuinely doesn't implement
get_latest_quote (no mocking needed to prove that), and Alpaca with no
credentials genuinely raises the RuntimeError this route maps to 503.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

import app.api.market_data as market_data_module
from app.main import app
from app.models.market_data import MarketTimestamp, Quote

client = TestClient(app)


def _sample_quote(symbol: str = "TSLA") -> Quote:
    return Quote(
        symbol=symbol,
        bid=340.0,
        ask=340.5,
        last=340.2,
        timestamp=MarketTimestamp(value="2026-08-15T20:00:00Z", source="alpaca"),
    )


class _FakeProvider:
    """Returns a fixed quote, or raises a fixed exception -- whichever
    the test needs, without a real provider or network call involved."""

    def __init__(self, quote: Quote | None = None, exc: Exception | None = None):
        self._quote = quote
        self._exc = exc

    def get_latest_quote(self, *, symbol: str) -> Quote:
        if self._exc is not None:
            raise self._exc
        return self._quote


class TestGetLatestQuoteRouteMocked:
    def test_successful_quote_returns_200_with_the_quote_shape(self, monkeypatch):
        monkeypatch.setattr(market_data_module, "get_provider", lambda name: _FakeProvider(quote=_sample_quote()))

        resp = client.get("/api/market-data/TSLA/quote", params={"provider": "alpaca"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "TSLA"
        assert data["bid"] == 340.0
        assert data["ask"] == 340.5
        assert data["last"] == 340.2
        assert data["timestamp"]["source"] == "alpaca"

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
