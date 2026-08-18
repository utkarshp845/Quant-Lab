"""Tests for app/research/lifecycle.py: the state-transition table, the
deterministic hypothesis hash, and OOS-partition linkage validation --
all pure logic, no I/O, no database."""

from datetime import datetime, timezone

import pytest

from app.models.oos_partition import OOSPartition, OOSPartitionCreateRequest
from app.models.research import (
    ConditionOperator,
    Experiment,
    ExperimentCreateRequest,
    ExperimentLifecycleState,
    FeatureCondition,
    FeatureConditionOperator,
    Outcome,
)
from app.research.lifecycle import (
    InvalidLifecycleTransitionError,
    PartitionLinkageError,
    build_freeze_snapshot,
    canonicalize_hypothesis,
    compute_hypothesis_hash,
    validate_partition_linkage,
    validate_transition,
)


def _conditions(**overrides) -> list[FeatureCondition]:
    fields = {"feature_id": "price.return_30m", "operator": FeatureConditionOperator.LTE, "value": -0.01}
    fields.update(overrides)
    return [FeatureCondition(**fields)]


def _experiment(**overrides) -> Experiment:
    request = ExperimentCreateRequest(
        name=overrides.pop("name", "TSLA Early Selling Continuation"),
        hypothesis=overrides.pop("hypothesis", "Declines >= 1% in 30m keep declining."),
        symbol=overrides.pop("symbol", "TSLA"),
        start_date=overrides.pop("start_date", "2024-01-01"),
        end_date=overrides.pop("end_date", "2024-06-30"),
        timeframe=overrides.pop("timeframe", "5m"),
        provider=overrides.pop("provider", "csv"),
        conditions=overrides.pop("conditions", _conditions()),
        outcome=overrides.pop(
            "outcome", Outcome(metric="forward_return", horizon_minutes=60, operator=ConditionOperator.LTE, threshold=-0.005)
        ),
    )
    return Experiment.new(request)


def _partition(**overrides) -> OOSPartition:
    fields = {
        "symbol": "TSLA",
        "timeframe": "5m",
        "provider": "csv",
        "development_start": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "development_end": datetime(2024, 6, 30, tzinfo=timezone.utc),
        "holdout_start": datetime(2024, 7, 1, tzinfo=timezone.utc),
        "holdout_end": datetime(2024, 12, 31, tzinfo=timezone.utc),
    }
    fields.update(overrides)
    return OOSPartition.new(OOSPartitionCreateRequest(**fields))


class TestValidTransitions:
    @pytest.mark.parametrize(
        "current,target",
        [
            (ExperimentLifecycleState.DRAFT, ExperimentLifecycleState.FROZEN),
            (ExperimentLifecycleState.FROZEN, ExperimentLifecycleState.OOS_EVALUATED),
            (ExperimentLifecycleState.FROZEN, ExperimentLifecycleState.ARCHIVED),
            (ExperimentLifecycleState.OOS_EVALUATED, ExperimentLifecycleState.ARCHIVED),
        ],
    )
    def test_every_documented_transition_is_accepted(self, current, target):
        validate_transition(current, target)  # must not raise


class TestInvalidTransitions:
    @pytest.mark.parametrize(
        "current,target",
        [
            (ExperimentLifecycleState.DRAFT, ExperimentLifecycleState.OOS_EVALUATED),
            (ExperimentLifecycleState.DRAFT, ExperimentLifecycleState.ARCHIVED),
            (ExperimentLifecycleState.DRAFT, ExperimentLifecycleState.DRAFT),
            (ExperimentLifecycleState.FROZEN, ExperimentLifecycleState.DRAFT),
            (ExperimentLifecycleState.FROZEN, ExperimentLifecycleState.FROZEN),
            (ExperimentLifecycleState.OOS_EVALUATED, ExperimentLifecycleState.DRAFT),
            (ExperimentLifecycleState.OOS_EVALUATED, ExperimentLifecycleState.FROZEN),
            (ExperimentLifecycleState.OOS_EVALUATED, ExperimentLifecycleState.OOS_EVALUATED),
            (ExperimentLifecycleState.ARCHIVED, ExperimentLifecycleState.DRAFT),
            (ExperimentLifecycleState.ARCHIVED, ExperimentLifecycleState.FROZEN),
            (ExperimentLifecycleState.ARCHIVED, ExperimentLifecycleState.OOS_EVALUATED),
            (ExperimentLifecycleState.ARCHIVED, ExperimentLifecycleState.ARCHIVED),
        ],
    )
    def test_every_other_transition_is_rejected(self, current, target):
        with pytest.raises(InvalidLifecycleTransitionError):
            validate_transition(current, target)


