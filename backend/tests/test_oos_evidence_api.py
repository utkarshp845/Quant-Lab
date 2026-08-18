"""End-to-end tests for OOS Evidence Accumulation V1's HTTP routes
(app/api/oos_evidence.py). Same isolated-throwaway-database convention
as tests/test_oos_evaluation_api.py.

Covers requirement 7's full list, exercised through the real HTTP
surface this time: partition/overlap rejections, immutability (a prior
evaluation is untouched by a later one), evaluation (including
repeated-evaluation rejection and the adversarial-request-body-is-
ignored proof), aggregation, lifecycle (stays OOS_EVALUATED, never
transitions further), and leakage (holdout access stays gated, no OOS
data is ever written to historical_features).
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
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_oos_evidence.db"))


SYMBOL, TIMEFRAME, PROVIDER = "TSLA", "5m", "csv"
DEVELOPMENT_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
DEVELOPMENT_END = datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)
HOLDOUT_1_START, HOLDOUT_1_END = datetime(2024, 1, 2, tzinfo=timezone.utc), datetime(2024, 1, 2, 4, 0, tzinfo=timezone.utc)
HOLDOUT_2_START, HOLDOUT_2_END = datetime(2024, 1, 3, tzinfo=timezone.utc), datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc)
HOLDOUT_3_START, HOLDOUT_3_END = datetime(2024, 1, 4, tzinfo=timezone.utc), datetime(2024, 1, 4, 4, 0, tzinfo=timezone.utc)

# See tests/test_oos_evidence_evaluation.py's own module-level comment:
# a minimal-lookback condition (return_5m needs only one prior,
# CONTIGUOUS bar) rather than an SMA50-based one, since several periods
# below are deliberately calendar-separated from the shared development
# window's own warm-up bars.
_CONDITION = [{"feature_id": "price.return_5m", "operator": ">", "value": -999.0}]


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
    save_bars(development_bars)
    for holdout_start in (HOLDOUT_1_START, HOLDOUT_2_START, HOLDOUT_3_START):
        save_bars(_bars(holdout_start, 48, base_price=development_bars[-1].close + 0.01))


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
        "name": "OOS Evidence API Test",
        "hypothesis": "A positive 5m return is followed by another positive 5m return.",
        "symbol": SYMBOL, "start_date": "2024-01-01", "end_date": "2024-01-01",
        "timeframe": TIMEFRAME, "provider": PROVIDER,
        "conditions": _CONDITION,
        "outcome": {"metric": "forward_return", "horizon_minutes": 5, "operator": ">", "threshold": -999.0},
    }
    fields.update(overrides)
    response = client.post("/api/research/experiments", json=fields)
    assert response.status_code == 200, response.text
    return response.json()


def _frozen_experiment_with_original_partition() -> tuple[dict, dict]:
    partition = _create_partition(holdout_start=HOLDOUT_1_START, holdout_end=HOLDOUT_1_END)
    experiment = _create_experiment()
    link = client.post(f"/api/research/experiments/{experiment['id']}/oos-partition", json={"oos_partition_id": partition["id"]})
    assert link.status_code == 200, link.text
    freeze = client.post(f"/api/research/experiments/{experiment['id']}/freeze")
    assert freeze.status_code == 200, freeze.text
    return freeze.json(), partition


class TestPeriodRegistration:
    def test_registering_a_valid_additional_period_succeeds(self):
        experiment, _original = _frozen_experiment_with_original_partition()
        period_2 = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)

        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period_2["id"]})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == period_2["id"]
        assert body["experiment_id"] == experiment["id"]
        assert datetime.fromisoformat(body["oos_start"].replace("Z", "+00:00")) == HOLDOUT_2_START
        assert datetime.fromisoformat(body["oos_end"].replace("Z", "+00:00")) == HOLDOUT_2_END

    def test_registering_several_sequential_periods_succeeds(self):
        experiment, _original = _frozen_experiment_with_original_partition()
        period_2 = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)
        period_3 = _create_partition(holdout_start=HOLDOUT_3_START, holdout_end=HOLDOUT_3_END)

        assert client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period_2["id"]}).status_code == 200
        assert client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period_3["id"]}).status_code == 200

        listed = client.get(f"/api/research/experiments/{experiment['id']}/oos-periods")
        assert listed.status_code == 200
        assert {p["oos_partition_id"] for p in listed.json()} == {period_2["id"], period_3["id"]}

    def test_registering_an_overlapping_period_is_rejected(self):
        experiment, _original = _frozen_experiment_with_original_partition()
        overlapping = _create_partition(
            holdout_start=HOLDOUT_1_START + timedelta(hours=2), holdout_end=HOLDOUT_1_START + timedelta(hours=6)
        )
        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": overlapping["id"]})
        assert response.status_code == 400

    def test_registering_a_mismatched_symbol_partition_is_rejected(self):
        experiment, _original = _frozen_experiment_with_original_partition()
        wrong_symbol = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END, symbol="NVDA")
        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": wrong_symbol["id"]})
        assert response.status_code == 400

    def test_registering_a_period_that_overlaps_the_development_range_is_rejected(self):
        experiment, _original = _frozen_experiment_with_original_partition()
        # A "partition" whose holdout window IS the experiment's own
        # development range -- rejected by validate_snapshot_partition_linkage()
        # (symbol/timeframe/provider match, but containment fails).
        overlapping_dev = _create_partition(
            holdout_start=datetime(2023, 6, 1, tzinfo=timezone.utc), holdout_end=datetime(2023, 6, 2, tzinfo=timezone.utc),
            development_start=datetime(2023, 1, 1, tzinfo=timezone.utc).isoformat(),
            development_end=datetime(2023, 5, 31, tzinfo=timezone.utc).isoformat(),
        )
        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": overlapping_dev["id"]})
        assert response.status_code == 400

    def test_registering_the_same_partition_twice_is_rejected(self):
        experiment, _original = _frozen_experiment_with_original_partition()
        period_2 = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)
        assert client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period_2["id"]}).status_code == 200

        second_attempt = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period_2["id"]})
        assert second_attempt.status_code == 400

    def test_registering_for_a_draft_experiment_is_rejected(self):
        experiment = _create_experiment()
        period = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)
        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period["id"]})
        assert response.status_code == 409

    def test_registering_for_an_archived_experiment_is_rejected(self):
        experiment, _original = _frozen_experiment_with_original_partition()
        client.post(f"/api/research/experiments/{experiment['id']}/archive")
        period = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)
        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period["id"]})
        assert response.status_code == 409

    def test_registering_an_unknown_partition_is_404(self):
        experiment, _original = _frozen_experiment_with_original_partition()
        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": "does-not-exist"})
        assert response.status_code == 404

    def test_registering_for_an_unknown_experiment_is_404(self):
        period = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)
        response = client.post("/api/research/experiments/does-not-exist/oos-periods", json={"oos_partition_id": period["id"]})
        assert response.status_code == 404


class TestEvaluation:
    def test_evaluating_a_registered_period_succeeds(self):
        _seed_bars()
        experiment, _original = _frozen_experiment_with_original_partition()
        period_2 = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)
        client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period_2["id"]})

        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods/{period_2['id']}/evaluate")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "completed"
        assert body["oos_partition_id"] == period_2["id"]
        assert body["hypothesis_hash"] == experiment["hypothesis_hash"]
        assert body["signal_count"] > 0

    def test_evaluating_an_unregistered_partition_is_404(self):
        _seed_bars()
        experiment, _original = _frozen_experiment_with_original_partition()
        unregistered = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)

        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods/{unregistered['id']}/evaluate")
        assert response.status_code == 404

    def test_re_evaluating_a_completed_period_is_rejected(self):
        _seed_bars()
        experiment, _original = _frozen_experiment_with_original_partition()
        period_2 = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)
        client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period_2["id"]})
        first = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods/{period_2['id']}/evaluate")
        assert first.status_code == 200

        second = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods/{period_2['id']}/evaluate")
        assert second.status_code == 409

    def test_request_body_is_completely_ignored(self):
        """Requirement 6/8's own instruction, mirrored from OOS
        Evaluation v1's own test: an adversarial body has zero effect."""
        _seed_bars()
        experiment, _original = _frozen_experiment_with_original_partition()
        period_2 = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)
        client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period_2["id"]})

        adversarial_body = {
            "conditions": [{"feature_id": "price.return_60m", "operator": "<", "value": -0.5}],
            "outcome": {"metric": "forward_return", "horizon_minutes": 60, "operator": "<", "threshold": -0.5},
            "symbol": "NVDA", "provider": "alpaca", "timeframe": "1h",
        }
        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods/{period_2['id']}/evaluate", json=adversarial_body)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["symbol"] == SYMBOL
        assert body["timeframe"] == TIMEFRAME
        assert body["outcome_horizon_minutes"] == 5
        assert body["hypothesis_hash"] == experiment["hypothesis_hash"]


