"""Tests for the Experiment Freeze & Provenance v1 additions to
app/storage/research_repository.py (set_oos_partition/freeze_experiment/
mark_oos_evaluated/mark_archived, and lifecycle fields round-tripping
through save_experiment/get_experiment) and the new
app/storage/experiment_freeze_repository.py (the immutable snapshot
table). Same explicit-db_path-per-test isolation convention as
tests/test_research_repository.py."""

from datetime import datetime, timezone

from app.models.experiment_freeze import ExperimentFreezeSnapshot
from app.models.research import (
    ConditionOperator,
    Experiment,
    ExperimentCreateRequest,
    ExperimentLifecycleState,
    FeatureCondition,
    FeatureConditionOperator,
    Outcome,
)
from app.research.lifecycle import build_freeze_snapshot, compute_hypothesis_hash
from app.storage.experiment_freeze_repository import get_snapshot, save_snapshot
from app.storage.research_repository import (
    freeze_experiment,
    get_experiment,
    mark_archived,
    mark_oos_evaluated,
    save_experiment,
    set_oos_partition,
)


def _experiment(**overrides) -> Experiment:
    request = ExperimentCreateRequest(
        name=overrides.pop("name", "TSLA Early Selling Continuation"),
        hypothesis=overrides.pop("hypothesis", "Declines >= 1% in 30m keep declining."),
        symbol=overrides.pop("symbol", "TSLA"),
        start_date=overrides.pop("start_date", "2024-01-01"),
        end_date=overrides.pop("end_date", "2024-06-30"),
        timeframe=overrides.pop("timeframe", "5m"),
        provider=overrides.pop("provider", "csv"),
        conditions=overrides.pop(
            "conditions", [FeatureCondition(feature_id="price.return_30m", operator=FeatureConditionOperator.LTE, value=-0.01)]
        ),
        outcome=overrides.pop(
            "outcome", Outcome(metric="forward_return", horizon_minutes=60, operator=ConditionOperator.LTE, threshold=-0.005)
        ),
    )
    return Experiment.new(request)


class TestLifecycleFieldsRoundTrip:
    def test_a_freshly_saved_experiment_defaults_to_draft(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)

        loaded = get_experiment(experiment.id, db_path=db_path)
        assert loaded.lifecycle_state == ExperimentLifecycleState.DRAFT
        assert loaded.oos_partition_id is None
        assert loaded.hypothesis_hash is None
        assert loaded.frozen_at is None
        assert loaded.archived_at is None


class TestSetOosPartition:
    def test_setting_a_partition_on_a_draft_experiment_succeeds(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)

        assert set_oos_partition(experiment.id, "partition-1", db_path=db_path) is True
        assert get_experiment(experiment.id, db_path=db_path).oos_partition_id == "partition-1"

    def test_setting_a_partition_on_a_frozen_experiment_is_refused(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)
        freeze_experiment(
            experiment.id, hypothesis_hash="h", frozen_at=datetime.now(timezone.utc), oos_partition_id=None, db_path=db_path
        )

        assert set_oos_partition(experiment.id, "partition-2", db_path=db_path) is False
        assert get_experiment(experiment.id, db_path=db_path).oos_partition_id is None


class TestFreezeExperiment:
    def test_freezing_a_draft_experiment_succeeds(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)
        frozen_at = datetime.now(timezone.utc)

        assert freeze_experiment(
            experiment.id, hypothesis_hash="h1", frozen_at=frozen_at, oos_partition_id="p1", db_path=db_path
        ) is True

        loaded = get_experiment(experiment.id, db_path=db_path)
        assert loaded.lifecycle_state == ExperimentLifecycleState.FROZEN
        assert loaded.hypothesis_hash == "h1"
        assert loaded.oos_partition_id == "p1"
        assert loaded.frozen_at == frozen_at

    def test_freezing_an_already_frozen_experiment_is_refused(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)
        freeze_experiment(
            experiment.id, hypothesis_hash="h1", frozen_at=datetime.now(timezone.utc), oos_partition_id=None, db_path=db_path
        )

        assert freeze_experiment(
            experiment.id, hypothesis_hash="h2", frozen_at=datetime.now(timezone.utc), oos_partition_id=None, db_path=db_path
        ) is False
        # the original freeze is untouched
        assert get_experiment(experiment.id, db_path=db_path).hypothesis_hash == "h1"


