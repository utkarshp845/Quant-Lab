"""End-to-end tests for Backtesting v1's HTTP routes (app/api/backtesting.py):

    POST /backtests
    GET  /backtests
    GET  /backtests/{id}
    GET  /backtests/{id}/signals
    POST /backtests/{id}/run

Every test gets an isolated, throwaway SQLite file via the `isolated_db`
autouse fixture -- same convention as tests/test_research_api.py. A
Backtest always references a real Experiment, created through the real
POST /api/research/experiments route (never hand-built) -- proving
Backtesting v1's own "select an existing Research experiment" boundary
holds through the actual HTTP surface, not just at the engine level
(already covered by tests/test_backtest_engine.py).
"""

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
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_backtest.db"))


def _bar(symbol: str, timestamp: datetime, *, o: float, h: float, l: float, c: float, provider="csv", timeframe="5m") -> HistoricalBar:
    return HistoricalBar(symbol=symbol, timestamp=timestamp, open=o, high=h, low=l, close=c, volume=1_000, provider=provider, timeframe=timeframe)


def _seed_bars_and_features(bars: list[HistoricalBar], *, provider="csv", timeframe="5m") -> list[HistoricalBar]:
    save_bars(bars)
    records = compute_features(symbol=bars[0].symbol, timeframe=timeframe, provider=provider, bars=bars, calculated_at=datetime.now(timezone.utc))
    save_features(records)
    return bars


def _seed_signal_bars(symbol="TSLA", *, start=None, provider="csv") -> list[HistoricalBar]:
    """A small, hand-verifiable OHLC series (see
    tests/test_backtest_engine.py for the identical shape/reasoning):
    one -2% drop bar (a signal, since -2% <= -1%) entered at the NEXT
    bar's open, followed by a further rise -- exactly ONE qualifying,
    fully-measurable signal at windows [1, 2]."""
    start = start or datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
    ohlc = [
        (100.0, 100.0, 100.0, 100.0),  # 0
        (100.0, 100.0, 100.0, 100.0),  # 1
        (100.0, 100.0, 97.0, 98.0),  # 2  <- signal: return_5m = -2%
        (97.0, 99.0, 96.0, 97.5),  # 3  <- entry bar, entry_price = 97.0
        (97.5, 101.0, 97.0, 100.0),  # 4 <- window=1 outcome
        (100.0, 102.0, 99.0, 101.0),  # 5 <- window=2 outcome
    ]
    bars = [
        _bar(symbol, start + timedelta(minutes=5 * i), o=o, h=h, l=l, c=c, provider=provider)
        for i, (o, h, l, c) in enumerate(ohlc)
    ]
    return _seed_bars_and_features(bars, provider=provider)


def _experiment_request(**overrides) -> dict:
    payload = {
        "name": "TSLA Early Selling Continuation",
        "hypothesis": "Declines >= 1% in 5m keep declining in the next 5m.",
        "symbol": "TSLA",
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
        "timeframe": "5m",
        "provider": "csv",
        "conditions": [{"feature_id": "price.return_5m", "operator": "<=", "value": -0.01}],
        "outcome": {"metric": "forward_return", "horizon_minutes": 5, "operator": "<=", "threshold": -0.005},
    }
    payload.update(overrides)
    return payload


def _create_experiment(**overrides) -> dict:
    resp = client.post("/api/research/experiments", json=_experiment_request(**overrides))
    assert resp.status_code == 200
    return resp.json()


class TestCreateBacktest:
    def test_creates_a_draft_backtest_referencing_the_experiment(self):
        experiment = _create_experiment()

        resp = client.post("/api/backtests", json={"experiment_id": experiment["id"]})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "draft"
        assert body["experiment_id"] == experiment["id"]
        assert body["symbol"] == experiment["symbol"]
        assert body["timeframe"] == experiment["timeframe"]
        assert body["provider"] == experiment["provider"]
        assert body["feature_contract_version"] == experiment["feature_contract_version"]
        assert body["completed_at"] is None
        assert body["results"] is None

    def test_defaults_windows_to_5_15_30_60(self):
        experiment = _create_experiment()

        resp = client.post("/api/backtests", json={"experiment_id": experiment["id"]})

        assert resp.json()["windows"] == [5, 15, 30, 60]

    def test_custom_windows_are_accepted_and_sorted(self):
        experiment = _create_experiment()

        resp = client.post("/api/backtests", json={"experiment_id": experiment["id"], "windows": [30, 1, 10]})

        assert resp.status_code == 200
        assert resp.json()["windows"] == [1, 10, 30]

    def test_a_missing_experiment_id_returns_404(self):
        resp = client.post("/api/backtests", json={"experiment_id": "does-not-exist"})
        assert resp.status_code == 404

    def test_duplicate_windows_are_rejected_at_the_model_level(self):
        experiment = _create_experiment()

        resp = client.post("/api/backtests", json={"experiment_id": experiment["id"], "windows": [5, 5]})

        assert resp.status_code == 422

    def test_a_non_positive_window_is_rejected_at_the_model_level(self):
        experiment = _create_experiment()

        resp = client.post("/api/backtests", json={"experiment_id": experiment["id"], "windows": [5, 0]})

        assert resp.status_code == 422