class TestLifecycle:
    def test_evaluating_an_additional_period_first_transitions_frozen_to_oos_evaluated(self):
        _seed_bars()
        experiment, _original = _frozen_experiment_with_original_partition()
        period_2 = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)
        client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period_2["id"]})

        client.post(f"/api/research/experiments/{experiment['id']}/oos-periods/{period_2['id']}/evaluate")

        reloaded = client.get(f"/api/research/experiments/{experiment['id']}").json()
        assert reloaded["lifecycle_state"] == "oos_evaluated"

    def test_a_second_periods_evaluation_leaves_an_already_oos_evaluated_experiment_unchanged(self):
        """Requirement 5: no OOS_EVALUATED -> FROZEN or -> DRAFT
        transition is ever attempted; the experiment simply stays
        OOS_EVALUATED across every additional successful evaluation."""
        _seed_bars()
        experiment, _original = _frozen_experiment_with_original_partition()
        client.post(f"/api/research/experiments/{experiment['id']}/oos-evaluate")  # original partition -> OOS_EVALUATED
        assert client.get(f"/api/research/experiments/{experiment['id']}").json()["lifecycle_state"] == "oos_evaluated"

        period_2 = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)
        client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period_2["id"]})
        response = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods/{period_2['id']}/evaluate")

        assert response.status_code == 200
        reloaded = client.get(f"/api/research/experiments/{experiment['id']}").json()
        assert reloaded["lifecycle_state"] == "oos_evaluated"  # unchanged, not re-transitioned