class TestMarkOosEvaluated:
    def test_a_frozen_experiment_can_move_to_oos_evaluated(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)
        freeze_experiment(
            experiment.id, hypothesis_hash="h1", frozen_at=datetime.now(timezone.utc), oos_partition_id=None, db_path=db_path
        )

        assert mark_oos_evaluated(experiment.id, oos_evaluated_at=datetime.now(timezone.utc), db_path=db_path) is True
        assert get_experiment(experiment.id, db_path=db_path).lifecycle_state == ExperimentLifecycleState.OOS_EVALUATED

    def test_a_draft_experiment_cannot_move_to_oos_evaluated(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)

        assert mark_oos_evaluated(experiment.id, oos_evaluated_at=datetime.now(timezone.utc), db_path=db_path) is False
        assert get_experiment(experiment.id, db_path=db_path).lifecycle_state == ExperimentLifecycleState.DRAFT


class TestMarkArchived:
    def test_a_frozen_experiment_can_be_archived(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)
        freeze_experiment(
            experiment.id, hypothesis_hash="h1", frozen_at=datetime.now(timezone.utc), oos_partition_id=None, db_path=db_path
        )
        archived_at = datetime.now(timezone.utc)

        assert mark_archived(experiment.id, archived_at=archived_at, db_path=db_path) is True
        loaded = get_experiment(experiment.id, db_path=db_path)
        assert loaded.lifecycle_state == ExperimentLifecycleState.ARCHIVED
        assert loaded.archived_at == archived_at

    def test_an_oos_evaluated_experiment_can_be_archived(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)
        freeze_experiment(
            experiment.id, hypothesis_hash="h1", frozen_at=datetime.now(timezone.utc), oos_partition_id=None, db_path=db_path
        )
        mark_oos_evaluated(experiment.id, oos_evaluated_at=datetime.now(timezone.utc), db_path=db_path)

        assert mark_archived(experiment.id, archived_at=datetime.now(timezone.utc), db_path=db_path) is True
        assert get_experiment(experiment.id, db_path=db_path).lifecycle_state == ExperimentLifecycleState.ARCHIVED

    def test_a_draft_experiment_cannot_be_archived(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)

        assert mark_archived(experiment.id, archived_at=datetime.now(timezone.utc), db_path=db_path) is False
        assert get_experiment(experiment.id, db_path=db_path).lifecycle_state == ExperimentLifecycleState.DRAFT


class TestFreezeSnapshotPersistence:
    def test_a_saved_snapshot_round_trips_exactly(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        frozen_at = datetime.now(timezone.utc)
        snapshot = build_freeze_snapshot(experiment, hypothesis_hash=compute_hypothesis_hash(experiment), frozen_at=frozen_at)

        save_snapshot(snapshot, db_path=db_path)
        loaded = get_snapshot(experiment.id, db_path=db_path)

        assert loaded == snapshot

    def test_getting_a_snapshot_for_an_unfrozen_experiment_returns_none(self, tmp_path):
        db_path = tmp_path / "research.db"
        assert get_snapshot("never-frozen", db_path=db_path) is None

    def test_the_snapshot_survives_after_the_live_row_moves_on(self, tmp_path):
        """requirement 5: the snapshot must answer "what hypothesis was
        evaluated" without depending on the live `experiments` row --
        proven here by advancing the live row all the way to ARCHIVED
        and confirming the snapshot is untouched."""
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)
        frozen_at = datetime.now(timezone.utc)
        snapshot = build_freeze_snapshot(experiment, hypothesis_hash=compute_hypothesis_hash(experiment), frozen_at=frozen_at)
        save_snapshot(snapshot, db_path=db_path)
        freeze_experiment(
            experiment.id, hypothesis_hash=snapshot.hypothesis_hash, frozen_at=frozen_at, oos_partition_id=None, db_path=db_path
        )

        mark_oos_evaluated(experiment.id, oos_evaluated_at=datetime.now(timezone.utc), db_path=db_path)
        mark_archived(experiment.id, archived_at=datetime.now(timezone.utc), db_path=db_path)

        reloaded_snapshot = get_snapshot(experiment.id, db_path=db_path)
        assert reloaded_snapshot == snapshot
        assert get_experiment(experiment.id, db_path=db_path).lifecycle_state == ExperimentLifecycleState.ARCHIVED