class TestGetAndListBacktests:
    def test_get_by_id_returns_what_was_created(self):
        experiment = _create_experiment()
        created = client.post("/api/backtests", json={"experiment_id": experiment["id"]}).json()

        resp = client.get(f"/api/backtests/{created['id']}")

        assert resp.status_code == 200
        assert resp.json() == created

    def test_get_missing_id_returns_404(self):
        resp = client.get("/api/backtests/does-not-exist")
        assert resp.status_code == 404

    def test_list_returns_every_created_backtest(self):
        experiment = _create_experiment()
        first = client.post("/api/backtests", json={"experiment_id": experiment["id"]}).json()
        second = client.post("/api/backtests", json={"experiment_id": experiment["id"], "windows": [1]}).json()

        resp = client.get("/api/backtests")

        assert resp.status_code == 200
        ids = {b["id"] for b in resp.json()}
        assert {first["id"], second["id"]} <= ids

    def test_list_filters_by_experiment_id(self):
        experiment_a = _create_experiment(name="A")
        experiment_b = _create_experiment(name="B")
        backtest_a = client.post("/api/backtests", json={"experiment_id": experiment_a["id"]}).json()
        client.post("/api/backtests", json={"experiment_id": experiment_b["id"]})

        resp = client.get(f"/api/backtests?experiment_id={experiment_a['id']}")

        assert resp.status_code == 200
        ids = {b["id"] for b in resp.json()}
        assert ids == {backtest_a["id"]}


class TestRunBacktest:
    def test_run_missing_id_returns_404(self):
        resp = client.post("/api/backtests/does-not-exist/run")
        assert resp.status_code == 404

    def test_running_finds_the_seeded_signal_and_completes(self):
        _seed_signal_bars()
        experiment = _create_experiment()
        created = client.post("/api/backtests", json={"experiment_id": experiment["id"], "windows": [1, 2]}).json()

        resp = client.post(f"/api/backtests/{created['id']}/run")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["completed_at"] is not None

        windows = {w["window_bars"]: w for w in body["results"]["windows"]}
        assert windows[1]["signal_count"] == 1
        assert windows[1]["win_count"] == 1
        assert windows[1]["win_rate"] == pytest.approx(1.0)
        assert windows[1]["mean_return"] == pytest.approx(0.030927835, abs=1e-6)
        assert windows[1]["std_dev_return"] is None  # a single observation

        assert windows[2]["signal_count"] == 1
        assert windows[2]["mean_return"] == pytest.approx(0.041237113, abs=1e-6)

    def test_running_with_no_matching_bars_completes_with_zero_signals(self):
        """An empty dataset is a legitimate, non-error outcome -- not a
        FAILED run."""
        experiment = _create_experiment()
        created = client.post("/api/backtests", json={"experiment_id": experiment["id"]}).json()

        resp = client.post(f"/api/backtests/{created['id']}/run")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        for window in body["results"]["windows"]:
            assert window["signal_count"] == 0
            assert window["win_rate"] is None

    def test_get_signals_returns_the_individual_inspectable_signal(self):
        _seed_signal_bars()
        experiment = _create_experiment()
        created = client.post("/api/backtests", json={"experiment_id": experiment["id"], "windows": [1, 2]}).json()
        client.post(f"/api/backtests/{created['id']}/run")

        resp = client.get(f"/api/backtests/{created['id']}/signals")

        assert resp.status_code == 200
        body = resp.json()
        assert body["backtest_id"] == created["id"]
        assert body["signal_count"] == 1

        signal = body["signals"][0]
        assert signal["experiment_id"] == experiment["id"]
        assert signal["symbol"] == "TSLA"
        assert signal["timeframe"] == "5m"
        assert signal["entry_price"] == pytest.approx(97.0)
        assert signal["feature_values"] == {"price.return_5m": pytest.approx(-0.02)}
        assert [o["window_bars"] for o in signal["outcomes"]] == [1, 2]

    def test_get_signals_for_a_backtest_that_has_not_run_yet_is_empty(self):
        experiment = _create_experiment()
        created = client.post("/api/backtests", json={"experiment_id": experiment["id"]}).json()

        resp = client.get(f"/api/backtests/{created['id']}/signals")

        assert resp.status_code == 200
        assert resp.json()["signal_count"] == 0

    def test_re_running_is_reproducible_not_cumulative(self):
        _seed_signal_bars()
        experiment = _create_experiment()
        created = client.post("/api/backtests", json={"experiment_id": experiment["id"], "windows": [1, 2]}).json()

        first_run = client.post(f"/api/backtests/{created['id']}/run").json()
        second_run = client.post(f"/api/backtests/{created['id']}/run").json()

        assert first_run["results"] == second_run["results"]

        signals = client.get(f"/api/backtests/{created['id']}/signals").json()
        assert signals["signal_count"] == 1  # not doubled by the second run

    def test_get_signals_missing_backtest_id_returns_404(self):
        resp = client.get("/api/backtests/does-not-exist/signals")
        assert resp.status_code == 404
