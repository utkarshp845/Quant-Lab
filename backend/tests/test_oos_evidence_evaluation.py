"""Tests for app/oos_evidence/evaluation.py::evaluate_oos_period() --
the OOS Evidence Accumulation V1 evaluation orchestrator, against a
real (tmp_path) SQLite database and synthetic-but-realistic bars. No
HTTP involved (see tests/test_oos_evidence_api.py for the end-to-end
route tests) -- these call the orchestrator directly, matching
tests/test_oos_evaluation_engine.py's own convention for
app.oos_evaluation.engine.evaluate_oos().

Covers requirement 7's "Evaluation" list: multiple sequential OOS
periods, one failed evaluation followed by a successful one, repeated
evaluation of an already-COMPLETED period producing rejection, and
deterministic results for unchanged data -- plus the immutability
guarantee that a mutated LIVE Experiment row never affects what gets
evaluated (the hypothesis comes exclusively from the frozen snapshot).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.market_data import HistoricalBar
from app.models.oos_evaluation import OOSEvaluationStatus
from app.models.oos_evidence import OOSPeriod
from app.models.oos_partition import OOSPartition, OOSPartitionCreateRequest
from app.models.research import ConditionOperator, Experiment, ExperimentCreateRequest, FeatureCondition, FeatureConditionOperator, Outcome
from app.oos_evidence.evaluation import (
    ExperimentNotFoundForEvaluationError,
    PeriodAlreadyEvaluatedError,
    PeriodNotRegisteredError,
    evaluate_oos_period,
)
from app.oos_evidence.period import validate_new_period
from app.research.lifecycle import build_freeze_snapshot, compute_hypothesis_hash, validate_snapshot_partition_linkage
from app.storage import (
    experiment_freeze_repository,
    historical_bar_repository,
    oos_evaluation_repository,
    oos_evidence_repository,
    oos_partition_repository,
    research_repository,
)
from app.storage.db import get_connection

SYMBOL, TIMEFRAME, PROVIDER = "TSLA", "5m", "csv"

# A minimal-lookback condition (return_5m needs only ONE prior,
# CONTIGUOUS bar to be defined -- app/features/price.py) rather than
# this codebase's own SMA50-based test condition (app/features/
# price_position.py, a 50-bar trailing window): several DIFFERENT OOS
# periods below are deliberately placed days apart, each with its own
# development window that is NOT calendar-contiguous with any OTHER
# period's holdout bars (by design -- app/oos_evaluation/warmup.py's
# own, unmodified "a gap yields fewer contiguous bars, never a
# fabricated value" rule means a 50-bar-lookback feature would
# legitimately stay None for an entire 48-bar holdout window that
# starts days after its own warm-up bars end). return_5m only needs
# ONE prior bar, satisfied from each period's own SECOND holdout bar
# onward using that period's own internally-contiguous holdout bars
# alone -- proving evaluation genuinely works per-period, not
# incidentally relying on cross-period warm-up "leaking" a feature
# value it structurally should not have.
_ALWAYS_TRUE_CONDITION = [FeatureCondition(feature_id="price.return_5m", operator=FeatureConditionOperator.GT, value=-999.0)]


def _bars(start: datetime, count: int, *, base_price=100.0, price_step=0.01) -> list[HistoricalBar]:
    bars = []
    for i in range(count):
        close = base_price + i * price_step
        bars.append(
            HistoricalBar(
                symbol=SYMBOL, timestamp=start + timedelta(minutes=5 * i),
                open=close, high=close + 0.05, low=close - 0.05, close=close,
                volume=1_000, provider=PROVIDER, timeframe=TIMEFRAME,
            )
        )
    return bars


def _make_partition(db_path, *, development_start, development_end, holdout_start, holdout_end) -> OOSPartition:
    partition = OOSPartition.new(
        OOSPartitionCreateRequest(
            symbol=SYMBOL, timeframe=TIMEFRAME, provider=PROVIDER,
            development_start=development_start, development_end=development_end,
            holdout_start=holdout_start, holdout_end=holdout_end,
        )
    )
    oos_partition_repository.save_partition(partition, db_path=db_path)
    return partition


def _make_experiment(db_path, *, start_date, end_date, conditions, horizon_minutes=5) -> Experiment:
    outcome = Outcome(metric="forward_return", horizon_minutes=horizon_minutes, operator=ConditionOperator.GT, threshold=-999.0)
    request = ExperimentCreateRequest(
        name="OOS Evidence Test", hypothesis="h", symbol=SYMBOL, start_date=start_date, end_date=end_date,
        timeframe=TIMEFRAME, provider=PROVIDER, conditions=conditions, outcome=outcome,
    )
    experiment = Experiment.new(request)
    research_repository.save_experiment(experiment, db_path=db_path)
    return experiment


def _freeze(db_path, experiment: Experiment, *, oos_partition_id: str | None):
    if oos_partition_id is not None:
        research_repository.set_oos_partition(experiment.id, oos_partition_id, db_path=db_path)
        experiment = research_repository.get_experiment(experiment.id, db_path=db_path)
    frozen_at = datetime.now(timezone.utc)
    hypothesis_hash = compute_hypothesis_hash(experiment)
    snapshot = build_freeze_snapshot(experiment, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at)
    experiment_freeze_repository.save_snapshot(snapshot, db_path=db_path)
    research_repository.freeze_experiment(
        experiment.id, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at, oos_partition_id=oos_partition_id, db_path=db_path
    )
    return research_repository.get_experiment(experiment.id, db_path=db_path)


def _register_period(db_path, snapshot, new_partition: OOSPartition) -> OOSPeriod:
    """Mirrors what app/api/oos_evidence.py's real registration route
    does (validate linkage, gather already-registered partitions,
    validate the new period, save) -- used directly here so these tests
    don't need a running HTTP app."""
    validate_snapshot_partition_linkage(snapshot, new_partition)
    already_registered = []
    if snapshot.oos_partition_id is not None:
        original = oos_partition_repository.get_partition(snapshot.oos_partition_id, db_path=db_path)
        if original is not None:
            already_registered.append(original)
    already_registered.extend(
        partition
        for partition in (
            oos_partition_repository.get_partition(p.oos_partition_id, db_path=db_path)
            for p in oos_evidence_repository.list_periods(snapshot.experiment_id, db_path=db_path)
        )
        if partition is not None
    )
    validate_new_period(snapshot=snapshot, new_partition=new_partition, already_registered_partitions=already_registered)
    period = OOSPeriod(
        id=new_partition.id, experiment_id=snapshot.experiment_id, oos_partition_id=new_partition.id,
        symbol=new_partition.symbol, timeframe=new_partition.timeframe, provider=new_partition.provider,
        oos_start=new_partition.holdout_start, oos_end=new_partition.holdout_end, label=new_partition.label,
        registered_at=datetime.now(timezone.utc),
    )
    oos_evidence_repository.save_period(period, db_path=db_path)
    return period


