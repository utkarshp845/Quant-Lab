"""End-to-end tests for the OOS / Holdout Partition Framework's HTTP
routes (app/api/oos_partitions.py). Same isolated-throwaway-database
convention as tests/test_research_api.py."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.market_data import HistoricalBar
from app.storage.historical_bar_repository import save_bars

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_oos_partitions.db"))


def _create_request(**overrides) -> dict:
    fields = {
        "symbol": "TSLA",
        "timeframe": "5m",
        "provider": "csv",
        "development_start": "2024-01-01T00:00:00Z",
        "development_end": "2024-06-30T00:00:00Z",
        "holdout_start": "2024-07-01T00:00:00Z",
        "holdout_end": "2024-12-31T00:00:00Z",
    }
    fields.update(overrides)
    return fields


class TestCreateAndFetch:
    def test_create_then_get_round_trips(self):
        created = client.post("/api/oos/partitions", json=_create_request())
        assert created.status_code == 200
        body = created.json()

        fetched = client.get(f"/api/oos/partitions/{body['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == body["id"]

    def test_creating_the_same_partition_twice_returns_the_same_id(self):
        first = client.post("/api/oos/partitions", json=_create_request()).json()
        second = client.post("/api/oos/partitions", json=_create_request()).json()
        assert first["id"] == second["id"]
        assert first["created_at"] == second["created_at"]

        listed = client.get("/api/oos/partitions").json()
        assert len(listed) == 1

    def test_unknown_symbol_is_rejected(self):
        response = client.post("/api/oos/partitions", json=_create_request(symbol="AAPL"))
        assert response.status_code == 400

    def test_overlapping_ranges_are_rejected_with_422(self):
        response = client.post(
            "/api/oos/partitions",
            json=_create_request(development_end="2024-08-01T00:00:00Z", holdout_start="2024-07-01T00:00:00Z"),
        )
        assert response.status_code == 422

    def test_unknown_id_is_404(self):
        assert client.get("/api/oos/partitions/does-not-exist").status_code == 404


class TestSegmentBarRoutes:
    def _seed(self):
        save_bars(
            [
                HistoricalBar(
                    symbol="TSLA", timestamp=datetime(2024, 3, 1, tzinfo=timezone.utc),
                    open=100, high=101, low=99, close=100.5, volume=1000, provider="csv", timeframe="5m",
                ),
                HistoricalBar(
                    symbol="TSLA", timestamp=datetime(2024, 8, 1, tzinfo=timezone.utc),
                    open=110, high=111, low=109, close=110.5, volume=1000, provider="csv", timeframe="5m",
                ),
            ]
        )

    def test_development_bars_are_returned_unrestricted(self):
        self._seed()
        partition_id = client.post("/api/oos/partitions", json=_create_request()).json()["id"]

        response = client.get(f"/api/oos/partitions/{partition_id}/development/bars")
        assert response.status_code == 200
        bars = response.json()
        assert len(bars) == 1
        assert bars[0]["timestamp"].startswith("2024-03-01")

    def test_holdout_bars_are_refused_without_confirmation(self):
        self._seed()
        partition_id = client.post("/api/oos/partitions", json=_create_request()).json()["id"]

        response = client.get(f"/api/oos/partitions/{partition_id}/holdout/bars")
        assert response.status_code == 403

    def test_holdout_bars_are_returned_once_explicitly_confirmed(self):
        self._seed()
        partition_id = client.post("/api/oos/partitions", json=_create_request()).json()["id"]

        response = client.get(
            f"/api/oos/partitions/{partition_id}/holdout/bars", params={"confirm_oos_validation_use": "true"}
        )
        assert response.status_code == 200
        bars = response.json()
        assert len(bars) == 1
        assert bars[0]["timestamp"].startswith("2024-08-01")
