"""Tests for app/storage/oos_evaluation_repository.py -- the only
module that writes SQL for `oos_evaluations`/`oos_evaluation_signals`.
Same explicit-db_path-per-test isolation convention as
tests/test_backtest_repository.py."""

from datetime import datetime, timezone

from app.models.backtesting import BacktestResults, BacktestWindowOutcome, BacktestWindowResults
from app.models.oos_evaluation import OOSEvaluationResult, OOSEvaluationStatus, OOSSignal
from app.storage.oos_evaluation_repository import get_evaluation, get_signals, list_evaluations, save_evaluation


def _result(**overrides) -> OOSEvaluationResult:
    fields = dict(
        id="eval-1",
        experiment_id="exp-1",
        hypothesis_hash="hash-1",
        frozen_snapshot_id="exp-1",
        oos_partition_id="partition-1",
        symbol="TSLA",
        timeframe="5m",
        provider="csv",
        holdout_start=datetime(2024, 7, 1, tzinfo=timezone.utc),
        holdout_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        feature_contract_version="v1",
        outcome_horizon_minutes=5,
        outcome_window_bars=1,
        signal_count=1,
        results=BacktestResults(
            windows=[
                BacktestWindowResults(
                    window_bars=1, signal_count=1, win_count=1, win_rate=1.0, mean_return=0.01, median_return=0.01,
                    std_dev_return=None, best_return=0.01, worst_return=0.01, mean_mfe=0.02, mean_mae=-0.005,
                )
            ]
        ),
        status=OOSEvaluationStatus.COMPLETED,
        error_message=None,
        frozen_at=datetime(2024, 6, 15, tzinfo=timezone.utc),
        evaluated_at=datetime(2024, 7, 5, tzinfo=timezone.utc),
    )
    fields.update(overrides)
    return OOSEvaluationResult(**fields)


def _signal(evaluation_id="eval-1") -> OOSSignal:
    return OOSSignal(
        evaluation_id=evaluation_id,
        symbol="TSLA",
        timeframe="5m",
        signal_timestamp=datetime(2024, 7, 1, 10, 0, tzinfo=timezone.utc),
        entry_timestamp=datetime(2024, 7, 1, 10, 5, tzinfo=timezone.utc),
        entry_price=100.0,
        feature_values={"price_position.ma50_distance": 0.01},
        outcomes=[BacktestWindowOutcome(window_bars=1, outcome_timestamp=datetime(2024, 7, 1, 10, 10, tzinfo=timezone.utc), forward_return=0.01, mfe=0.02, mae=-0.005)],
    )


class TestSaveAndGet:
    def test_a_saved_evaluation_round_trips_exactly(self, tmp_path):
        db_path = tmp_path / "oos_eval.db"
        result = _result()
        signals = [_signal()]
        save_evaluation(result, signals, db_path=db_path)

        fetched = get_evaluation(result.id, db_path=db_path)
        assert fetched == result
        assert get_signals(result.id, db_path=db_path) == signals

    def test_a_failed_evaluation_has_no_results(self, tmp_path):
        db_path = tmp_path / "oos_eval.db"
        result = _result(id="eval-failed", results=None, status=OOSEvaluationStatus.FAILED, error_message="boom", signal_count=0)
        save_evaluation(result, [], db_path=db_path)

        fetched = get_evaluation("eval-failed", db_path=db_path)
        assert fetched.status == OOSEvaluationStatus.FAILED
        assert fetched.results is None
        assert fetched.error_message == "boom"

    def test_getting_an_unknown_id_returns_none(self, tmp_path):
        db_path = tmp_path / "oos_eval.db"
        assert get_evaluation("does-not-exist", db_path=db_path) is None


class TestAppendOnly:
    def test_re_evaluating_creates_a_second_row_never_replacing_the_first(self, tmp_path):
        db_path = tmp_path / "oos_eval.db"
        first = _result(id="eval-1", evaluated_at=datetime(2024, 7, 1, tzinfo=timezone.utc))
        second = _result(id="eval-2", evaluated_at=datetime(2024, 7, 2, tzinfo=timezone.utc))
        save_evaluation(first, [_signal("eval-1")], db_path=db_path)
        save_evaluation(second, [_signal("eval-2")], db_path=db_path)

        assert get_evaluation("eval-1", db_path=db_path) == first
        assert get_evaluation("eval-2", db_path=db_path) == second
        evaluations = list_evaluations("exp-1", db_path=db_path)
        assert [e.id for e in evaluations] == ["eval-2", "eval-1"]  # newest first


class TestListEvaluations:
    def test_only_evaluations_for_the_requested_experiment_are_returned(self, tmp_path):
        db_path = tmp_path / "oos_eval.db"
        save_evaluation(_result(id="eval-1", experiment_id="exp-1"), [], db_path=db_path)
        save_evaluation(_result(id="eval-2", experiment_id="exp-2"), [], db_path=db_path)

        assert [e.id for e in list_evaluations("exp-1", db_path=db_path)] == ["eval-1"]
        assert [e.id for e in list_evaluations("exp-2", db_path=db_path)] == ["eval-2"]

    def test_unknown_experiment_returns_empty_list(self, tmp_path):
        db_path = tmp_path / "oos_eval.db"
        assert list_evaluations("does-not-exist", db_path=db_path) == []


class TestGetSignals:
    def test_signals_are_ordered_oldest_first(self, tmp_path):
        db_path = tmp_path / "oos_eval.db"
        early = _signal("eval-1")
        late = OOSSignal(**{**early.model_dump(), "signal_timestamp": datetime(2024, 7, 1, 12, 0, tzinfo=timezone.utc), "entry_timestamp": datetime(2024, 7, 1, 12, 5, tzinfo=timezone.utc)})
        save_evaluation(_result(), [late, early], db_path=db_path)

        signals = get_signals("eval-1", db_path=db_path)
        assert [s.signal_timestamp for s in signals] == [early.signal_timestamp, late.signal_timestamp]

    def test_unknown_evaluation_returns_empty_list(self, tmp_path):
        db_path = tmp_path / "oos_eval.db"
        assert get_signals("does-not-exist", db_path=db_path) == []
