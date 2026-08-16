"""Tests for SchwabProvider's real equity-data integration (bars, latest quote).

Everything here uses httpx.MockTransport -- no real network call, no
real credentials, ever, and no real OAuth login (which this provider
deliberately cannot do itself -- see schwab_provider.py's module
docstring). Response fixtures for price-history/quotes are shaped per
the CONFIRMED field names documented there; the quote-timestamp field
name is the one piece flagged as unconfirmed, and is exercised as
documented rather than silently assumed correct.

Covers TSLA and NVDA per the requested scope, plus what's genuinely
new about this provider versus Alpaca/Massive: OAuth2 access-token
refresh, caching, and re-refresh on expiry -- exercised with an
injectable clock rather than real timing.
"""

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app.models.market_data import MarketBar, Quote
from app.providers.schwab_provider import SchwabProvider

TOKEN_PATH = "/v1/oauth/token"


def _token_response(access_token="fake_access_1", expires_in=1800) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": access_token,
            "refresh_token": "fake_refresh_rotated",
            "id_token": "fake",
            "token_type": "Bearer",
            "scope": "api",
            "expires_in": expires_in,
        },
    )


def _price_history_response(symbol: str, candles: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"candles": candles, "symbol": symbol, "empty": len(candles) == 0})


def _quote_response(symbol: str, bid: float, ask: float, last: float, t_ms: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            symbol: {
                "assetMainType": "EQUITY",
                "symbol": symbol,
                "quote": {"bidPrice": bid, "askPrice": ask, "lastPrice": last, "quoteTimeInLong": t_ms},
            }
        },
    )


TSLA_CANDLES_MS = [
    {"datetime": 1786334400000, "open": 245.10, "high": 248.75, "low": 243.20, "close": 247.55, "volume": 98_400_000},
    {"datetime": 1786420800000, "open": 247.60, "high": 250.00, "low": 246.00, "close": 249.30, "volume": 87_200_000},
]

NVDA_CANDLES_MS = [
    {"datetime": 1786334400000, "open": 118.40, "high": 120.10, "low": 117.90, "close": 119.85, "volume": 210_000_000},
]


def _make_provider(handler, now=None) -> SchwabProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(base_url="https://api.schwabapi.com", transport=transport)
    return SchwabProvider(
        client_id="fake_client_id",
        client_secret="fake_client_secret",
        refresh_token="fake_refresh_token",
        client=client,
        now=now,
    )


class TestCredentialsRequiredBeforeAnyRequest:
    @pytest.mark.parametrize(
        "missing_env",
        ["SCHWAB_CLIENT_ID", "SCHWAB_CLIENT_SECRET", "SCHWAB_REFRESH_TOKEN"],
    )
    def test_missing_any_one_credential_raises_before_touching_the_network(self, monkeypatch, missing_env):
        monkeypatch.delenv("SCHWAB_CLIENT_ID", raising=False)
        monkeypatch.delenv("SCHWAB_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("SCHWAB_REFRESH_TOKEN", raising=False)

        creds = {"client_id": "id", "client_secret": "secret", "refresh_token": "token"}
        env_to_kwarg = {
            "SCHWAB_CLIENT_ID": "client_id",
            "SCHWAB_CLIENT_SECRET": "client_secret",
            "SCHWAB_REFRESH_TOKEN": "refresh_token",
        }
        del creds[env_to_kwarg[missing_env]]

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no HTTP request should be made without full credentials")

        transport = httpx.MockTransport(handler)
        client = httpx.Client(base_url="https://api.schwabapi.com", transport=transport)
        provider = SchwabProvider(client=client, **creds)

        with pytest.raises(RuntimeError, match=missing_env):
            provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 12))

    def test_error_names_the_bootstrap_script(self, monkeypatch):
        monkeypatch.delenv("SCHWAB_REFRESH_TOKEN", raising=False)

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no HTTP request should be made without credentials")

        transport = httpx.MockTransport(handler)
        client = httpx.Client(base_url="https://api.schwabapi.com", transport=transport)
        provider = SchwabProvider(client_id="id", client_secret="secret", client=client)

        with pytest.raises(RuntimeError, match="schwab_oauth_bootstrap"):
            provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))