class TestDeterministicHash:
    def test_identical_experiments_hash_identically(self):
        assert compute_hypothesis_hash(_experiment()) == compute_hypothesis_hash(_experiment())

    def test_two_independently_constructed_experiments_hash_identically(self):
        # Different ids and created_at (Experiment.new() assigns those
        # fresh every call) -- the hash must still match, since neither
        # is a research-defining field.
        a, b = _experiment(), _experiment()
        assert a.id != b.id
        assert compute_hypothesis_hash(a) == compute_hypothesis_hash(b)

    def test_condition_order_does_not_affect_the_hash(self):
        forward = _experiment(
            conditions=[
                FeatureCondition(feature_id="price.return_30m", operator=FeatureConditionOperator.LTE, value=-0.01),
                FeatureCondition(feature_id="volume.relative_volume", operator=FeatureConditionOperator.GT, value=1.5),
            ]
        )
        reversed_order = _experiment(
            conditions=[
                FeatureCondition(feature_id="volume.relative_volume", operator=FeatureConditionOperator.GT, value=1.5),
                FeatureCondition(feature_id="price.return_30m", operator=FeatureConditionOperator.LTE, value=-0.01),
            ]
        )
        assert compute_hypothesis_hash(forward) == compute_hypothesis_hash(reversed_order)

    def test_hash_changes_when_a_condition_threshold_changes(self):
        base = _experiment()
        changed = _experiment(conditions=_conditions(value=-0.02))
        assert compute_hypothesis_hash(base) != compute_hypothesis_hash(changed)

    def test_hash_changes_when_symbol_changes(self):
        base = _experiment()
        changed = _experiment(symbol="NVDA")
        assert compute_hypothesis_hash(base) != compute_hypothesis_hash(changed)

    def test_hash_changes_when_timeframe_changes(self):
        base = _experiment()
        changed = _experiment(timeframe="1h")
        assert compute_hypothesis_hash(base) != compute_hypothesis_hash(changed)

    def test_hash_changes_when_date_range_changes(self):
        base = _experiment()
        changed = _experiment(end_date="2024-07-15")
        assert compute_hypothesis_hash(base) != compute_hypothesis_hash(changed)

    def test_hash_changes_when_feature_contract_version_changes(self):
        base = _experiment()
        changed = base.model_copy(update={"feature_contract_version": "some-other-version"})
        assert compute_hypothesis_hash(base) != compute_hypothesis_hash(changed)

    def test_hash_changes_when_outcome_threshold_changes(self):
        base = _experiment()
        changed = _experiment(
            outcome=Outcome(metric="forward_return", horizon_minutes=60, operator=ConditionOperator.LTE, threshold=-0.05)
        )
        assert compute_hypothesis_hash(base) != compute_hypothesis_hash(changed)

    def test_hash_changes_when_outcome_horizon_changes(self):
        base = _experiment()
        changed = _experiment(
            outcome=Outcome(metric="forward_return", horizon_minutes=30, operator=ConditionOperator.LTE, threshold=-0.005)
        )
        assert compute_hypothesis_hash(base) != compute_hypothesis_hash(changed)

    def test_hash_is_unaffected_by_name_hypothesis_text_or_id(self):
        base = _experiment()
        renamed = base.model_copy(update={"name": "A totally different label", "hypothesis": "Completely different prose."})
        assert compute_hypothesis_hash(base) == compute_hypothesis_hash(renamed)

    def test_hash_is_unaffected_by_oos_partition_id(self):
        base = _experiment()
        linked = base.model_copy(update={"oos_partition_id": "some-partition-id"})
        assert compute_hypothesis_hash(base) == compute_hypothesis_hash(linked)

    def test_canonicalize_excludes_non_research_defining_fields(self):
        canonical = canonicalize_hypothesis(_experiment())
        assert "id" not in canonical
        assert "created_at" not in canonical
        assert "name" not in canonical
        assert "hypothesis" not in canonical
        assert "oos_partition_id" not in canonical


