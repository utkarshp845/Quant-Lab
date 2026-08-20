"""End-to-end tests for the Statistical Validation HTTP routes
(app/api/statistical_validation.py) -- the one real "engine exists,
route doesn't" gap the redesign audit found. Exercises the real
create-experiment -> run -> create-backtest -> run -> GET
.../statistical-validation[-v2] HTTP flow, same convention as
tests/test_backtest_api.py, proving the route wraps
build_statistical_validation_report()/_v2() correctly rather than
re-testing the engines themselves (already covered by
tests/test_statistical_validation_engine.py / test_statistical_validation_v2_engine.py).
"""

import random
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
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_statistical_validation_api.db"))


def _generate_bars(symbol="TSLA", *, n=140, seed=0, provider="csv", timeframe="5m") -> list[HistoricalBar]:
    rnd = random.Random(seed)
    base = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
    drop_indices = {20, 50, 51, 52, 90, 91}
    closes = [100.0]
    for i in range(1, n):
        pct = -0.025 if i in drop_indices else rnd.uniform(-0.003, 0.003)
        closes.append(closes[-1] * (1 + pct))
    bars = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i > 0 else close
        bars.append(
            HistoricalBar(
                symbol=symbol, timestamp=base + timedelta(minutes=5 * i), open=open_,
                high=max(open_, close) * 1.0005, low=min(open_, close) * 0.9995, close=close,
                volume=1_000, provider=provider, timeframe=timeframe,
            )
        )
    return bars


def _seed_bars_and_features(symbol="TSLA", provider="csv", timeframe="5m") -> list[HistoricalBar]:
    bars = _generate_bars(symbol=symbol, provider=provider, timeframe=timeframe)
    save_bars(bars)
    records = compute_features(symbol=symbol, timeframe=timeframe, provider=provider, bars=bars, calculated_at=datetime.now(timezone.utc))
    save_features(records)
    return bars


def _create_and_run_experiment_and_backtest(bars: list[HistoricalBar]) -> tuple[dict, dict]:
    experiment_resp = client.post(
        "/api/research/experiments",
        json={
            "name": "Synthetic Selling Continuation",
            "hypothesis": "test fixture",
            "symbol": bars[0].symbol,
            "start_date": bars[0].timestamp.date().isoformat(),
            "end_date": bars[-1].timestamp.date().isoformat(),
            "timeframe": bars[0].timeframe,
            "provider": bars[0].provider,
            "conditions": [{"feature_id": "price.return_5m", "operator": "<=", "value": -0.01}],
            "outcome": {"metric": "forward_return", "horizon_minutes": 5, "operator": "<=", "threshold": -0.005},
        },
    )
    assert experiment_resp.status_code == 200, experiment_resp.text
    experiment = experiment_resp.json()
    run_resp = client.post(f"/api/research/experiments/{experiment['id']}/run")
    assert run_resp.status_code == 200

    backtest_resp = client.post("/api/backtests", json={"experiment_id": experiment["id"], "windows": [1, 2, 3]})
    assert backtest_resp.status_code == 200, backtest_resp.text
    backtest = backtest_resp.json()
    run_backtest_resp = client.post(f"/api/backtests/{backtest['id']}/run")
    assert run_backtest_resp.status_code == 200
    return experiment, run_backtest_resp.json()


def test_statistical_validation_v1_route_returns_report():
    bars = _seed_bars_and_features()
    experiment, backtest = _create_and_run_experiment_and_backtest(bars)

    resp = client.get(f"/api/backtests/{backtest['id']}/statistical-validation", params={"primary_window_bars": 1})
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["experiment_id"] == experiment["id"]
    assert report["backtest_id"] == backtest["id"]
    assert report["primary_window_bars"] == 1
    assert "primary_permutation_test" in report
    assert "primary_effect_size" in report


def test_statistical_validation_v2_route_returns_report():
    bars = _seed_bars_and_features()
    experiment, backtest = _create_and_run_experiment_and_backtest(bars)

    resp = client.get(f"/api/backtests/{backtest['id']}/statistical-validation-v2", params={"primary_window_bars": 1})
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["experiment_id"] == experiment["id"]
    assert report["backtest_id"] == backtest["id"]
    assert "method_a_mean_difference" in report
    assert "method_b_mean_difference" in report
    assert "power_analysis" in report


def test_statistical_validation_404_for_missing_backtest():
    assert client.get("/api/backtests/nonexistent/statistical-validation").status_code == 404
    assert client.get("/api/backtests/nonexistent/statistical-validation-v2").status_code == 404


def test_statistical_validation_400_for_window_not_in_backtest():
    bars = _seed_bars_and_features()
    _experiment, backtest = _create_and_run_experiment_and_backtest(bars)

    resp = client.get(f"/api/backtests/{backtest['id']}/statistical-validation", params={"primary_window_bars": 999})
    assert resp.status_code == 400