class TestAccessTokenRefresh:
    def test_first_call_fetches_an_access_token_via_refresh_grant(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if request.url.path == TOKEN_PATH:
                assert request.headers["Authorization"].startswith("Basic ")
                assert "grant_type=refresh_token" in request.content.decode()
                assert "refresh_token=fake_refresh_token" in request.content.decode()
                return _token_response()
            return _price_history_response("TSLA", [])

        provider = _make_provider(handler)
        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))

        token_calls = [c for c in calls if c.url.path == TOKEN_PATH]
        assert len(token_calls) == 1

    def test_access_token_is_reused_across_calls_within_its_lifetime(self):
        token_call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_call_count
            if request.url.path == TOKEN_PATH:
                token_call_count += 1
                return _token_response()
            assert request.headers["Authorization"] == "Bearer fake_access_1"
            return _price_history_response("TSLA", [])

        fixed_now = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)
        provider = _make_provider(handler, now=lambda: fixed_now)

        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))
        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))

        assert token_call_count == 1

    def test_access_token_is_refreshed_again_once_expired(self):
        token_call_count = 0
        current_time = {"value": datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)}

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_call_count
            if request.url.path == TOKEN_PATH:
                token_call_count += 1
                return _token_response(access_token=f"fake_access_{token_call_count}")
            return _price_history_response("TSLA", [])

        provider = _make_provider(handler, now=lambda: current_time["value"])

        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))
        assert token_call_count == 1

        # Jump forward past the 30-minute access-token lifetime.
        current_time["value"] += timedelta(minutes=31)
        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))
        assert token_call_count == 2

    def test_custom_expires_in_from_the_token_response_is_honored(self):
        current_time = {"value": datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)}
        token_call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_call_count
            if request.url.path == TOKEN_PATH:
                token_call_count += 1
                return _token_response(expires_in=120)  # a short-lived token, atypical but should be honored
            return _price_history_response("TSLA", [])

        provider = _make_provider(handler, now=lambda: current_time["value"])
        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))
        assert token_call_count == 1

        current_time["value"] += timedelta(seconds=90)  # past a 120s token minus the 60s early-refresh margin
        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))
        assert token_call_count == 2


