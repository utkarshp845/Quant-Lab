"""End-to-end tests for the "why did this event qualify?" lineage route
(app/api/research_lineage.py) -- a hand-verifiable OHLC series (same
shape as tests/test_backtest_api.py's own `_seed_signal_bars`, so the
signal/outcome bars and values here are hand-checkable, not just
"whatever the engine produced")."""

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
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_research_lineage_api.db"))


def _bar(symbol, timestamp, *, o, h, l, c, provider="csv", timeframe="5m") -> HistoricalBar:
    return HistoricalBar(symbol=symbol, timestamp=timestamp, open=o, high=h, low=l, close=c, volume=1_000, provider=provider, timeframe=timeframe)


def _seed() -> list[HistoricalBar]:
    start = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
    ohlc = [
        (100.0, 100.0, 100.0, 100.0),  # 0
        (100.0, 100.0, 100.0, 100.0),  # 1
        (100.0, 100.0, 97.0, 98.0),  # 2  <- signal: return_5m = -2%
        (97.0, 99.0, 96.0, 97.5),  # 3  <- outcome bar (1 bar / 5m forward)
    ]
    bars = [_bar("TSLA", start + timedelta(minutes=5 * i), o=o, h=h, l=l, c=c) for i, (o, h, l, c) in enumerate(ohlc)]
    save_bars(bars)
    records = compute_features(symbol="TSLA", timeframe="5m", provider="csv", bars=bars, calculated_at=datetime.now(timezone.utc))
    save_features(records)
    return bars


def _create_and_run_experiment() -> dict:
    resp = client.post(
        "/api/research/experiments",
        json={
            "name": "Lineage fixture",
            "hypothesis": "test fixture",
            "symbol": "TSLA",
            "start_date": "2026-01-05",
            "end_date": "2026-01-05",
            "timeframe": "5m",
            "provider": "csv",
            "conditions": [{"feature_id": "price.return_5m", "operator": "<=", "value": -0.01}],
            "outcome": {"metric": "forward_return", "horizon_minutes": 5, "operator": "<=", "threshold": -0.001},
        },
    )
    assert resp.status_code == 200, resp.text
    experiment = resp.json()
    run_resp = client.post(f"/api/research/experiments/{experiment['id']}/run")
    assert run_resp.status_code == 200, run_resp.text
    return run_resp.json()


def test_lineage_bundles_signal_bar_feature_and_outcome():
    _seed()
    experiment = _create_and_run_experiment()

    events = client.get(f"/api/research/experiments/{experiment['id']}/events").json()
    assert events["event_count"] == 1
    event = events["events"][0]

    resp = client.get(
        f"/api/research/experiments/{experiment['id']}/lineage",
        params={"signal_timestamp": event["signal_timestamp"]},
    )
    assert resp.status_code == 200, resp.text
    lineage = resp.json()

    assert lineage["experiment_id"] == experiment["id"]
    assert lineage["symbol"] == "TSLA"
    assert lineage["signal_bar"]["close"] == pytest.approx(98.0)
    assert lineage["outcome_bar"]["close"] == pytest.approx(97.5)
    assert lineage["feature_record"] is not None
    assert lineage["feature_record"]["price"]["return_5m"] == pytest.approx(-0.02, abs=1e-6)

    assert len(lineage["condition_evaluations"]) == 1
    condition = lineage["condition_evaluations"][0]
    assert condition["feature_id"] == "price.return_5m"
    assert condition["observed_value"] == pytest.approx(-0.02, abs=1e-6)
    assert condition["operator"] == "<="
    assert condition["value"] == -0.01
    assert condition["feature_name"]  # human-readable label present, not blank
    assert condition["feature_description"]


def test_lineage_404_for_unknown_experiment():
    resp = client.get(
        "/api/research/experiments/nonexistent/lineage",
        params={"signal_timestamp": "2026-01-05T14:10:00Z"},
    )
    assert resp.status_code == 404


def test_lineage_404_for_timestamp_with_no_event():
    _seed()
    experiment = _create_and_run_experiment()

    resp = client.get(
        f"/api/research/experiments/{experiment['id']}/lineage",
        params={"signal_timestamp": "2099-01-01T00:00:00Z"},
    )
    assert resp.status_code == 404
