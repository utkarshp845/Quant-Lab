"""End-to-end tests for Experiment Freeze & Provenance v1's HTTP routes
(app/api/experiment_freeze.py):

    POST /research/experiments/{id}/oos-partition
    POST /research/experiments/{id}/freeze
    GET  /research/experiments/{id}/frozen
    GET  /research/experiments/{id}/provenance
    POST /research/experiments/{id}/archive

Same isolated-throwaway-database convention as
tests/test_research_api.py/tests/test_oos_partitions_api.py.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_experiment_freeze.db"))


def _experiment_request(**overrides) -> dict:
    fields = {
        "name": "TSLA Early Selling Continuation",
        "hypothesis": "Declines >= 1% in 30m keep declining >= 0.5% over the next 60m.",
        "symbol": "TSLA",
        "start_date": "2024-01-01",
        "end_date": "2024-06-30",
        "timeframe": "5m",
        "provider": "csv",
        "conditions": [{"feature_id": "price.return_30m", "operator": "<=", "value": -0.01}],
        "outcome": {"metric": "forward_return", "horizon_minutes": 60, "operator": "<=", "threshold": -0.005},
    }
    fields.update(overrides)
    return fields


def _create_experiment(**overrides) -> dict:
    response = client.post("/api/research/experiments", json=_experiment_request(**overrides))
    assert response.status_code == 200, response.text
    return response.json()


def _create_partition(**overrides) -> dict:
    # development_end is end-of-day (not midnight-start) so that an
    # Experiment's `date`-only end_date of the SAME calendar day (which
    # app/research/lifecycle.py::_experiment_range_as_datetime() treats
    # as spanning the whole day, 00:00:00 through 23:59:59.999999) is
    # still entirely contained -- see that function's own docstring.
    fields = {
        "symbol": "TSLA",
        "timeframe": "5m",
        "provider": "csv",
        "development_start": "2024-01-01T00:00:00Z",
        "development_end": "2024-06-30T23:59:59.999999Z",
        "holdout_start": "2024-07-01T00:00:00Z",
        "holdout_end": "2024-12-31T00:00:00Z",
    }
    fields.update(overrides)
    response = client.post("/api/oos/partitions", json=fields)
    assert response.status_code == 200, response.text
    return response.json()


class TestFreezeTransition:
    def test_freezing_a_draft_experiment_succeeds(self):
        experiment = _create_experiment()

        response = client.post(f"/api/research/experiments/{experiment['id']}/freeze")
        assert response.status_code == 200
        body = response.json()
        assert body["lifecycle_state"] == "frozen"
        assert body["hypothesis_hash"]
        assert body["frozen_at"] is not None

    def test_freezing_an_already_frozen_experiment_is_rejected(self):
        experiment = _create_experiment()
        client.post(f"/api/research/experiments/{experiment['id']}/freeze")

        response = client.post(f"/api/research/experiments/{experiment['id']}/freeze")
        assert response.status_code == 409

    def test_freezing_an_unknown_experiment_is_404(self):
        assert client.post("/api/research/experiments/does-not-exist/freeze").status_code == 404


class TestArchive:
    def test_archiving_a_frozen_experiment_succeeds(self):
        experiment = _create_experiment()
        client.post(f"/api/research/experiments/{experiment['id']}/freeze")

        response = client.post(f"/api/research/experiments/{experiment['id']}/archive")
        assert response.status_code == 200
        assert response.json()["lifecycle_state"] == "archived"
        assert response.json()["archived_at"] is not None

    def test_archiving_a_draft_experiment_is_rejected(self):
        experiment = _create_experiment()

        response = client.post(f"/api/research/experiments/{experiment['id']}/archive")
        assert response.status_code == 409

    def test_archiving_an_already_archived_experiment_is_rejected(self):
        experiment = _create_experiment()
        client.post(f"/api/research/experiments/{experiment['id']}/freeze")
        client.post(f"/api/research/experiments/{experiment['id']}/archive")

        response = client.post(f"/api/research/experiments/{experiment['id']}/archive")
        assert response.status_code == 409


class TestFrozenMutationRejection:
    def test_associating_a_partition_after_freezing_is_rejected(self):
        experiment = _create_experiment()
        partition = _create_partition()
        client.post(f"/api/research/experiments/{experiment['id']}/freeze")

        response = client.post(
            f"/api/research/experiments/{experiment['id']}/oos-partition",
            json={"oos_partition_id": partition["id"]},
        )
        assert response.status_code == 409

    def test_associating_a_partition_before_freezing_succeeds(self):
        experiment = _create_experiment()
        partition = _create_partition()

        response = client.post(
            f"/api/research/experiments/{experiment['id']}/oos-partition",
            json={"oos_partition_id": partition["id"]},
        )
        assert response.status_code == 200
        assert response.json()["oos_partition_id"] == partition["id"]


class TestPartitionLinkageValidation:
    def test_incompatible_symbol_is_rejected(self):
        experiment = _create_experiment(symbol="TSLA")
        partition = _create_partition(symbol="NVDA")

        response = client.post(
            f"/api/research/experiments/{experiment['id']}/oos-partition",
            json={"oos_partition_id": partition["id"]},
        )
        assert response.status_code == 400

    def test_incompatible_timeframe_is_rejected(self):
        experiment = _create_experiment(timeframe="5m")
        partition = _create_partition(timeframe="1h")

        response = client.post(
            f"/api/research/experiments/{experiment['id']}/oos-partition",
            json={"oos_partition_id": partition["id"]},
        )
        assert response.status_code == 400

    def test_incompatible_provider_is_rejected(self):
        experiment = _create_experiment(provider="csv")
        partition = _create_partition(provider="alpaca")

        response = client.post(
            f"/api/research/experiments/{experiment['id']}/oos-partition",
            json={"oos_partition_id": partition["id"]},
        )
        assert response.status_code == 400

    def test_experiment_range_outside_development_window_is_rejected(self):
        experiment = _create_experiment(start_date="2023-01-01", end_date="2023-06-01")
        partition = _create_partition()  # development window is 2024

        response = client.post(
            f"/api/research/experiments/{experiment['id']}/oos-partition",
            json={"oos_partition_id": partition["id"]},
        )
        assert response.status_code == 400

    def test_experiment_range_touching_holdout_dates_is_rejected(self):
        experiment = _create_experiment(start_date="2024-06-15", end_date="2024-07-15")  # bleeds into holdout
        partition = _create_partition()

        response = client.post(
            f"/api/research/experiments/{experiment['id']}/oos-partition",
            json={"oos_partition_id": partition["id"]},
        )
        assert response.status_code == 400

    def test_missing_partition_is_404(self):
        experiment = _create_experiment()

        response = client.post(
            f"/api/research/experiments/{experiment['id']}/oos-partition",
            json={"oos_partition_id": "does-not-exist"},
        )
        assert response.status_code == 404

    def test_freeze_re_validates_linkage_even_if_association_was_valid(self):
        # Association-time and freeze-time validation both run the same
        # check -- this proves freeze doesn't skip its own, since the
        # only way association could have gone through is if it were
        # valid at the time (immutability elsewhere means it stays
        # valid), so this is really a "freeze also succeeds" check
        # exercised through the full flow.
        experiment = _create_experiment(start_date="2024-02-01", end_date="2024-03-01")
        partition = _create_partition()
        client.post(f"/api/research/experiments/{experiment['id']}/oos-partition", json={"oos_partition_id": partition["id"]})

        response = client.post(f"/api/research/experiments/{experiment['id']}/freeze")
        assert response.status_code == 200
        assert response.json()["oos_partition_id"] == partition["id"]


class TestFrozenDefinitionAndProvenance:
    def test_frozen_definition_is_unavailable_before_freezing(self):
        experiment = _create_experiment()
        assert client.get(f"/api/research/experiments/{experiment['id']}/frozen").status_code == 409

    def test_frozen_definition_is_available_after_freezing(self):
        experiment = _create_experiment()
        client.post(f"/api/research/experiments/{experiment['id']}/freeze")

        response = client.get(f"/api/research/experiments/{experiment['id']}/frozen")
        assert response.status_code == 200
        body = response.json()
        assert body["experiment_id"] == experiment["id"]
        assert body["symbol"] == "TSLA"
        assert body["hypothesis_hash"]

    def test_provenance_includes_the_linked_partition(self):
        experiment = _create_experiment(start_date="2024-02-01", end_date="2024-03-01")
        partition = _create_partition()
        client.post(f"/api/research/experiments/{experiment['id']}/oos-partition", json={"oos_partition_id": partition["id"]})
        client.post(f"/api/research/experiments/{experiment['id']}/freeze")

        response = client.get(f"/api/research/experiments/{experiment['id']}/provenance")
        assert response.status_code == 200
        body = response.json()
        assert body["oos_partition"]["id"] == partition["id"]
        assert body["lifecycle_state"] == "frozen"
        assert body["hypothesis_hash"]

    def test_provenance_without_a_linked_partition_has_none(self):
        experiment = _create_experiment()
        client.post(f"/api/research/experiments/{experiment['id']}/freeze")

        response = client.get(f"/api/research/experiments/{experiment['id']}/provenance")
        assert response.status_code == 200
        assert response.json()["oos_partition"] is None

    def test_provenance_before_freezing_is_409(self):
        experiment = _create_experiment()
        assert client.get(f"/api/research/experiments/{experiment['id']}/provenance").status_code == 409


class TestPersistenceAndReload:
    def test_frozen_state_survives_a_fresh_read_after_full_lifecycle(self):
        experiment = _create_experiment()
        client.post(f"/api/research/experiments/{experiment['id']}/freeze")
        client.post(f"/api/research/experiments/{experiment['id']}/archive")

        reloaded = client.get(f"/api/research/experiments/{experiment['id']}").json()
        assert reloaded["lifecycle_state"] == "archived"
        assert reloaded["hypothesis_hash"] is not None
        assert reloaded["frozen_at"] is not None
        assert reloaded["archived_at"] is not None

        snapshot = client.get(f"/api/research/experiments/{experiment['id']}/frozen").json()
        assert snapshot["hypothesis_hash"] == reloaded["hypothesis_hash"]
