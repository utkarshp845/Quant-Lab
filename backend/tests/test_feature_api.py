"""End-to-end tests for Feature Engine v1's HTTP routes
(app/api/features.py):

    POST /features/compute
    GET  /features/{symbol}

Every test gets an isolated, throwaway SQLite file via the
`isolated_db` autouse fixture (same convention as
tests/test_historical_storage_api.py) -- never the real developer
database. Underlying/SPY/QQQ bars are seeded directly through
app.storage.historical_bar_repository.save_bars(), bypassing the HTTP
save/validation route entirely -- what matters here is Feature Engine
v1's own routes.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.market_data import HistoricalBar
from app.storage.historical_bar_repository import save_bars

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_features.db"))


def _bar(symbol: str, ts: datetime, close: float, *, provider="csv", timeframe="5m", volume=1_000) -> HistoricalBar:
    return HistoricalBar(
        symbol=symbol, timestamp=ts, open=close, high=close + 0.5, low=close - 0.5, close=close, volume=volume,
        provider=provider, timeframe=timeframe,
    )


def _seed_series(symbol: str, closes: list[float], *, start=None, provider="csv") -> list[HistoricalBar]:
    start = start or datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    bars = [_bar(symbol, start + timedelta(minutes=5 * i), c, provider=provider) for i, c in enumerate(closes)]
    save_bars(bars)
    return bars


class TestComputeFeaturesRoute:
    def test_computes_and_persists_features_for_every_bar(self):
        _seed_series("TSLA", [100.0, 101.0, 102.0, 103.0, 104.0, 99.0])

        resp = client.post(
            "/api/features/compute",
            json={"symbol": "TSLA", "start_date": "2026-01-01", "end_date": "2026-01-31", "timeframe": "5m", "provider": "csv"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["bar_count"] == 6
        assert body["feature_count"] == 6
        assert len(body["features"]) == 6

    def test_no_bars_available_yields_zero_features_not_an_error(self):
        resp = client.post(
            "/api/features/compute",
            json={"symbol": "TSLA", "start_date": "2026-01-01", "end_date": "2026-01-31", "timeframe": "5m", "provider": "csv"},
        )

        assert resp.status_code == 200
        assert resp.json()["feature_count"] == 0

    def test_unsupported_timeframe_returns_400(self):
        resp = client.post(
            "/api/features/compute",
            json={"symbol": "TSLA", "start_date": "2026-01-01", "end_date": "2026-01-31", "timeframe": "3m", "provider": "csv"},
        )
        assert resp.status_code == 400

    def test_end_before_start_returns_400(self):
        resp = client.post(
            "/api/features/compute",
            json={"symbol": "TSLA", "start_date": "2026-01-31", "end_date": "2026-01-01", "timeframe": "5m", "provider": "csv"},
        )
        assert resp.status_code == 400

    def test_never_modifies_historical_bars(self):
        """Data-integrity requirement: the feature layer must never
        modify the historical dataset it reads."""
        from app.storage.historical_bar_repository import get_bars

        bars = _seed_series("TSLA", [100.0, 101.0, 102.0])
        before = get_bars(symbol="TSLA", timeframe="5m", provider="csv", start=bars[0].timestamp.date(), end=bars[-1].timestamp.date())

        client.post(
            "/api/features/compute",
            json={"symbol": "TSLA", "start_date": "2026-01-01", "end_date": "2026-01-31", "timeframe": "5m", "provider": "csv"},
        )

        after = get_bars(symbol="TSLA", timeframe="5m", provider="csv", start=bars[0].timestamp.date(), end=bars[-1].timestamp.date())
        assert after == before


class TestMarketContextViaApi:
    def test_tsla_gets_market_context_when_spy_and_qqq_data_exists(self):
        _seed_series("TSLA", [100.0] * 6 + [98.0])
        _seed_series("SPY", [500.0] * 6 + [505.0])
        _seed_series("QQQ", [400.0] * 6 + [396.0])

        resp = client.post(
            "/api/features/compute",
            json={"symbol": "TSLA", "start_date": "2026-01-01", "end_date": "2026-01-31", "timeframe": "5m", "provider": "csv"},
        )

        body = resp.json()
        assert body["market_context_applied"] is True
        last_feature = body["features"][-1]
        assert last_feature["market_context"]["spy_return_5m"] == pytest.approx(0.01)

    def test_nvda_gets_market_context_when_spy_and_qqq_data_exists(self):
        _seed_series("NVDA", [200.0] * 6 + [196.0])
        _seed_series("SPY", [500.0] * 6 + [505.0])
        _seed_series("QQQ", [400.0] * 6 + [396.0])

        resp = client.post(
            "/api/features/compute",
            json={"symbol": "NVDA", "start_date": "2026-01-01", "end_date": "2026-01-31", "timeframe": "5m", "provider": "csv"},
        )

        assert resp.json()["market_context_applied"] is True

    def test_mcl_does_not_get_market_context_by_default(self):
        """"Do not apply SPY/QQQ context to MCL unless explicitly
        configured" -- MCL is not in MARKET_CONTEXT_SYMBOLS (which
        defaults to ALLOWED_SYMBOLS, i.e. TSLA/NVDA only)."""
        _seed_series("MCL", [80.0] * 6 + [78.0])
        _seed_series("SPY", [500.0] * 6 + [505.0])
        _seed_series("QQQ", [400.0] * 6 + [396.0])

        resp = client.post(
            "/api/features/compute",
            json={"symbol": "MCL", "start_date": "2026-01-01", "end_date": "2026-01-31", "timeframe": "5m", "provider": "csv"},
        )

        body = resp.json()
        assert body["market_context_applied"] is False
        assert all(f["market_context"] is None for f in body["features"])

    def test_include_market_context_false_opts_a_configured_symbol_out(self):
        _seed_series("TSLA", [100.0] * 6 + [98.0])
        _seed_series("SPY", [500.0] * 6 + [505.0])

        resp = client.post(
            "/api/features/compute",
            json={
                "symbol": "TSLA", "start_date": "2026-01-01", "end_date": "2026-01-31", "timeframe": "5m", "provider": "csv",
                "include_market_context": False,
            },
        )

        body = resp.json()
        assert body["market_context_applied"] is False
        assert all(f["market_context"] is None for f in body["features"])


class TestGetStoredFeaturesRoute:
    def test_returns_previously_computed_features(self):
        _seed_series("TSLA", [100.0, 101.0, 102.0])
        client.post(
            "/api/features/compute",
            json={"symbol": "TSLA", "start_date": "2026-01-01", "end_date": "2026-01-31", "timeframe": "5m", "provider": "csv"},
        )

        resp = client.get("/api/features/TSLA", params={"start": "2026-01-01", "end": "2026-01-31", "timeframe": "5m", "provider": "csv"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["feature_count"] == 3
        assert body["symbol"] == "TSLA"

    def test_returns_zero_not_an_error_when_nothing_computed_yet(self):
        resp = client.get("/api/features/TSLA", params={"start": "2026-01-01", "end": "2026-01-31", "timeframe": "5m", "provider": "csv"})
        assert resp.status_code == 200
        assert resp.json()["feature_count"] == 0

    def test_provider_query_param_is_required(self):
        resp = client.get("/api/features/TSLA", params={"start": "2026-01-01", "end": "2026-01-31"})
        assert resp.status_code == 422

    def test_end_before_start_returns_400(self):
        resp = client.get("/api/features/TSLA", params={"start": "2026-01-31", "end": "2026-01-01", "provider": "csv"})
        assert resp.status_code == 400


def _without_calculated_at(features: list[dict]) -> list[dict]:
    """`calculated_at` legitimately differs between two separate runs
    (it records WHEN that run happened, same as Research v1's own
    `completed_at`) -- reproducibility means every actual FEATURE VALUE
    is identical, not that the wall-clock computation timestamp is."""
    return [{k: v for k, v in f.items() if k != "calculated_at"} for f in features]


class TestReproducibility:
    def test_recomputing_produces_identical_feature_values(self):
        _seed_series("TSLA", [100.0] * 6 + [98.0, 97.5, 97.0])
        _seed_series("SPY", [500.0] * 9)
        request_body = {
            "symbol": "TSLA", "start_date": "2026-01-01", "end_date": "2026-01-31", "timeframe": "5m", "provider": "csv",
        }

        first = client.post("/api/features/compute", json=request_body).json()
        second = client.post("/api/features/compute", json=request_body).json()

        assert _without_calculated_at(first["features"]) == _without_calculated_at(second["features"])

        # Persisted state also reflects a single set of rows, not doubled.
        stored = client.get(
            "/api/features/TSLA", params={"start": "2026-01-01", "end": "2026-01-31", "timeframe": "5m", "provider": "csv"}
        ).json()
        assert stored["feature_count"] == first["feature_count"]


class TestExactWorkflow:
    """The end-to-end scenario this feature was built for: normalized
    historical_bars -> feature engine -> historical_features, then
    retrieved back."""

    def test_bars_to_features_to_persisted_retrieval(self):
        bars = _seed_series("TSLA", [100.0] * 6 + [98.0, 97.8, 97.5, 97.0, 100.0, 100.0, 98.5])
        _seed_series("SPY", [500.0 + i * 0.05 for i in range(13)])
        _seed_series("QQQ", [400.0 + i * 0.03 for i in range(13)])

        compute_resp = client.post(
            "/api/features/compute",
            json={"symbol": "TSLA", "start_date": "2026-01-01", "end_date": "2026-01-31", "timeframe": "5m", "provider": "csv"},
        )
        assert compute_resp.status_code == 200
        computed = compute_resp.json()
        assert computed["feature_count"] == len(bars)

        retrieved_resp = client.get(
            "/api/features/TSLA", params={"start": "2026-01-01", "end": "2026-01-31", "timeframe": "5m", "provider": "csv"}
        )
        assert retrieved_resp.status_code == 200
        retrieved = retrieved_resp.json()

        assert retrieved["features"] == computed["features"]
        # Every feature-record timestamp matches a bar timestamp exactly.
        assert [f["timestamp"] for f in retrieved["features"]] == [b.timestamp.isoformat().replace("+00:00", "Z") for b in bars]
