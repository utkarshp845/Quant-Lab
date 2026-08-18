"""End-to-end tests for OOS Evaluation v1's HTTP routes
(app/api/oos_evaluation.py). Same isolated-throwaway-database
convention as tests/test_experiment_freeze_api.py."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.market_data import HistoricalBar
from app.storage.historical_bar_repository import save_bars

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_oos_evaluation.db"))


SYMBOL, TIMEFRAME, PROVIDER = "TSLA", "5m", "csv"
DEVELOPMENT_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
HOLDOUT_START = datetime(2024, 1, 2, tzinfo=timezone.utc)
HOLDOUT_END = datetime(2024, 1, 2, 4, 0, tzinfo=timezone.utc)
DEVELOPMENT_END = HOLDOUT_START - timedelta(microseconds=1)


def _bars(start: datetime, count: int, *, base_price=100.0, price_step=0.01) -> list[HistoricalBar]:
    return [
        HistoricalBar(
            symbol=SYMBOL, timestamp=start + timedelta(minutes=5 * i),
            open=base_price + i * price_step, high=base_price + i * price_step + 0.05,
            low=base_price + i * price_step - 0.05, close=base_price + i * price_step,
            volume=1_000, provider=PROVIDER, timeframe=TIMEFRAME,
        )
        for i in range(count)
    ]


def _seed_bars():
    development_bars = _bars(DEVELOPMENT_START, 288)
    holdout_bars = _bars(HOLDOUT_START, 48, base_price=development_bars[-1].close + 0.01)
    save_bars(development_bars)
    save_bars(holdout_bars)


def _create_partition(**overrides) -> dict:
    fields = {
        "symbol": SYMBOL, "timeframe": TIMEFRAME, "provider": PROVIDER,
        "development_start": DEVELOPMENT_START.isoformat(), "development_end": DEVELOPMENT_END.isoformat(),
        "holdout_start": HOLDOUT_START.isoformat(), "holdout_end": HOLDOUT_END.isoformat(),
    }
    fields.update(overrides)
    response = client.post("/api/oos/partitions", json=fields)
    assert response.status_code == 200, response.text
    return response.json()


def _create_experiment(**overrides) -> dict:
    fields = {
        "name": "OOS Eval API Test",
        "hypothesis": "SMA50 distance stays positive.",
        "symbol": SYMBOL,
        "start_date": "2024-01-01",
        "end_date": "2024-01-01",
        "timeframe": TIMEFRAME,
        "provider": PROVIDER,
        "conditions": [{"feature_id": "price_position.ma50_distance", "operator": ">", "value": -1.0}],
        "outcome": {"metric": "forward_return", "horizon_minutes": 5, "operator": ">", "threshold": -999.0},
    }
    fields.update(overrides)
    response = client.post("/api/research/experiments", json=fields)
    assert response.status_code == 200, response.text
    return response.json()


def _frozen_experiment_with_partition() -> tuple[dict, dict]:
    partition = _create_partition()
    experiment = _create_experiment()
    link = client.post(
        f"/api/research/experiments/{experiment['id']}/oos-partition", json={"oos_partition_id": partition["id"]}
    )
    assert link.status_code == 200, link.text
    freeze = client.post(f"/api/research/experiments/{experiment['id']}/freeze")
    assert freeze.status_code == 200, freeze.text
    return freeze.json(), partition


class TestSuccessfulEvaluation:
    def test_evaluating_a_frozen_linked_experiment_succeeds(self):
        _seed_bars()
        experiment, partition = _frozen_experiment_with_partition()

        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-evaluate")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "completed"
        assert body["experiment_id"] == experiment["id"]
        assert body["hypothesis_hash"] == experiment["hypothesis_hash"]
        assert body["oos_partition_id"] == partition["id"]
        assert body["signal_count"] > 0

    def test_evaluation_transitions_the_experiment_to_oos_evaluated(self):
        _seed_bars()
        experiment, _partition = _frozen_experiment_with_partition()

        client.post(f"/api/research/experiments/{experiment['id']}/oos-evaluate")

        reloaded = client.get(f"/api/research/experiments/{experiment['id']}").json()
        assert reloaded["lifecycle_state"] == "oos_evaluated"

    def test_request_body_is_completely_ignored(self):
        """Requirement 8: the endpoint must not accept parameters that
        redefine the hypothesis -- proven here by sending an
        adversarial body with different conditions/thresholds/
        symbol/etc. and confirming the response is identical to the
        no-body call (same signal_count/hypothesis_hash), i.e. nothing
        in the posted JSON had any effect at all."""
        _seed_bars()
        experiment, _partition = _frozen_experiment_with_partition()

        adversarial_body = {
            "conditions": [{"feature_id": "price.return_60m", "operator": "<", "value": -0.5}],
            "outcome": {"metric": "forward_return", "horizon_minutes": 60, "operator": "<", "threshold": -0.5},
            "symbol": "NVDA",
            "provider": "alpaca",
            "timeframe": "1h",
            "start_date": "1999-01-01",
            "end_date": "1999-01-01",
        }
        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-evaluate", json=adversarial_body)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["symbol"] == SYMBOL  # NOT "NVDA" from the adversarial body
        assert body["timeframe"] == TIMEFRAME  # NOT "1h"
        assert body["outcome_horizon_minutes"] == 5  # NOT 60 -- the frozen Outcome's own horizon
        assert body["hypothesis_hash"] == experiment["hypothesis_hash"]


class TestReEvaluation:
    def test_re_evaluating_an_already_oos_evaluated_experiment_creates_a_second_record(self):
        _seed_bars()
        experiment, _partition = _frozen_experiment_with_partition()

        first = client.post(f"/api/research/experiments/{experiment['id']}/oos-evaluate").json()
        second = client.post(f"/api/research/experiments/{experiment['id']}/oos-evaluate")

        assert second.status_code == 200, second.text
        second_body = second.json()
        assert second_body["id"] != first["id"]
        assert second_body["signal_count"] == first["signal_count"]
        assert second_body["results"] == first["results"]

        history = client.get(f"/api/research/experiments/{experiment['id']}/oos-evaluations").json()
        assert len(history) == 2
        assert {e["id"] for e in history} == {first["id"], second_body["id"]}

        reloaded = client.get(f"/api/research/experiments/{experiment['id']}").json()
        assert reloaded["lifecycle_state"] == "oos_evaluated"  # unchanged, not re-transitioned


class TestPreconditionRejections:
    def test_draft_experiment_is_rejected(self):
        experiment = _create_experiment()
        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-evaluate")
        assert response.status_code == 409

    def test_archived_experiment_is_rejected(self):
        _seed_bars()
        experiment, _partition = _frozen_experiment_with_partition()
        client.post(f"/api/research/experiments/{experiment['id']}/archive")

        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-evaluate")
        assert response.status_code == 409

    def test_frozen_experiment_without_a_linked_partition_is_rejected(self):
        experiment = _create_experiment()
        client.post(f"/api/research/experiments/{experiment['id']}/freeze")

        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-evaluate")
        assert response.status_code == 400

    def test_unknown_experiment_is_404(self):
        assert client.post("/api/research/experiments/does-not-exist/oos-evaluate").status_code == 404


class TestReadRoutes:
    def test_get_evaluation_and_its_signals(self):
        _seed_bars()
        experiment, _partition = _frozen_experiment_with_partition()
        created = client.post(f"/api/research/experiments/{experiment['id']}/oos-evaluate").json()

        fetched = client.get(f"/api/research/oos-evaluations/{created['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == created["id"]

        signals = client.get(f"/api/research/oos-evaluations/{created['id']}/signals")
        assert signals.status_code == 200
        assert len(signals.json()) == created["signal_count"]

    def test_unknown_evaluation_is_404(self):
        assert client.get("/api/research/oos-evaluations/does-not-exist").status_code == 404
        assert client.get("/api/research/oos-evaluations/does-not-exist/signals").status_code == 404

    def test_listing_evaluations_for_an_unknown_experiment_is_404(self):
        assert client.get("/api/research/experiments/does-not-exist/oos-evaluations").status_code == 404
