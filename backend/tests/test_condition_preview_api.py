"""Tests for POST /research/conditions/preview (app/api/research_notebook.py,
app/research/design_preview.py) -- the Design stage's sample-size-only
preview. Confirms it counts matching signals from already-computed
features WITHOUT ever touching bars, an Outcome, or a success value."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.features.engine import compute_features
from app.main import app
from app.models.market_data import HistoricalBar
from app.storage.feature_repository import save_features
from app.storage.historical_bar_repository import save_bars

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_condition_preview.db"))


def _seed():
    start = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
    ohlc = [
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 100.0, 97.0, 98.0),  # return_5m = -2% -- matches <= -1%
        (98.0, 98.0, 96.0, 96.5),  # return_5m ~ -1.53% -- matches
        (96.5, 99.0, 96.0, 99.0),  # return_5m ~ +2.6% -- does not match
    ]
    bars = [
        HistoricalBar(symbol="TSLA", timestamp=start + timedelta(minutes=5 * i), open=o, high=h, low=l, close=c, volume=1_000, provider="csv", timeframe="5m")
        for i, (o, h, l, c) in enumerate(ohlc)
    ]
    save_bars(bars)
    records = compute_features(symbol="TSLA", timeframe="5m", provider="csv", bars=bars, calculated_at=datetime.now(timezone.utc))
    save_features(records)


def _preview_body(**overrides) -> dict:
    body = {
        "symbol": "TSLA",
        "start_date": "2026-01-05",
        "end_date": "2026-01-05",
        "timeframe": "5m",
        "provider": "csv",
        "conditions": [{"feature_id": "price.return_5m", "operator": "<=", "value": -0.01}],
    }
    body.update(overrides)
    return body


def test_counts_matching_signals_without_outcome_fields_in_response():
    _seed()
    resp = client.post("/api/research/conditions/preview", json=_preview_body())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["matching_signal_count"] == 2
    assert body["total_feature_records"] == 5
    # The response shape itself cannot contain outcome/success data --
    # only these two keys exist.
    assert set(body.keys()) == {"total_feature_records", "matching_signal_count"}


def test_no_bars_means_zero_matches():
    resp = client.post("/api/research/conditions/preview", json=_preview_body(symbol="NVDA"))
    assert resp.status_code == 200
    assert resp.json() == {"total_feature_records": 0, "matching_signal_count": 0}


def test_unknown_feature_id_rejected():
    resp = client.post(
        "/api/research/conditions/preview",
        json=_preview_body(conditions=[{"feature_id": "not.a.feature", "operator": "<=", "value": -0.01}]),
    )
    assert resp.status_code == 400


def test_unsupported_symbol_rejected():
    resp = client.post("/api/research/conditions/preview", json=_preview_body(symbol="MCL"))
    assert resp.status_code == 400


def test_end_before_start_rejected():
    resp = client.post("/api/research/conditions/preview", json=_preview_body(start_date="2026-01-10", end_date="2026-01-01"))
    assert resp.status_code == 400
