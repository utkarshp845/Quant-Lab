"""End-to-end HTTP tests for Research Notebook v1 (app/api/
research_notebook.py) -- same isolated-throwaway-database convention as
tests/test_oos_evidence_api.py."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_research_notebook_api.db"))


def _create_experiment(**overrides) -> dict:
    body = {
        "name": "Test experiment",
        "hypothesis": "Free text hypothesis",
        "symbol": "TSLA",
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
        "timeframe": "5m",
        "provider": "csv",
        "conditions": [{"feature_id": "price.return_15m", "operator": "<=", "value": -0.005}],
        "outcome": {"metric": "forward_return", "horizon_minutes": 15, "operator": "<=", "threshold": 0.0},
    }
    body.update(overrides)
    resp = client.post("/api/research/experiments", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---- Observations ----------------------------------------------------


def test_create_and_get_observation():
    resp = client.post(
        "/api/research/observations",
        json={
            "symbol": "tsla",
            "description": "Sharp premarket gap down on high volume.",
            "observed_start": "2026-01-05T09:00:00Z",
            "observed_end": "2026-01-05T09:30:00Z",
        },
    )
    assert resp.status_code == 200, resp.text
    observation = resp.json()
    assert observation["symbol"] == "TSLA"

    fetched = client.get(f"/api/research/observations/{observation['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == observation


def test_get_observation_404():
    assert client.get("/api/research/observations/nonexistent").status_code == 404


def test_create_observation_blank_description_rejected():
    resp = client.post(
        "/api/research/observations",
        json={"symbol": "TSLA", "description": "  ", "observed_start": "2026-01-05T09:00:00Z", "observed_end": "2026-01-05T09:30:00Z"},
    )
    assert resp.status_code == 422


def test_list_observations_filters_by_symbol():
    client.post(
        "/api/research/observations",
        json={"symbol": "TSLA", "description": "a", "observed_start": "2026-01-01T00:00:00Z", "observed_end": "2026-01-01T01:00:00Z"},
    )
    client.post(
        "/api/research/observations",
        json={"symbol": "NVDA", "description": "b", "observed_start": "2026-01-01T00:00:00Z", "observed_end": "2026-01-01T01:00:00Z"},
    )
    resp = client.get("/api/research/observations", params={"symbol": "TSLA"})
    assert resp.status_code == 200
    assert all(o["symbol"] == "TSLA" for o in resp.json())


# ---- Decisions ---------------------------------------------------------


def test_decision_log_full_worked_example():
    experiment = _create_experiment(design_group_id="dg-1", candidate_label="C")

    proposal = client.post(
        "/api/research/decisions",
        json={
            "design_group_id": "dg-1",
            "decision": "Proposed candidates A/B/C/D",
            "reason": "Enumerated every conceptually valid VWAP-distance-normalized definition.",
            "selection_criteria": [],
            "information_available": ["sample_size", "conceptual_validity", "data_availability"],
            "outcome_data_available": False,
        },
    )
    assert proposal.status_code == 200, proposal.text

    selection = client.post(
        "/api/research/decisions",
        json={
            "design_group_id": "dg-1",
            "decision": "Selected Candidate C",
            "reason": "Largest viable sample among conceptually valid definitions.",
            "selection_criteria": ["sample_size"],
            "information_available": ["sample_size"],
            "outcome_data_available": False,
            "resulting_experiment_id": experiment["id"],
        },
    )
    assert selection.status_code == 200, selection.text
    assert selection.json()["outcome_data_available"] is False

    history = client.get("/api/research/design-groups/dg-1/decisions")
    assert history.status_code == 200
    entries = history.json()
    assert len(entries) == 2
    assert entries[0]["decision"] == "Proposed candidates A/B/C/D"
    assert entries[1]["resulting_experiment_id"] == experiment["id"]


def test_decision_resulting_experiment_must_exist():
    resp = client.post(
        "/api/research/decisions",
        json={
            "design_group_id": "dg-1", "decision": "x", "reason": "y",
            "outcome_data_available": True, "resulting_experiment_id": "nonexistent",
        },
    )
    assert resp.status_code == 404


def test_empty_design_group_returns_empty_list():
    assert client.get("/api/research/design-groups/nonexistent/decisions").json() == []


# ---- Conclusions ---------------------------------------------------------


def _conclusion_body(**overrides) -> dict:
    body = {
        "state": "inconclusive",
        "statement": "No clear edge over baseline at the primary horizon.",
        "references_hypothesis": "Downside-momentum-on-volume continuation.",
        "references_sample": "63 independent episodes.",
        "references_baseline": "Unconditional TSLA 15m forward return.",
        "references_outcomes": "Mean -0.03%, median -0.06%.",
        "references_statistical_validation": "p=0.25 (Method A), p=0.41 (Method B).",
        "limitations": "Single symbol, single development window.",
    }
    body.update(overrides)
    return body


def test_create_and_list_conclusions_newest_first():
    experiment = _create_experiment()

    first = client.post(f"/api/research/experiments/{experiment['id']}/conclusions", json=_conclusion_body())
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/research/experiments/{experiment['id']}/conclusions", json=_conclusion_body(state="weakened")
    )
    assert second.status_code == 200

    listed = client.get(f"/api/research/experiments/{experiment['id']}/conclusions")
    assert listed.status_code == 200
    entries = listed.json()
    assert [e["id"] for e in entries] == [second.json()["id"], first.json()["id"]]


def test_conclusion_requires_every_reference_field():
    experiment = _create_experiment()
    resp = client.post(
        f"/api/research/experiments/{experiment['id']}/conclusions",
        json=_conclusion_body(references_baseline=""),
    )
    assert resp.status_code == 422


def test_conclusion_on_nonexistent_experiment_404():
    resp = client.post("/api/research/experiments/nonexistent/conclusions", json=_conclusion_body())
    assert resp.status_code == 404


# ---- Versions ---------------------------------------------------------


def test_version_tree_and_diff_from_parent():
    parent = _create_experiment(name="Experiment 2", design_group_id="dg-2", candidate_label="C", version_label="2")
    child_a = _create_experiment(
        name="Experiment 2A",
        parent_experiment_id=parent["id"],
        version_label="2A",
        conditions=[{"feature_id": "price.return_15m", "operator": "<=", "value": -0.01}],
    )
    child_b = _create_experiment(
        name="Experiment 2B", parent_experiment_id=parent["id"], version_label="2B",
    )

    resp = client.get(f"/api/research/experiments/{child_a['id']}/versions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["root_id"] == parent["id"]
    ids = {v["id"] for v in body["versions"]}
    assert ids == {parent["id"], child_a["id"], child_b["id"]}

    diff_fields = {d["field"] for d in body["diff_from_parent"]}
    assert "conditions" in diff_fields  # the only field child_a actually changed vs. its parent


def test_root_experiment_has_no_diff_from_parent():
    solo = _create_experiment(name="Standalone")
    resp = client.get(f"/api/research/experiments/{solo['id']}/versions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["root_id"] == solo["id"]
    assert body["versions"] == [
        {
            "id": solo["id"], "name": solo["name"], "version_label": None, "candidate_label": None,
            "design_group_id": None, "parent_experiment_id": None, "lifecycle_state": "draft",
            "created_at": solo["created_at"],
        }
    ]
    assert body["diff_from_parent"] is None


def test_versions_404_for_nonexistent_experiment():
    assert client.get("/api/research/experiments/nonexistent/versions").status_code == 404