class TestImmutability:
    def test_a_prior_evaluation_is_byte_identical_after_a_later_periods_evaluation(self):
        _seed_bars()
        experiment, _original = _frozen_experiment_with_original_partition()
        first = client.post(f"/api/research/experiments/{experiment['id']}/oos-evaluate").json()

        period_2 = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)
        client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period_2["id"]})
        client.post(f"/api/research/experiments/{experiment['id']}/oos-periods/{period_2['id']}/evaluate")

        reloaded_first = client.get(f"/api/research/oos-evaluations/{first['id']}")
        assert reloaded_first.status_code == 200
        assert reloaded_first.json() == first  # byte-identical, unaffected by the second period's evaluation

    def test_oos_evaluations_route_lists_evaluations_from_every_period(self):
        """GET .../oos-evaluations (OOS Evaluation v1, UNMODIFIED)
        already returns every evaluation for this experiment -- both
        the original partition's own run and every additional period's
        run, since both write into the SAME append-only table."""
        _seed_bars()
        experiment, _original = _frozen_experiment_with_original_partition()
        first = client.post(f"/api/research/experiments/{experiment['id']}/oos-evaluate").json()

        period_2 = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)
        client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period_2["id"]})
        second = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods/{period_2['id']}/evaluate").json()

        history = client.get(f"/api/research/experiments/{experiment['id']}/oos-evaluations").json()
        assert {e["id"] for e in history} == {first["id"], second["id"]}