class TestGetHistoricalData:
    def _handler_with_token(self, price_history_response):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == TOKEN_PATH:
                return _token_response()
            return price_history_response(request)

        return handler

    def test_tsla_candles_are_parsed_into_market_bars(self):
        def price_history(request: httpx.Request) -> httpx.Response:
            assert request.url.params["symbol"] == "TSLA"
            return _price_history_response("TSLA", TSLA_CANDLES_MS)

        provider = _make_provider(self._handler_with_token(price_history))
        bars = provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 10), end=date(2026, 8, 11))

        assert len(bars) == 2
        assert all(isinstance(b, MarketBar) for b in bars)
        assert bars[0].symbol == "TSLA"
        assert bars[0].open == 245.10
        assert bars[0].close == 247.55
        assert bars[0].volume == 98_400_000
        assert bars[0].timestamp.source == "schwab"
        assert bars[0].timestamp.value == datetime(2026, 8, 10, 4, 0, 0, tzinfo=timezone.utc)

    def test_nvda_candles_are_parsed_into_market_bars(self):
        def price_history(request: httpx.Request) -> httpx.Response:
            return _price_history_response("NVDA", NVDA_CANDLES_MS)

        provider = _make_provider(self._handler_with_token(price_history))
        bars = provider.get_historical_data(symbol="NVDA", start=date(2026, 8, 10), end=date(2026, 8, 10))

        assert len(bars) == 1
        assert bars[0].symbol == "NVDA"
        assert bars[0].close == 119.85

    def test_fractional_volume_is_rounded_to_the_nearest_share(self):
        """This provider hasn't been tested against a real account yet
        (see module docstring), so it defends against the same
        fractional-volume issue confirmed for real on Massive's data,
        rather than assuming Schwab's is always a clean integer."""

        def price_history(request: httpx.Request) -> httpx.Response:
            candle = dict(TSLA_CANDLES_MS[0], volume=98_400_000.6)
            return _price_history_response("TSLA", [candle])

        provider = _make_provider(self._handler_with_token(price_history))
        bars = provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 10), end=date(2026, 8, 10))

        assert bars[0].volume == 98_400_001
        assert isinstance(bars[0].volume, int)

    def test_period_and_frequency_params_are_sent(self):
        def price_history(request: httpx.Request) -> httpx.Response:
            assert request.url.params["periodType"] == "day"
            assert request.url.params["frequencyType"] == "daily"
            assert request.url.params["frequency"] == "1"
            return _price_history_response("TSLA", [])

        provider = _make_provider(self._handler_with_token(price_history))
        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))

    def test_start_and_end_are_sent_as_millisecond_epochs(self):
        def price_history(request: httpx.Request) -> httpx.Response:
            expected_start = str(int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000))
            expected_end = str(int(datetime(2026, 8, 12, tzinfo=timezone.utc).timestamp() * 1000))
            assert request.url.params["startDate"] == expected_start
            assert request.url.params["endDate"] == expected_end
            return _price_history_response("TSLA", [])

        provider = _make_provider(self._handler_with_token(price_history))
        provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 12))

    def test_empty_candles_returns_an_empty_list_not_an_error(self):
        def price_history(request: httpx.Request) -> httpx.Response:
            return _price_history_response("TSLA", [])

        provider = _make_provider(self._handler_with_token(price_history))
        assert provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1)) == []

    def test_http_error_propagates_instead_of_being_swallowed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == TOKEN_PATH:
                return _token_response()
            return httpx.Response(500, json={"error": "internal"})

        provider = _make_provider(handler)
        with pytest.raises(httpx.HTTPStatusError):
            provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))

    def test_token_refresh_http_error_propagates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == TOKEN_PATH:
                return httpx.Response(400, json={"error": "invalid_grant"})
            raise AssertionError("should not reach the data endpoint if the token refresh failed")

        provider = _make_provider(handler)
        with pytest.raises(httpx.HTTPStatusError):
            provider.get_historical_data(symbol="TSLA", start=date(2026, 8, 1), end=date(2026, 8, 1))


class TestGetLatestQuote:
    def test_tsla_quote_is_parsed_from_the_nested_quote_object(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == TOKEN_PATH:
                return _token_response()
            assert request.url.params["symbols"] == "TSLA"
            return _quote_response("TSLA", bid=247.50, ask=247.55, last=247.52, t_ms=1786478400000)

        provider = _make_provider(handler)
        quote = provider.get_latest_quote(symbol="TSLA")

        assert isinstance(quote, Quote)
        assert quote.symbol == "TSLA"
        assert quote.bid == 247.50
        assert quote.ask == 247.55
        assert quote.last == 247.52
        assert quote.timestamp.source == "schwab"
        assert quote.timestamp.value == datetime(2026, 8, 11, 20, 0, 0, tzinfo=timezone.utc)

    def test_nvda_quote_is_parsed_from_the_nested_quote_object(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == TOKEN_PATH:
                return _token_response()
            return _quote_response("NVDA", bid=119.80, ask=119.85, last=119.83, t_ms=1786478400000)

        provider = _make_provider(handler)
        quote = provider.get_latest_quote(symbol="NVDA")

        assert quote.symbol == "NVDA"
        assert quote.bid == 119.80
        assert quote.last == 119.83


class TestOptionsChainStillPlaceholder:
    def test_get_chain_still_raises_not_implemented(self):
        provider = SchwabProvider(client_id="id", client_secret="secret", refresh_token="token")
        with pytest.raises(NotImplementedError, match="options-chain"):
            provider.get_chain()
