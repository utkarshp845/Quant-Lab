"""Tests for app/oos_evaluation/engine.py::evaluate_oos() -- the actual
OOS evaluation pipeline, against a real (tmp_path) SQLite database and
synthetic-but-realistic bars. No HTTP involved (see
tests/test_oos_evaluation_api.py for the end-to-end route tests) --
these call the orchestrator directly, exactly like
tests/test_statistical_validation_engine.py calls
build_statistical_validation_report() directly.

Covers requirement 10's leakage-proof list: DRAFT/ARCHIVED rejection,
missing/incompatible partition rejection, holdout-only authorization,
development bars never becoming signal observations, warm-up not
leaking into eligibility, no-look-ahead from future holdout data, and
deterministic re-runs.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.market_data import HistoricalBar
from app.models.oos_evaluation import OOSEvaluationStatus
from app.models.oos_partition import OOSPartition, OOSPartitionCreateRequest
from app.models.research import ConditionOperator, Experiment, ExperimentCreateRequest, FeatureCondition, FeatureConditionOperator, Outcome
from app.oos.access import HoldoutAccessError, get_holdout_bars
from app.oos_evaluation.engine import (
    ExperimentNotFoundForEvaluationError,
    IncompleteProvenanceError,
    InvalidLifecycleForEvaluationError,
    NoLinkedPartitionError,
    PartitionNotFoundForEvaluationError,
    evaluate_oos,
)
from app.research.lifecycle import PartitionLinkageError, build_freeze_snapshot, compute_hypothesis_hash
from app.storage import experiment_freeze_repository, historical_bar_repository, oos_partition_repository, research_repository

SYMBOL, TIMEFRAME, PROVIDER = "TSLA", "5m", "csv"


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
        name="OOS Eval Test", hypothesis="h", symbol=SYMBOL, start_date=start_date, end_date=end_date,
        timeframe=TIMEFRAME, provider=PROVIDER, conditions=conditions, outcome=outcome,
    )
    experiment = Experiment.new(request)
    research_repository.save_experiment(experiment, db_path=db_path)
    return experiment


def _freeze(db_path, experiment: Experiment, *, oos_partition_id: str | None, validate_with_partition: OOSPartition | None = None):
    """Mirrors what app/api/experiment_freeze.py's real freeze route
    does (set link, validate, snapshot, freeze) -- used directly here
    so these tests don't need a running HTTP app. Some tests
    deliberately skip a step (see their own docstrings) to construct an
    otherwise-unreachable-via-the-API precondition scenario."""
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


def _bars(start: datetime, count: int, *, base_price=100.0, price_step=0.01) -> list[HistoricalBar]:
    """A smooth, strictly-increasing 5-minute close series (no gaps) --
    close[i] is always the largest value in any trailing window, so
    price_position.ma50_distance (SMA50-based) is always a small
    POSITIVE number once 50 contiguous bars exist, never a sign-flip
    edge case to reason about."""
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


_MA50_CONDITION = [FeatureCondition(feature_id="price_position.ma50_distance", operator=FeatureConditionOperator.GT, value=-1.0)]


class TestLifecycleRejections:
    def test_draft_experiment_cannot_be_evaluated(self, tmp_path):
        db_path = tmp_path / "oos_eval.db"
        experiment = _make_experiment(db_path, start_date="2024-01-01", end_date="2024-01-01", conditions=_MA50_CONDITION)

        with pytest.raises(InvalidLifecycleForEvaluationError, match="DRAFT|draft"):
            evaluate_oos(experiment.id, db_path=db_path)

    def test_archived_experiment_cannot_be_evaluated(self, tmp_path):
        db_path = tmp_path / "oos_eval.db"
        partition = _make_partition(
            db_path,
            development_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            development_end=datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc),
            holdout_start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            holdout_end=datetime(2024, 1, 2, 4, 0, tzinfo=timezone.utc),
        )
        experiment = _make_experiment(db_path, start_date="2024-01-01", end_date="2024-01-01", conditions=_MA50_CONDITION)
        experiment = _freeze(db_path, experiment, oos_partition_id=partition.id)
        research_repository.mark_archived(experiment.id, archived_at=datetime.now(timezone.utc), db_path=db_path)

        with pytest.raises(InvalidLifecycleForEvaluationError):
            evaluate_oos(experiment.id, db_path=db_path)

    def test_unknown_experiment_id(self, tmp_path):
        db_path = tmp_path / "oos_eval.db"
        with pytest.raises(ExperimentNotFoundForEvaluationError):
            evaluate_oos("does-not-exist", db_path=db_path)


class TestPartitionLinkagePreconditions:
    def test_no_linked_partition_is_rejected(self, tmp_path):
        db_path = tmp_path / "oos_eval.db"
        experiment = _make_experiment(db_path, start_date="2024-01-01", end_date="2024-01-01", conditions=_MA50_CONDITION)
        experiment = _freeze(db_path, experiment, oos_partition_id=None)

        with pytest.raises(NoLinkedPartitionError):
            evaluate_oos(experiment.id, db_path=db_path)

    def test_incomplete_provenance_missing_snapshot_is_rejected(self, tmp_path):
        """Simulates a frozen `experiments` row whose snapshot write
        never happened (structurally shouldn't occur via the real
        freeze route, which writes both together -- app/oos_evaluation/
        engine.py::_check_preconditions()'s own docstring) by calling
        research_repository.freeze_experiment() directly, WITHOUT
        experiment_freeze_repository.save_snapshot()."""
        db_path = tmp_path / "oos_eval.db"
        partition = _make_partition(
            db_path,
            development_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            development_end=datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc),
            holdout_start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            holdout_end=datetime(2024, 1, 2, 4, 0, tzinfo=timezone.utc),
        )
        experiment = _make_experiment(db_path, start_date="2024-01-01", end_date="2024-01-01", conditions=_MA50_CONDITION)
        research_repository.set_oos_partition(experiment.id, partition.id, db_path=db_path)
        research_repository.freeze_experiment(
            experiment.id, hypothesis_hash="deadbeef", frozen_at=datetime.now(timezone.utc),
            oos_partition_id=partition.id, db_path=db_path,
        )

        with pytest.raises(IncompleteProvenanceError):
            evaluate_oos(experiment.id, db_path=db_path)

    def test_partition_that_no_longer_exists_is_rejected(self, tmp_path):
        db_path = tmp_path / "oos_eval.db"
        experiment = _make_experiment(db_path, start_date="2024-01-01", end_date="2024-01-01", conditions=_MA50_CONDITION)
        experiment.oos_partition_id = "phantom-partition"
        frozen_at = datetime.now(timezone.utc)
        hypothesis_hash = compute_hypothesis_hash(experiment)
        snapshot = build_freeze_snapshot(experiment, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at)
        experiment_freeze_repository.save_snapshot(snapshot, db_path=db_path)
        research_repository.freeze_experiment(
            experiment.id, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at,
            oos_partition_id="phantom-partition", db_path=db_path,
        )

        with pytest.raises(PartitionNotFoundForEvaluationError):
            evaluate_oos(experiment.id, db_path=db_path)

    def test_incompatible_partition_symbol_is_rejected(self, tmp_path):
        """Freezes with a partition for a DIFFERENT symbol than the
        experiment itself -- unreachable via the real freeze route
        (which validates this before persisting), constructed directly
        here to prove evaluate_oos() re-checks it independently rather
        than trusting the stored link blindly."""
        db_path = tmp_path / "oos_eval.db"
        partition = OOSPartition.new(
            OOSPartitionCreateRequest(
                symbol="NVDA", timeframe=TIMEFRAME, provider=PROVIDER,
                development_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                development_end=datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc),
                holdout_start=datetime(2024, 1, 2, tzinfo=timezone.utc),
                holdout_end=datetime(2024, 1, 2, 4, 0, tzinfo=timezone.utc),
            )
        )
        oos_partition_repository.save_partition(partition, db_path=db_path)
        experiment = _make_experiment(db_path, start_date="2024-01-01", end_date="2024-01-01", conditions=_MA50_CONDITION)
        experiment.oos_partition_id = partition.id
        frozen_at = datetime.now(timezone.utc)
        hypothesis_hash = compute_hypothesis_hash(experiment)
        snapshot = build_freeze_snapshot(experiment, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at)
        experiment_freeze_repository.save_snapshot(snapshot, db_path=db_path)
        research_repository.freeze_experiment(
            experiment.id, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at, oos_partition_id=partition.id, db_path=db_path
        )

        with pytest.raises(PartitionLinkageError):
            evaluate_oos(experiment.id, db_path=db_path)


class TestHoldoutOnlyAuthorization:
    def test_get_holdout_bars_refuses_without_explicit_confirmation(self, tmp_path):
        """Requirement 10's own explicit ask: proves the ONE holdout
        access path this module uses cannot be used without the
        explicit flag -- see app/oos_evaluation/engine.py's own
        docstring, step 4."""
        db_path = tmp_path / "oos_eval.db"
        partition = _make_partition(
            db_path,
            development_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            development_end=datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc),
            holdout_start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            holdout_end=datetime(2024, 1, 2, 4, 0, tzinfo=timezone.utc),
        )
        with pytest.raises(HoldoutAccessError):
            get_holdout_bars(partition, confirm_oos_validation_use=False, db_path=db_path)


class _FullScenario:
    """Shared builder for the pipeline-correctness tests below: 288
    contiguous 5-minute DEVELOPMENT bars (24 hours -- comfortably more
    than the 50-bar SMA50 warm-up requirement) followed immediately by
    48 contiguous 5-minute HOLDOUT bars (4 hours), strictly-increasing
    prices throughout, and the `price_position.ma50_distance > -1.0`
    condition (see _MA50_CONDITION) -- true at EVERY bar that has a
    defined ma50_distance, since a strictly-increasing series always
    has today's close above its own trailing SMA50.
    """

    development_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    holdout_start = datetime(2024, 1, 2, tzinfo=timezone.utc)
    holdout_end = datetime(2024, 1, 2, 4, 0, tzinfo=timezone.utc)
    development_end = holdout_start - timedelta(microseconds=1)

    def __init__(self, db_path, *, horizon_minutes=5, poison_index: int | None = None, poison_price: float = 500.0):
        self.db_path = db_path
        development_bars = _bars(self.development_start, 288)
        holdout_bars = _bars(self.holdout_start, 48, base_price=development_bars[-1].close + 0.01)
        if poison_index is not None:
            b = holdout_bars[poison_index]
            holdout_bars[poison_index] = HistoricalBar(
                symbol=b.symbol, timestamp=b.timestamp, open=poison_price, high=poison_price + 1,
                low=poison_price - 1, close=poison_price, volume=b.volume, provider=b.provider, timeframe=b.timeframe,
            )
        historical_bar_repository.save_bars(development_bars, db_path=db_path)
        historical_bar_repository.save_bars(holdout_bars, db_path=db_path)

        self.partition = _make_partition(
            db_path, development_start=self.development_start, development_end=self.development_end,
            holdout_start=self.holdout_start, holdout_end=self.holdout_end,
        )
        experiment = _make_experiment(
            db_path, start_date="2024-01-01", end_date="2024-01-01", conditions=_MA50_CONDITION, horizon_minutes=horizon_minutes
        )
        self.experiment = _freeze(db_path, experiment, oos_partition_id=self.partition.id)

    def evaluate(self):
        return evaluate_oos(self.experiment.id, db_path=self.db_path)


class TestWarmupAndHoldoutOnlyEligibility:
    def test_the_first_holdout_bar_already_has_a_signal_thanks_to_warmup(self, tmp_path):
        result, signals = _FullScenario(tmp_path / "oos_eval.db").evaluate()
        assert result.status == OOSEvaluationStatus.COMPLETED
        assert result.signal_count > 0
        assert signals[0].signal_timestamp == _FullScenario.holdout_start

    def test_every_signal_and_entry_falls_within_the_holdout_window(self, tmp_path):
        _result, signals = _FullScenario(tmp_path / "oos_eval.db").evaluate()
        assert signals, "expected at least one signal to assert over"
        for signal in signals:
            assert _FullScenario.holdout_start <= signal.signal_timestamp <= _FullScenario.holdout_end
            assert _FullScenario.holdout_start <= signal.entry_timestamp <= _FullScenario.holdout_end
            for outcome in signal.outcomes:
                assert outcome.outcome_timestamp >= signal.entry_timestamp
                assert outcome.outcome_timestamp <= _FullScenario.holdout_end

    def test_development_only_condition_never_produces_a_signal(self, tmp_path):
        """A condition satisfied ONLY by a huge price drop planted at
        the LAST development bar (via price.return_5m) -- proves a
        development bar can never become an eligible OOS signal
        observation, even though the Feature Engine, during warm-up,
        genuinely computes a return_5m value there that WOULD satisfy
        the condition; that FeatureRecord is simply never looked up
        against, because `bars` fed to run_backtest() is holdout bars
        only."""
        db_path = tmp_path / "oos_eval.db"
        development_bars = _bars(_FullScenario.development_start, 60)
        # Plant a huge drop on the LAST development bar only.
        last = development_bars[-1]
        development_bars[-1] = HistoricalBar(
            symbol=last.symbol, timestamp=last.timestamp, open=last.open, high=last.open,
            low=last.open * 0.5, close=last.open * 0.5, volume=last.volume, provider=last.provider, timeframe=last.timeframe,
        )
        holdout_bars = _bars(_FullScenario.holdout_start, 48, base_price=development_bars[-1].close)
        historical_bar_repository.save_bars(development_bars, db_path=db_path)
        historical_bar_repository.save_bars(holdout_bars, db_path=db_path)

        partition = _make_partition(
            db_path, development_start=_FullScenario.development_start, development_end=_FullScenario.development_end,
            holdout_start=_FullScenario.holdout_start, holdout_end=_FullScenario.holdout_end,
        )
        condition = [FeatureCondition(feature_id="price.return_5m", operator=FeatureConditionOperator.LTE, value=-0.1)]
        experiment = _make_experiment(db_path, start_date="2024-01-01", end_date="2024-01-01", conditions=condition)
        experiment = _freeze(db_path, experiment, oos_partition_id=partition.id)

        result, signals = evaluate_oos(experiment.id, db_path=db_path)
        assert result.status == OOSEvaluationStatus.COMPLETED
        assert signals == []  # the only bar satisfying the condition was a development bar


class TestNoLookAheadFromFutureHoldoutData:
    def test_an_early_signal_is_unaffected_by_a_change_far_later_in_holdout(self, tmp_path):
        baseline = _FullScenario(tmp_path / "baseline.db", horizon_minutes=5)
        _result_a, signals_a = baseline.evaluate()

        mutated = _FullScenario(tmp_path / "mutated.db", horizon_minutes=5, poison_index=40, poison_price=999.0)
        _result_b, signals_b = mutated.evaluate()

        assert signals_a, "expected at least one early signal to compare"
        early_a = signals_a[0]
        early_b = signals_b[0]
        assert early_a.signal_timestamp == early_b.signal_timestamp
        assert early_a.entry_price == early_b.entry_price
        assert early_a.feature_values == early_b.feature_values
        assert [o.forward_return for o in early_a.outcomes] == [o.forward_return for o in early_b.outcomes]


class TestDeterministicReRun:
    def test_rerunning_the_same_frozen_experiment_gives_identical_analytics(self, tmp_path):
        scenario = _FullScenario(tmp_path / "oos_eval.db")
        result_1, signals_1 = scenario.evaluate()
        result_2, signals_2 = scenario.evaluate()

        assert result_1.id != result_2.id  # a new, additional record each time
        assert result_1.signal_count == result_2.signal_count
        assert result_1.results == result_2.results  # identical aggregate analytics
        assert [s.feature_values for s in signals_1] == [s.feature_values for s in signals_2]
        assert [[o.forward_return for o in s.outcomes] for s in signals_1] == [
            [o.forward_return for o in s.outcomes] for s in signals_2
        ]
