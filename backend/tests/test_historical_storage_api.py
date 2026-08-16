"""Tests for the historical-bar storage routes (v0.1.17):

    POST /market-data/history/save
    GET  /market-data/{symbol}/history/stored

Every test gets an isolated, throwaway SQLite file via the
`isolated_db` autouse fixture below (DATABASE_PATH pointed at a fresh
pytest tmp_path per test) -- never the real developer database.

TestExactWorkflow is the literal A-G scenario from this feature's spec:
fetch from a (mocked) Alpaca provider, save, confirm stored, load back,
verify the loaded bars match what was fetched, then disable the
provider and prove the load still works because it never touches
Alpaca at all.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

import app.api.historical_data as historical_data_module
from app.main import app
from app.models.market_data import MarketBar, MarketTimestamp

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_historical_bars.db"))


def _bar_payload(symbol="TSLA", timestamp="2026-08-10T04:00:00Z", provider="alpaca", timeframe="1d", close=247.55):
    return {
        "symbol": symbol,
        "timestamp": timestamp,
        "open": 245.10,
        "high": 248.75,
        "low": 243.20,
        "close": close,
        "volume": 98_400_000,
        "provider": provider,
        "timeframe": timeframe,
    }


class TestSaveHistoricalBarsRoute:
    def test_saves_new_bars(self):
        resp = client.post("/api/market-data/history/save", json={"bars": [_bar_payload()]})

        assert resp.status_code == 200
        assert resp.json() == {"total": 1, "inserted": 1, "skipped_duplicates": 0}

    def test_empty_bars_list_is_a_200_no_op(self):
        resp = client.post("/api/market-data/history/save", json={"bars": []})

        assert resp.status_code == 200
        assert resp.json() == {"total": 0, "inserted": 0, "skipped_duplicates": 0}

    def test_saving_the_same_bars_twice_reports_duplicates_not_an_error(self):
        payload = {"bars": [_bar_payload()]}
        client.post("/api/market-data/history/save", json=payload)

        resp = client.post("/api/market-data/history/save", json=payload)

        assert resp.status_code == 200
        assert resp.json() == {"total": 1, "inserted": 0, "skipped_duplicates": 1}

    def test_malformed_bar_returns_422(self):
        bad_bar = _bar_payload()
        del bad_bar["close"]  # required field missing

        resp = client.post("/api/market-data/history/save", json={"bars": [bad_bar]})

        assert resp.status_code == 422


class TestGetStoredHistoricalBarsRoute:
    def test_returns_previously_saved_bars(self):
        client.post("/api/market-data/history/save", json={"bars": [_bar_payload()]})

        resp = client.get(
            "/api/market-data/TSLA/history/stored",
            params={"start": "2026-08-01", "end": "2026-08-16", "timeframe": "1d", "provider": "alpaca"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "TSLA"
        assert data["provider"] == "alpaca"
        assert data["timeframe"] == "1d"
        assert data["bar_count"] == 1
        assert data["bars"][0]["close"] == 247.55

    def test_returns_zero_bars_not_an_error_when_nothing_stored(self):
        resp = client.get(
            "/api/market-data/TSLA/history/stored",
            params={"start": "2026-08-01", "end": "2026-08-16", "timeframe": "1d", "provider": "alpaca"},
        )

        assert resp.status_code == 200
        assert resp.json()["bar_count"] == 0
        assert resp.json()["bars"] == []

    def test_unsupported_symbol_returns_400(self):
        resp = client.get(
            "/api/market-data/AAPL/history/stored",
            params={"start": "2026-08-01", "end": "2026-08-16", "provider": "alpaca"},
        )
        assert resp.status_code == 400

    def test_unsupported_timeframe_returns_400(self):
        resp = client.get(
            "/api/market-data/TSLA/history/stored",
            params={"start": "2026-08-01", "end": "2026-08-16", "timeframe": "3m", "provider": "alpaca"},
        )
        assert resp.status_code == 400

    def test_end_before_start_returns_400(self):
        resp = client.get(
            "/api/market-data/TSLA/history/stored",
            params={"start": "2026-08-16", "end": "2026-08-01", "provider": "alpaca"},
        )
        assert resp.status_code == 400

    def test_provider_query_param_is_required(self):
        resp = client.get("/api/market-data/TSLA/history/stored", params={"start": "2026-08-01", "end": "2026-08-16"})
        assert resp.status_code == 422

    def test_different_provider_saved_for_the_same_symbol_and_timeframe_does_not_leak_in(self):
        client.post("/api/market-data/history/save", json={"bars": [_bar_payload(provider="alpaca", close=247.55)]})
        client.post("/api/market-data/history/save", json={"bars": [_bar_payload(provider="massive", close=247.60)]})

        resp = client.get(
            "/api/market-data/TSLA/history/stored",
            params={"start": "2026-08-01", "end": "2026-08-16", "timeframe": "1d", "provider": "alpaca"},
        )

        assert resp.json()["bar_count"] == 1
        assert resp.json()["bars"][0]["close"] == 247.55

    def test_a_different_timeframe_saved_for_the_same_symbol_does_not_leak_in(self):
        client.post("/api/market-data/history/save", json={"bars": [_bar_payload(timeframe="1d")]})
        client.post("/api/market-data/history/save", json={"bars": [_bar_payload(timeframe="1h")]})

        resp = client.get(
            "/api/market-data/TSLA/history/stored",
            params={"start": "2026-08-01", "end": "2026-08-16", "timeframe": "1d", "provider": "alpaca"},
        )

        assert resp.json()["bar_count"] == 1
        assert resp.json()["bars"][0]["timeframe"] == "1d"

    def test_a_different_symbol_saved_does_not_leak_in(self):
        client.post("/api/market-data/history/save", json={"bars": [_bar_payload(symbol="TSLA")]})
        client.post("/api/market-data/history/save", json={"bars": [_bar_payload(symbol="NVDA")]})

        resp = client.get(
            "/api/market-data/TSLA/history/stored",
            params={"start": "2026-08-01", "end": "2026-08-16", "timeframe": "1d", "provider": "alpaca"},
        )

        assert resp.json()["bar_count"] == 1
        assert resp.json()["bars"][0]["symbol"] == "TSLA"

    def test_date_range_retrieval_excludes_bars_outside_the_window(self):
        client.post(
            "/api/market-data/history/save",
            json={
                "bars": [
                    _bar_payload(timestamp="2026-08-01T04:00:00Z"),
                    _bar_payload(timestamp="2026-08-10T04:00:00Z"),
                    _bar_payload(timestamp="2026-08-20T04:00:00Z"),
                ]
            },
        )

        resp = client.get(
            "/api/market-data/TSLA/history/stored",
            params={"start": "2026-08-05", "end": "2026-08-15", "timeframe": "1d", "provider": "alpaca"},
        )

        assert resp.json()["bar_count"] == 1
        assert resp.json()["bars"][0]["timestamp"] == "2026-08-10T04:00:00Z"


class TestExactWorkflow:
    """The literal A-G scenario this feature's spec asked to be tested:

    A. Fetch TSLA daily bars from Alpaca.
    B. Save them to the database.
    C. Confirm they exist in the database.
    D. Load the same TSLA daily bars from the database.
    E. Verify the returned records match the originally fetched bars.
    F. Disconnect/disable the Alpaca provider.
    G. Load the same historical data again -- must still work, since it
       comes from the database, not Alpaca.
    """

    def test_fetch_save_confirm_load_then_survive_a_disabled_provider(self, monkeypatch):
        real_alpaca_bars = [
            MarketBar(
                symbol="TSLA",
                timestamp=MarketTimestamp(value="2026-08-10T04:00:00Z", source="alpaca"),
                open=245.10,
                high=248.75,
                low=243.20,
                close=247.55,
                volume=98_400_000,
            ),
            MarketBar(
                symbol="TSLA",
                timestamp=MarketTimestamp(value="2026-08-11T04:00:00Z", source="alpaca"),
                open=247.60,
                high=250.00,
                low=246.00,
                close=249.30,
                volume=87_200_000,
            ),
        ]

        class WorkingAlpacaProvider:
            def get_historical_data(self, **kwargs):
                return real_alpaca_bars

        # A. Fetch TSLA daily bars from Alpaca (mocked, but going through
        # the real GET /market-data/{symbol}/history route end to end).
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: WorkingAlpacaProvider())
        fetch_resp = client.get(
            "/api/market-data/TSLA/history",
            params={"start": "2026-08-10", "end": "2026-08-11", "timeframe": "1d", "provider": "alpaca"},
        )
        assert fetch_resp.status_code == 200
        fetched_bars = fetch_resp.json()["bars"]
        assert len(fetched_bars) == 2

        # B. Save them to the database.
        save_resp = client.post("/api/market-data/history/save", json={"bars": fetched_bars})
        assert save_resp.status_code == 200
        assert save_resp.json()["inserted"] == 2

        # C. Confirm they exist in the database.
        confirm_resp = client.get(
            "/api/market-data/TSLA/history/stored",
            params={"start": "2026-08-10", "end": "2026-08-11", "timeframe": "1d", "provider": "alpaca"},
        )
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["bar_count"] == 2

        # D. Load the same TSLA daily bars from the database.
        loaded_bars = confirm_resp.json()["bars"]

        # E. Verify the returned records match the originally fetched
        # normalized HistoricalBar records exactly.
        assert loaded_bars == fetched_bars

        # F. Disconnect/disable the Alpaca provider -- any call to it
        # now raises, simulating missing credentials or a network outage.
        def broken_provider(name):
            raise RuntimeError("AlpacaProvider requires ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY ...")

        monkeypatch.setattr(historical_data_module, "get_provider", broken_provider)

        # Prove the live-fetch route genuinely fails now.
        broken_fetch_resp = client.get(
            "/api/market-data/TSLA/history",
            params={"start": "2026-08-10", "end": "2026-08-11", "timeframe": "1d", "provider": "alpaca"},
        )
        assert broken_fetch_resp.status_code == 503

        # G. Load the same historical data again -- must still succeed,
        # because /stored never calls get_provider (historical_storage.py
        # imports the repository directly, not the provider registry).
        second_load_resp = client.get(
            "/api/market-data/TSLA/history/stored",
            params={"start": "2026-08-10", "end": "2026-08-11", "timeframe": "1d", "provider": "alpaca"},
        )
        assert second_load_resp.status_code == 200
        assert second_load_resp.json()["bars"] == fetched_bars