class _MultiPeriodScenario:
    """288 contiguous 5-minute DEVELOPMENT bars (24 hours), reused as
    the shared warm-up context for every period below, each with its
    OWN, separately-seeded, non-overlapping 48-bar (4-hour) holdout
    window -- strictly-increasing prices throughout every segment, and
    the `price.return_5m > -999.0` condition (_ALWAYS_TRUE_CONDITION),
    true at every bar with a defined return_5m."""

    development_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    development_end = datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)

    def __init__(self, db_path, *, n_periods: int = 2, horizon_minutes=5):
        self.db_path = db_path
        development_bars = _bars(self.development_start, 288)
        historical_bar_repository.save_bars(development_bars, db_path=db_path)

        self.holdout_windows = [
            (datetime(2024, 1, 2 + i, tzinfo=timezone.utc), datetime(2024, 1, 2 + i, 4, 0, tzinfo=timezone.utc)) for i in range(n_periods)
        ]
        for holdout_start, _holdout_end in self.holdout_windows:
            holdout_bars = _bars(holdout_start, 48, base_price=development_bars[-1].close + 0.01)
            historical_bar_repository.save_bars(holdout_bars, db_path=db_path)

        self.original_partition = _make_partition(
            db_path, development_start=self.development_start, development_end=self.development_end,
            holdout_start=self.holdout_windows[0][0], holdout_end=self.holdout_windows[0][1],
        )
        experiment = _make_experiment(
            db_path, start_date="2024-01-01", end_date="2024-01-01", conditions=_ALWAYS_TRUE_CONDITION, horizon_minutes=horizon_minutes
        )
        self.experiment = _freeze(db_path, experiment, oos_partition_id=self.original_partition.id)
        self.snapshot = experiment_freeze_repository.get_snapshot(self.experiment.id, db_path=db_path)

        self.additional_partitions = []
        for holdout_start, holdout_end in self.holdout_windows[1:]:
            partition = _make_partition(
                db_path, development_start=self.development_start, development_end=self.development_end,
                holdout_start=holdout_start, holdout_end=holdout_end,
            )
            _register_period(db_path, self.snapshot, partition)
            self.additional_partitions.append(partition)

    def evaluate(self, oos_partition_id: str):
        """Mirrors what the real API route
        (app/api/oos_evidence.py::evaluate_oos_period_route()) does
        immediately after evaluate_oos_period() returns: persists the
        result. The "already evaluated" precondition
        (app/oos_evidence/evaluation.py) is itself read from that
        persisted history, so a caller that never persists would never
        observe it -- this helper always persists, exactly like the
        real route always does."""
        result, signals = evaluate_oos_period(self.experiment.id, oos_partition_id, db_path=self.db_path)
        oos_evaluation_repository.save_evaluation(result, signals, db_path=self.db_path)
        return result, signals


