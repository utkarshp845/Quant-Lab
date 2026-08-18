"""End-to-end tests for OOS Statistical Review V1's HTTP routes
(app/api/oos_statistical_review.py). Same isolated-throwaway-database
convention as tests/test_oos_evidence_api.py.

Covers requirement 13's "API" list: correct HTTP behavior, nonexistent
experiment/review rejected, review cannot be mutated (no endpoint
exists to do so), and lifecycle is not incorrectly changed by the
statistical review.
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
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_oos_statistical_review.db"))


SYMBOL, TIMEFRAME, PROVIDER = "TSLA", "5m", "csv"
DEVELOPMENT_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
DEVELOPMENT_END = datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)
HOLDOUT_1_START, HOLDOUT_1_END = datetime(2024, 1, 2, tzinfo=timezone.utc), datetime(2024, 1, 2, 4, 0, tzinfo=timezone.utc)
HOLDOUT_2_START, HOLDOUT_2_END = datetime(2024, 1, 3, tzinfo=timezone.utc), datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc)
HOLDOUT_3_START, HOLDOUT_3_END = datetime(2024, 1, 4, tzinfo=timezone.utc), datetime(2024, 1, 4, 4, 0, tzinfo=timezone.utc)

_CONDITION = [{"feature_id": "price.return_5m", "operator": ">", "value": 0.0}]


def _bars(start: datetime, count: int, *, seed: int, base_price=100.0) -> list[HistoricalBar]:
    import numpy as np

    rng = np.random.default_rng(seed)
    price = base_price
    bars = []
    for i in range(count):
        price = max(1.0, price * (1 + rng.normal(0, 0.001)))
        bars.append(
            HistoricalBar(
                symbol=SYMBOL, timestamp=start + timedelta(minutes=5 * i), open=price, high=price + 0.1,
                low=price - 0.1, close=price, volume=1_000, provider=PROVIDER, timeframe=TIMEFRAME,
            )
        )
    return bars


def _seed_bars():
    development_bars = _bars(DEVELOPMENT_START, 288, seed=1)
    save_bars(development_bars)
    for i, holdout_start in enumerate((HOLDOUT_1_START, HOLDOUT_2_START, HOLDOUT_3_START)):
        save_bars(_bars(holdout_start, 48, seed=10 + i, base_price=development_bars[-1].close))


def _create_partition(*, holdout_start, holdout_end, **overrides) -> dict:
    fields = {
        "symbol": SYMBOL, "timeframe": TIMEFRAME, "provider": PROVIDER,
        "development_start": DEVELOPMENT_START.isoformat(), "development_end": DEVELOPMENT_END.isoformat(),
        "holdout_start": holdout_start.isoformat(), "holdout_end": holdout_end.isoformat(),
    }
    fields.update(overrides)
    response = client.post("/api/oos/partitions", json=fields)
    assert response.status_code == 200, response.text
    return response.json()


def _create_experiment(**overrides) -> dict:
    fields = {
        "name": "OOS Statistical Review API Test",
        "hypothesis": "A positive 5m return is followed by another positive 5m return.",
        "symbol": SYMBOL, "start_date": "2024-01-01", "end_date": "2024-01-01",
        "timeframe": TIMEFRAME, "provider": PROVIDER, "conditions": _CONDITION,
        "outcome": {"metric": "forward_return", "horizon_minutes": 15, "operator": ">", "threshold": -999.0},
    }
    fields.update(overrides)
    response = client.post("/api/research/experiments", json=fields)
    assert response.status_code == 200, response.text
    return response.json()


def _frozen_experiment_with_multiple_periods(n_periods: int = 3) -> dict:
    windows = [(HOLDOUT_1_START, HOLDOUT_1_END), (HOLDOUT_2_START, HOLDOUT_2_END), (HOLDOUT_3_START, HOLDOUT_3_END)][:n_periods]
    partitions = [_create_partition(holdout_start=s, holdout_end=e) for s, e in windows]

    experiment = _create_experiment()
    link = client.post(f"/api/research/experiments/{experiment['id']}/oos-partition", json={"oos_partition_id": partitions[0]["id"]})
    assert link.status_code == 200, link.text
    freeze = client.post(f"/api/research/experiments/{experiment['id']}/freeze")
    assert freeze.status_code == 200, freeze.text
    experiment = freeze.json()

    first_eval = client.post(f"/api/research/experiments/{experiment['id']}/oos-evaluate")
    assert first_eval.status_code == 200, first_eval.text

    for partition in partitions[1:]:
        register = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": partition["id"]})
        assert register.status_code == 200, register.text
        evaluate = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods/{partition['id']}/evaluate")
        assert evaluate.status_code == 200, evaluate.text

    return experiment


class TestCreateReview:
    def test_creating_a_review_succeeds(self):
        _seed_bars()
        experiment = _frozen_experiment_with_multiple_periods()

        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-statistical-review")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["experiment_id"] == experiment["id"]
        assert body["hypothesis_hash"] == experiment["hypothesis_hash"]
        assert body["primary_window_bars"] > 0
        assert body["verdict"] in {"supported", "not_supported", "inconclusive", "insufficient_data"}
        assert len(body["included_evaluation_ids"]) == 3
        assert body["seed"] == 1337
        assert body["n_resamples"] == 10_000

    def test_request_body_is_completely_ignored(self):
        """No caller-supplied config (seed/n_resamples/anything else)
        can influence the review -- the identical 'nothing here is
        configurable by the caller' proof app/api/oos_evaluation.py's
        own test already established."""
        _seed_bars()
        experiment = _frozen_experiment_with_multiple_periods()

        adversarial_body = {"seed": 1, "n_resamples": 10, "primary_window_bars": 999}
        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-statistical-review", json=adversarial_body)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["seed"] == 1337  # NOT 1
        assert body["n_resamples"] == 10_000  # NOT 10

    def test_never_frozen_experiment_is_409(self):
        experiment = _create_experiment()
        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-statistical-review")
        assert response.status_code == 409

    def test_unknown_experiment_is_404(self):
        assert client.post("/api/research/experiments/does-not-exist/oos-statistical-review").status_code == 404

    def test_review_does_not_change_lifecycle(self):
        """Requirement: an OOS statistical review is pure analysis --
        it must never transition, or otherwise touch, the experiment's
        own lifecycle_state."""
        _seed_bars()
        experiment = _frozen_experiment_with_multiple_periods()
        before = client.get(f"/api/research/experiments/{experiment['id']}").json()["lifecycle_state"]

        client.post(f"/api/research/experiments/{experiment['id']}/oos-statistical-review")

        after = client.get(f"/api/research/experiments/{experiment['id']}").json()["lifecycle_state"]
        assert before == after == "oos_evaluated"

    def test_underlying_oos_evaluations_are_unchanged_after_a_review(self):
        _seed_bars()
        experiment = _frozen_experiment_with_multiple_periods()
        before = client.get(f"/api/research/experiments/{experiment['id']}/oos-evaluations").json()

        client.post(f"/api/research/experiments/{experiment['id']}/oos-statistical-review")

        after = client.get(f"/api/research/experiments/{experiment['id']}/oos-evaluations").json()
        assert before == after


class TestReadRoutes:
    def test_list_and_get_a_review(self):
        _seed_bars()
        experiment = _frozen_experiment_with_multiple_periods()
        created = client.post(f"/api/research/experiments/{experiment['id']}/oos-statistical-review").json()

        listed = client.get(f"/api/research/experiments/{experiment['id']}/oos-statistical-reviews")
        assert listed.status_code == 200
        assert [r["id"] for r in listed.json()] == [created["id"]]

        fetched = client.get(f"/api/research/oos-statistical-reviews/{created['id']}")
        assert fetched.status_code == 200
        assert fetched.json() == created

    def test_running_the_review_twice_produces_two_immutable_records(self):
        _seed_bars()
        experiment = _frozen_experiment_with_multiple_periods()
        first = client.post(f"/api/research/experiments/{experiment['id']}/oos-statistical-review").json()
        second = client.post(f"/api/research/experiments/{experiment['id']}/oos-statistical-review").json()

        assert first["id"] != second["id"]
        assert first["verdict"] == second["verdict"]
        assert first["sample_sizes"] == second["sample_sizes"]

        # The first review is byte-identical after the second one runs.
        reloaded_first = client.get(f"/api/research/oos-statistical-reviews/{first['id']}")
        assert reloaded_first.json() == first

        history = client.get(f"/api/research/experiments/{experiment['id']}/oos-statistical-reviews").json()
        assert {r["id"] for r in history} == {first["id"], second["id"]}

    def test_unknown_review_is_404(self):
        assert client.get("/api/research/oos-statistical-reviews/does-not-exist").status_code == 404

    def test_listing_reviews_for_an_unknown_experiment_is_404(self):
        assert client.get("/api/research/experiments/does-not-exist/oos-statistical-reviews").status_code == 404

    def test_listing_reviews_before_any_exist_is_an_empty_list(self):
        _seed_bars()
        experiment = _frozen_experiment_with_multiple_periods()
        response = client.get(f"/api/research/experiments/{experiment['id']}/oos-statistical-reviews")
        assert response.status_code == 200
        assert response.json() == []


class TestReviewCannotBeMutated:
    def test_no_put_patch_or_delete_route_exists_for_a_review(self):
        _seed_bars()
        experiment = _frozen_experiment_with_multiple_periods()
        created = client.post(f"/api/research/experiments/{experiment['id']}/oos-statistical-review").json()

        assert client.put(f"/api/research/oos-statistical-reviews/{created['id']}", json={}).status_code == 405
        assert client.patch(f"/api/research/oos-statistical-reviews/{created['id']}", json={}).status_code == 405
        assert client.delete(f"/api/research/oos-statistical-reviews/{created['id']}").status_code == 405


class TestInsufficientDataThroughTheRealApi:
    def test_a_single_always_true_period_yields_insufficient_data(self):
        """One period, an always-true condition -> one giant episode --
        far below the formal-test threshold -- verdict must be
        INSUFFICIENT_DATA, never a fabricated p-value."""
        _seed_bars()
        partition = _create_partition(holdout_start=HOLDOUT_1_START, holdout_end=HOLDOUT_1_END)
        experiment = _create_experiment(conditions=[{"feature_id": "price.return_5m", "operator": ">", "value": -999.0}])
        client.post(f"/api/research/experiments/{experiment['id']}/oos-partition", json={"oos_partition_id": partition["id"]})
        experiment = client.post(f"/api/research/experiments/{experiment['id']}/freeze").json()
        client.post(f"/api/research/experiments/{experiment['id']}/oos-evaluate")

        review = client.post(f"/api/research/experiments/{experiment['id']}/oos-statistical-review").json()
        assert review["verdict"] == "insufficient_data"
        assert review["method_a_test"] is None
        assert review["method_b_test"] is None
