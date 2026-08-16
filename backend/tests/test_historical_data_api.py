"""Tests for GET /api/market-data/{symbol}/history (v0.1.16).

Same three-layer structure as test_market_data_api.py: mocked-provider
tests exercise validation and every branch of the exception-mapping
logic precisely, a second, unmocked class proves the mapping holds
against real provider behavior (CSVProvider genuinely doesn't implement
get_historical_data; Alpaca/Massive with no credentials genuinely raise
the RuntimeError this route maps to 503; an unsupported symbol/
timeframe/provider genuinely gets rejected).
"""

import httpx
import pytest
from fastapi.testclient import TestClient

import app.api.historical_data as historical_data_module
from app.main import app
from app.models.market_data import MarketBar, MarketTimestamp

client = TestClient(app)


def _sample_bars(symbol: str = "TSLA") -> list[MarketBar]:
    return [
        MarketBar(
            symbol=symbol,
            timestamp=MarketTimestamp(value="2026-08-10T13:30:00Z", source="alpaca"),
            open=245.10,
            high=248.75,
            low=243.20,
            close=247.55,
            volume=98_400_000,
        ),
        MarketBar(
            symbol=symbol,
            timestamp=MarketTimestamp(value="2026-08-10T13:31:00Z", source="alpaca"),
            open=247.55,
            high=249.00,
            low=247.00,
            close=248.90,
            volume=54_000,
        ),
    ]


class _FakeProvider:
    def __init__(self, bars: list[MarketBar] | None = None, exc: Exception | None = None):
        self._bars = bars
        self._exc = exc
        self.received_kwargs: dict = {}

    def get_historical_data(self, **kwargs) -> list[MarketBar]:
        self.received_kwargs = kwargs
        if self._exc is not None:
            raise self._exc
        return self._bars or []


class TestGetHistoryRouteMocked:
    def test_successful_fetch_returns_200_with_the_normalized_shape(self, monkeypatch):
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(bars=_sample_bars()))

        resp = client.get(
            "/api/market-data/TSLA/history",
            params={"start": "2026-08-10", "end": "2026-08-10", "timeframe": "1m", "provider": "alpaca"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "TSLA"
        assert data["provider"] == "alpaca"
        assert data["timeframe"] == "1m"
        assert data["start"] == "2026-08-10"
        assert data["end"] == "2026-08-10"
        assert data["bar_count"] == 2
        bar = data["bars"][0]
        assert bar["symbol"] == "TSLA"
        assert bar["open"] == 245.10
        assert bar["provider"] == "alpaca"  # promoted from MarketBar.timestamp.source
        assert bar["timestamp"] == "2026-08-10T13:30:00Z"  # flattened, not nested

    def test_symbol_is_uppercased_before_reaching_the_provider(self, monkeypatch):
        fake = _FakeProvider(bars=_sample_bars())
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: fake)

        resp = client.get(
            "/api/market-data/tsla/history",
            params={"start": "2026-08-10", "end": "2026-08-10", "provider": "alpaca"},
        )

        assert resp.status_code == 200
        assert fake.received_kwargs["symbol"] == "TSLA"

    def test_timeframe_defaults_to_daily(self, monkeypatch):
        fake = _FakeProvider(bars=[])
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: fake)

        resp = client.get(
            "/api/market-data/TSLA/history", params={"start": "2026-08-10", "end": "2026-08-10", "provider": "alpaca"}
        )

        assert resp.status_code == 200
        assert resp.json()["timeframe"] == "1d"
        assert fake.received_kwargs["timeframe"] == "1Day"

    @pytest.mark.parametrize(
        "timeframe,alpaca_timeframe",
        [("1m", "1Min"), ("5m", "5Min"), ("15m", "15Min"), ("1h", "1Hour"), ("1d", "1Day")],
    )
    def test_normalized_timeframe_maps_to_alpacas_own_vocabulary(self, monkeypatch, timeframe, alpaca_timeframe):
        fake = _FakeProvider(bars=[])
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: fake)

        client.get(
            "/api/market-data/TSLA/history",
            params={"start": "2026-08-10", "end": "2026-08-10", "timeframe": timeframe, "provider": "alpaca"},
        )

        assert fake.received_kwargs["timeframe"] == alpaca_timeframe

    @pytest.mark.parametrize(
        "timeframe,multiplier,timespan",
        [("1m", 1, "minute"), ("5m", 5, "minute"), ("15m", 15, "minute"), ("1h", 1, "hour"), ("1d", 1, "day")],
    )
    def test_normalized_timeframe_maps_to_massives_own_vocabulary(self, monkeypatch, timeframe, multiplier, timespan):
        fake = _FakeProvider(bars=[])
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: fake)

        client.get(
            "/api/market-data/TSLA/history",
            params={"start": "2026-08-10", "end": "2026-08-10", "timeframe": timeframe, "provider": "massive"},
        )

        assert fake.received_kwargs["multiplier"] == multiplier
        assert fake.received_kwargs["timespan"] == timespan

    def test_unsupported_symbol_returns_400(self, monkeypatch):
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(bars=[]))

        resp = client.get(
            "/api/market-data/AAPL/history",
            params={"start": "2026-08-10", "end": "2026-08-10", "provider": "alpaca"},
        )

        assert resp.status_code == 400
        assert "AAPL" in resp.json()["detail"]

    def test_unsupported_timeframe_returns_400(self, monkeypatch):
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(bars=[]))

        resp = client.get(
            "/api/market-data/TSLA/history",
            params={"start": "2026-08-10", "end": "2026-08-10", "timeframe": "3m", "provider": "alpaca"},
        )

        assert resp.status_code == 400
        assert "3m" in resp.json()["detail"]

    def test_end_before_start_returns_400(self, monkeypatch):
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(bars=[]))

        resp = client.get(
            "/api/market-data/TSLA/history",
            params={"start": "2026-08-10", "end": "2026-08-01", "provider": "alpaca"},
        )

        assert resp.status_code == 400

    def test_provider_query_param_is_required(self):
        resp = client.get("/api/market-data/TSLA/history", params={"start": "2026-08-10", "end": "2026-08-10"})
        assert resp.status_code == 422

    def test_unknown_provider_name_returns_404(self, monkeypatch):
        def raise_unknown(name):
            raise ValueError(f"Unknown market data provider: {name!r}. Available: ['alpaca']")

        monkeypatch.setattr(historical_data_module, "get_provider", raise_unknown)

        resp = client.get(
            "/api/market-data/TSLA/history",
            params={"start": "2026-08-10", "end": "2026-08-10", "provider": "robinhood"},
        )
        assert resp.status_code == 404
        assert "robinhood" in resp.json()["detail"]

    def test_provider_without_historical_data_support_returns_501(self, monkeypatch):
        monkeypatch.setattr(
            historical_data_module,
            "get_provider",
            lambda name: _FakeProvider(exc=NotImplementedError("csv does not support historical data.")),
        )

        resp = client.get(
            "/api/market-data/TSLA/history", params={"start": "2026-08-10", "end": "2026-08-10", "provider": "csv"}
        )
        assert resp.status_code == 501

    def test_missing_credentials_returns_503(self, monkeypatch):
        monkeypatch.setattr(
            historical_data_module,
            "get_provider",
            lambda name: _FakeProvider(exc=RuntimeError("AlpacaProvider requires ALPACA_API_KEY_ID ...")),
        )

        resp = client.get(
            "/api/market-data/TSLA/history",
            params={"start": "2026-08-10", "end": "2026-08-10", "provider": "alpaca"},
        )
        assert resp.status_code == 503
        assert "ALPACA_API_KEY_ID" in resp.json()["detail"]

    def test_upstream_rate_limit_returns_429(self, monkeypatch):
        request = httpx.Request("GET", "https://data.alpaca.markets/v2/stocks/TSLA/bars")
        response = httpx.Response(429, request=request, text="rate limited")
        exc = httpx.HTTPStatusError("429 error", request=request, response=response)
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(exc=exc))

        resp = client.get(
            "/api/market-data/TSLA/history",
            params={"start": "2026-08-10", "end": "2026-08-10", "provider": "alpaca"},
        )
        assert resp.status_code == 429

    def test_other_upstream_http_error_returns_502(self, monkeypatch):
        request = httpx.Request("GET", "https://data.alpaca.markets/v2/stocks/TSLA/bars")
        response = httpx.Response(500, request=request, text="internal error")
        exc = httpx.HTTPStatusError("500 error", request=request, response=response)
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(exc=exc))

        resp = client.get(
            "/api/market-data/TSLA/history",
            params={"start": "2026-08-10", "end": "2026-08-10", "provider": "alpaca"},
        )
        assert resp.status_code == 502
        assert "alpaca" in resp.json()["detail"]