class TestMultipleSequentialPeriods:
    def test_each_additional_period_evaluates_independently_and_successfully(self, tmp_path):
        scenario = _MultiPeriodScenario(tmp_path / "evidence.db", n_periods=3)

        result_2, signals_2 = scenario.evaluate(scenario.additional_partitions[0].id)
        result_3, signals_3 = scenario.evaluate(scenario.additional_partitions[1].id)

        assert result_2.status == OOSEvaluationStatus.COMPLETED
        assert result_3.status == OOSEvaluationStatus.COMPLETED
        assert result_2.signal_count > 0
        assert result_3.signal_count > 0
        assert result_2.id != result_3.id
        assert result_2.oos_partition_id == scenario.additional_partitions[0].id
        assert result_3.oos_partition_id == scenario.additional_partitions[1].id
        # Every signal falls strictly within ITS OWN holdout window.
        for signal in signals_2:
            assert scenario.holdout_windows[1][0] <= signal.signal_timestamp <= scenario.holdout_windows[1][1]
        for signal in signals_3:
            assert scenario.holdout_windows[2][0] <= signal.signal_timestamp <= scenario.holdout_windows[2][1]

    def test_each_evaluation_carries_the_same_hypothesis_hash(self, tmp_path):
        """Requirement 8's own real-data-validation check, exercised
        here too: the SAME immutable hypothesis_hash across every
        period's evaluation, for the SAME frozen experiment."""
        scenario = _MultiPeriodScenario(tmp_path / "evidence.db", n_periods=3)
        result_2, _ = scenario.evaluate(scenario.additional_partitions[0].id)
        result_3, _ = scenario.evaluate(scenario.additional_partitions[1].id)

        assert result_2.hypothesis_hash == result_3.hypothesis_hash == scenario.snapshot.hypothesis_hash

    def test_original_partitions_own_evaluation_is_still_reachable_through_the_old_pipeline(self, tmp_path):
        """OOS Evaluation v1's own evaluate_oos() (unmodified) still
        works for the originally frozen-time-linked partition,
        unaffected by anything OOS Evidence Accumulation V1 adds."""
        from app.oos_evaluation.engine import evaluate_oos

        scenario = _MultiPeriodScenario(tmp_path / "evidence.db", n_periods=2)
        result_1, signals_1 = evaluate_oos(scenario.experiment.id, db_path=scenario.db_path)
        assert result_1.status == OOSEvaluationStatus.COMPLETED
        assert result_1.oos_partition_id == scenario.original_partition.id
        assert signals_1


class TestPeriodMustBeRegistered:
    def test_evaluating_an_unregistered_partition_is_rejected(self, tmp_path):
        scenario = _MultiPeriodScenario(tmp_path / "evidence.db", n_periods=1)
        unregistered = _make_partition(
            scenario.db_path, development_start=scenario.development_start, development_end=scenario.development_end,
            holdout_start=datetime(2024, 1, 10, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 10, 4, 0, tzinfo=timezone.utc),
        )
        with pytest.raises(PeriodNotRegisteredError):
            scenario.evaluate(unregistered.id)

    def test_unknown_experiment_id_is_rejected(self, tmp_path):
        db_path = tmp_path / "evidence.db"
        with pytest.raises(ExperimentNotFoundForEvaluationError):
            evaluate_oos_period("does-not-exist", "also-missing", db_path=db_path)