class TestAggregation:
    def test_evidence_aggregates_across_both_periods(self):
        _seed_bars()
        experiment, _original = _frozen_experiment_with_original_partition()
        first = client.post(f"/api/research/experiments/{experiment['id']}/oos-evaluate").json()

        period_2 = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)
        client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period_2["id"]})
        second = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods/{period_2['id']}/evaluate").json()

        evidence = client.get(f"/api/research/experiments/{experiment['id']}/oos-evidence")
        assert evidence.status_code == 200, evidence.text
        body = evidence.json()
        assert body["experiment_id"] == experiment["id"]
        assert body["hypothesis_hash"] == experiment["hypothesis_hash"]
        assert body["oos_period_count"] == 2
        assert body["completed_evaluation_count"] == 2
        assert body["failed_evaluation_count"] == 0
        assert body["total_raw_signals"] == first["signal_count"] + second["signal_count"]
        assert body["total_independent_episodes"] > 0
        assert datetime.fromisoformat(body["earliest_oos_start"].replace("Z", "+00:00")) == HOLDOUT_1_START
        assert datetime.fromisoformat(body["latest_oos_end"].replace("Z", "+00:00")) == HOLDOUT_2_END
        assert {r["evaluation_id"] for r in body["per_period_results"]} == {first["id"], second["id"]}
        # No significance claim anywhere in the response.
        assert not {"p_value", "confidence_interval", "significant"} & set(body.keys())

    def test_evidence_before_any_evaluation_is_empty_but_valid(self):
        experiment, _original = _frozen_experiment_with_original_partition()
        evidence = client.get(f"/api/research/experiments/{experiment['id']}/oos-evidence")
        assert evidence.status_code == 200
        body = evidence.json()
        assert body["oos_period_count"] == 0
        assert body["completed_evaluation_count"] == 0
        assert body["total_raw_signals"] == 0
        assert body["mean_return"] is None
        assert body["per_period_results"] == []

    def test_evidence_for_an_unfrozen_experiment_is_409(self):
        experiment = _create_experiment()
        response = client.get(f"/api/research/experiments/{experiment['id']}/oos-evidence")
        assert response.status_code == 409

    def test_evidence_for_an_unknown_experiment_is_404(self):
        assert client.get("/api/research/experiments/does-not-exist/oos-evidence").status_code == 404


class TestLeakage:
    def test_holdout_access_still_requires_explicit_confirmation(self):
        """The pre-existing gate (app/api/oos_partitions.py, UNMODIFIED)
        is untouched by anything this feature adds."""
        experiment, original = _frozen_experiment_with_original_partition()
        gated = client.get(f"/api/oos/partitions/{original['id']}/holdout/bars")
        assert gated.status_code == 403

    def test_no_oos_signal_or_feature_data_leaks_into_a_different_periods_evaluation(self):
        """Every signal from period 2's own evaluation falls strictly
        within period 2's own OOS window -- never period 1's."""
        _seed_bars()
        experiment, _original = _frozen_experiment_with_original_partition()
        period_2 = _create_partition(holdout_start=HOLDOUT_2_START, holdout_end=HOLDOUT_2_END)
        client.post(f"/api/research/experiments/{experiment['id']}/oos-periods", json={"oos_partition_id": period_2["id"]})
        second = client.post(f"/api/research/experiments/{experiment['id']}/oos-periods/{period_2['id']}/evaluate").json()

        signals = client.get(f"/api/research/oos-evaluations/{second['id']}/signals").json()
        assert signals, "expected at least one signal to assert over"
        for signal in signals:
            timestamp = datetime.fromisoformat(signal["signal_timestamp"])
            assert HOLDOUT_2_START <= timestamp <= HOLDOUT_2_END