class TestGetHistoryRouteAgainstRealRegistry:
    """No monkeypatching of get_provider here -- proves the exception
    mapping and symbol/timeframe validation hold against the actual
    registry and actual provider classes, not just test doubles."""

    def test_csv_provider_genuinely_returns_501(self):
        resp = client.get(
            "/api/market-data/TSLA/history", params={"start": "2026-08-10", "end": "2026-08-10", "provider": "csv"}
        )
        assert resp.status_code == 501

    def test_unknown_provider_name_genuinely_returns_404(self):
        resp = client.get(
            "/api/market-data/TSLA/history",
            params={"start": "2026-08-10", "end": "2026-08-10", "provider": "robinhood"},
        )
        assert resp.status_code == 404

    def test_alpaca_without_credentials_genuinely_returns_503(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
        monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)

        resp = client.get(
            "/api/market-data/TSLA/history",
            params={"start": "2026-08-10", "end": "2026-08-10", "provider": "alpaca"},
        )
        assert resp.status_code == 503

    def test_massive_without_credentials_genuinely_returns_503(self, monkeypatch):
        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)

        resp = client.get(
            "/api/market-data/TSLA/history",
            params={"start": "2026-08-10", "end": "2026-08-10", "provider": "massive"},
        )
        assert resp.status_code == 503

    def test_nvda_is_also_allowed(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
        # Still 503 (no credentials), not 400 -- proves NVDA passes the
        # symbol allowlist and the request reaches the provider layer.
        resp = client.get(
            "/api/market-data/NVDA/history",
            params={"start": "2026-08-10", "end": "2026-08-10", "provider": "alpaca"},
        )
        assert resp.status_code == 503

    def test_aapl_is_rejected_before_reaching_any_provider(self):
        resp = client.get(
            "/api/market-data/AAPL/history",
            params={"start": "2026-08-10", "end": "2026-08-10", "provider": "alpaca"},
        )
        assert resp.status_code == 400