class TestRepeatedEvaluationRejected:
    def test_a_completed_period_cannot_be_evaluated_again(self, tmp_path):
        scenario = _MultiPeriodScenario(tmp_path / "evidence.db", n_periods=2)
        partition_id = scenario.additional_partitions[0].id

        result, _signals = scenario.evaluate(partition_id)
        assert result.status == OOSEvaluationStatus.COMPLETED

        with pytest.raises(PeriodAlreadyEvaluatedError):
            scenario.evaluate(partition_id)


class TestFailedThenSuccessfulEvaluation:
    def test_a_failed_evaluation_does_not_block_a_later_successful_one(self, tmp_path, monkeypatch):
        scenario = _MultiPeriodScenario(tmp_path / "evidence.db", n_periods=2)
        partition_id = scenario.additional_partitions[0].id

        import app.oos_evaluation.engine as engine_module

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated Feature Engine failure")

        monkeypatch.setattr(engine_module, "compute_features", _boom)
        failed_result, failed_signals = scenario.evaluate(partition_id)
        assert failed_result.status == OOSEvaluationStatus.FAILED
        assert failed_signals == []

        monkeypatch.undo()
        success_result, success_signals = scenario.evaluate(partition_id)
        assert success_result.status == OOSEvaluationStatus.COMPLETED
        assert success_signals
        assert success_result.id != failed_result.id


class TestImmutabilityOfTheFrozenHypothesis:
    def test_mutating_the_live_experiment_row_does_not_affect_evaluation(self, tmp_path):
        """Requirement 2's own guarantee: the hypothesis comes
        EXCLUSIVELY from the frozen ExperimentFreezeSnapshot -- proven
        by directly corrupting the live `experiments` row's
        research-defining fields (bypassing every real mutation
        boundary, exactly like tests/test_oos_evaluation_audit.py's own
        "tampered live Experiment row" proof) and confirming the
        evaluation's own results are UNCHANGED."""
        scenario = _MultiPeriodScenario(tmp_path / "evidence.db", n_periods=2)
        partition_id = scenario.additional_partitions[0].id

        baseline_result, baseline_signals = scenario.evaluate(partition_id)

        # A second, identical scenario, but with the live row corrupted
        # (a condition that would never fire) AFTER freezing -- the
        # frozen snapshot is untouched, so evaluation must be unaffected.
        tampered_db = tmp_path / "tampered.db"
        tampered_scenario = _MultiPeriodScenario(tampered_db, n_periods=2)
        tampered_partition_id = tampered_scenario.additional_partitions[0].id
        conn = get_connection(tampered_db)
        try:
            with conn:
                conn.execute(
                    "UPDATE experiments SET conditions_json = ? WHERE id = ?",
                    ('[{"feature_id": "price.return_5m", "operator": "<", "value": -999.0}]', tampered_scenario.experiment.id),
                )
        finally:
            conn.close()

        tampered_result, tampered_signals = tampered_scenario.evaluate(tampered_partition_id)

        assert tampered_result.signal_count == baseline_result.signal_count
        assert [s.signal_timestamp for s in tampered_signals] == [s.signal_timestamp for s in baseline_signals]


class TestDeterministicResults:
    def test_a_period_evaluated_once_gives_the_same_analytics_a_dry_run_would(self, tmp_path):
        """Since a period can only be evaluated once when COMPLETED
        (see TestRepeatedEvaluationRejected above), "deterministic
        results for unchanged data" is proven by comparing the ACTUAL
        evaluation against a second, independent scenario built from
        the identical inputs (rather than re-running the SAME period,
        which is deliberately disallowed once COMPLETED)."""
        scenario_a = _MultiPeriodScenario(tmp_path / "a.db", n_periods=2)
        scenario_b = _MultiPeriodScenario(tmp_path / "b.db", n_periods=2)

        result_a, signals_a = scenario_a.evaluate(scenario_a.additional_partitions[0].id)
        result_b, signals_b = scenario_b.evaluate(scenario_b.additional_partitions[0].id)

        assert result_a.signal_count == result_b.signal_count
        assert result_a.results == result_b.results
        assert [s.feature_values for s in signals_a] == [s.feature_values for s in signals_b]