class TestPartitionLinkage:
    def test_a_fully_contained_development_range_is_valid(self):
        experiment = _experiment(start_date="2024-02-01", end_date="2024-03-01")
        validate_partition_linkage(experiment, _partition())  # must not raise

    def test_mismatched_symbol_is_rejected(self):
        experiment = _experiment(symbol="NVDA")
        with pytest.raises(PartitionLinkageError, match="not compatible"):
            validate_partition_linkage(experiment, _partition())  # partition is TSLA

    def test_mismatched_timeframe_is_rejected(self):
        experiment = _experiment(timeframe="1h")
        with pytest.raises(PartitionLinkageError, match="not compatible"):
            validate_partition_linkage(experiment, _partition(timeframe="5m"))

    def test_mismatched_provider_is_rejected(self):
        experiment = _experiment(provider="alpaca")
        with pytest.raises(PartitionLinkageError, match="not compatible"):
            validate_partition_linkage(experiment, _partition(provider="csv"))

    def test_range_extending_past_development_end_is_rejected(self):
        experiment = _experiment(start_date="2024-06-01", end_date="2024-07-15")  # bleeds into holdout
        with pytest.raises(PartitionLinkageError):
            validate_partition_linkage(experiment, _partition())

    def test_range_entirely_inside_holdout_is_rejected(self):
        experiment = _experiment(start_date="2024-08-01", end_date="2024-09-01")
        with pytest.raises(PartitionLinkageError):
            validate_partition_linkage(experiment, _partition())

    def test_range_entirely_outside_the_partition_is_rejected(self):
        experiment = _experiment(start_date="2020-01-01", end_date="2020-02-01")
        with pytest.raises(PartitionLinkageError):
            validate_partition_linkage(experiment, _partition())


class TestBuildFreezeSnapshot:
    def test_snapshot_carries_the_experiments_own_field_values(self):
        experiment = _experiment()
        frozen_at = datetime(2024, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
        snapshot = build_freeze_snapshot(experiment, hypothesis_hash="abc123", frozen_at=frozen_at)

        assert snapshot.experiment_id == experiment.id
        assert snapshot.hypothesis_hash == "abc123"
        assert snapshot.symbol == experiment.symbol
        assert snapshot.timeframe == experiment.timeframe
        assert snapshot.provider == experiment.provider
        assert snapshot.start_date == experiment.start_date
        assert snapshot.end_date == experiment.end_date
        assert snapshot.feature_contract_version == experiment.feature_contract_version
        assert snapshot.conditions == experiment.conditions
        assert snapshot.outcome == experiment.outcome
        assert snapshot.frozen_at == frozen_at
        assert snapshot.experiment_created_at == experiment.created_at

    def test_snapshot_is_a_copy_not_a_live_reference(self):
        experiment = _experiment()
        snapshot = build_freeze_snapshot(experiment, hypothesis_hash="abc123", frozen_at=datetime.now(timezone.utc))
        experiment.name = "mutated after the fact"  # legal on the in-memory pydantic object
        assert snapshot.name != "mutated after the fact"
