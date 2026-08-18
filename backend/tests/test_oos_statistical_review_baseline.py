"""Tests for app/oos_statistical_review/baseline.py::
compute_oos_unconditional_baseline() -- against a real (tmp_path)
SQLite database and synthetic-but-realistic bars.

Covers requirement 3 ("the baseline must be constructed from the SAME
OOS time ranges, not from development data" / "fail with a clear error
rather than silently substituting") and requirement 13's "missing
baseline data fails closed".
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.market_data import HistoricalBar
from app.models.oos_evaluation import OOSEvaluationResult, OOSEvaluationStatus
from app.models.oos_partition import OOSPartition, OOSPartitionCreateRequest
from app.oos_statistical_review.baseline import BaselineConstructionError, compute_oos_unconditional_baseline
from app.storage import historical_bar_repository, oos_partition_repository

SYMBOL, TIMEFRAME, PROVIDER = "TSLA", "5m", "csv"
DEVELOPMENT_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
DEVELOPMENT_END = datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)
HOLDOUT_START = datetime(2024, 1, 2, tzinfo=timezone.utc)
HOLDOUT_END = datetime(2024, 1, 2, 4, 0, tzinfo=timezone.utc)


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


def _make_partition(db_path) -> OOSPartition:
    partition = OOSPartition.new(
        OOSPartitionCreateRequest(
            symbol=SYMBOL, timeframe=TIMEFRAME, provider=PROVIDER,
            development_start=DEVELOPMENT_START, development_end=DEVELOPMENT_END,
            holdout_start=HOLDOUT_START, holdout_end=HOLDOUT_END,
        )
    )
    oos_partition_repository.save_partition(partition, db_path=db_path)
    return partition


def _make_evaluation(partition: OOSPartition, *, outcome_window_bars=3, symbol=None, timeframe=None, provider=None) -> OOSEvaluationResult:
    return OOSEvaluationResult(
        id="eval-1", experiment_id="exp-1", hypothesis_hash="deadbeef", frozen_snapshot_id="exp-1",
        oos_partition_id=partition.id, symbol=symbol or partition.symbol, timeframe=timeframe or partition.timeframe,
        provider=provider or partition.provider, holdout_start=partition.holdout_start, holdout_end=partition.holdout_end,
        feature_contract_version="v1", outcome_horizon_minutes=15, outcome_window_bars=outcome_window_bars,
        signal_count=0, results=None, status=OOSEvaluationStatus.COMPLETED, error_message=None,
        frozen_at=datetime.now(timezone.utc), evaluated_at=datetime.now(timezone.utc),
    )


def _seed_bars(db_path):
    development_bars = _bars(DEVELOPMENT_START, 288)
    holdout_bars = _bars(HOLDOUT_START, 48, base_price=development_bars[-1].close + 0.01)
    historical_bar_repository.save_bars(development_bars, db_path=db_path)
    historical_bar_repository.save_bars(holdout_bars, db_path=db_path)
    return development_bars, holdout_bars


class TestBaselineUsesOnlyHoldoutBars:
    def test_every_baseline_signal_falls_within_the_holdout_window(self, tmp_path):
        db_path = tmp_path / "baseline.db"
        _seed_bars(db_path)
        partition = _make_partition(db_path)
        evaluation = _make_evaluation(partition)

        signals = compute_oos_unconditional_baseline(evaluation, db_path=db_path)
        assert signals, "expected at least one baseline signal to assert over"
        for signal in signals:
            assert HOLDOUT_START <= signal.signal_timestamp <= HOLDOUT_END
            assert HOLDOUT_START <= signal.entry_timestamp <= HOLDOUT_END
            for outcome in signal.outcomes:
                assert outcome.outcome_timestamp <= HOLDOUT_END

    def test_baseline_covers_nearly_every_eligible_holdout_bar(self, tmp_path):
        """CONTROL_CONDITION (volume.volume >= 0) is true for every bar
        -- the baseline should include (holdout bar count - a small
        edge-of-window shortfall for the final few bars lacking a full
        forward window), never a mysteriously small subset."""
        db_path = tmp_path / "baseline.db"
        _seed_bars(db_path)
        partition = _make_partition(db_path)
        evaluation = _make_evaluation(partition, outcome_window_bars=3)

        signals = compute_oos_unconditional_baseline(evaluation, db_path=db_path)
        assert len(signals) >= 40  # 48 holdout bars, minus the last few with no next-bar-open / no full window


class TestFailsClosed:
    def test_missing_partition_raises(self, tmp_path):
        db_path = tmp_path / "baseline.db"
        _seed_bars(db_path)
        partition = _make_partition(db_path)
        evaluation = _make_evaluation(partition)
        evaluation = evaluation.model_copy(update={"oos_partition_id": "does-not-exist"})

        with pytest.raises(BaselineConstructionError, match="no longer exists"):
            compute_oos_unconditional_baseline(evaluation, db_path=db_path)

    def test_symbol_mismatch_raises(self, tmp_path):
        db_path = tmp_path / "baseline.db"
        _seed_bars(db_path)
        partition = _make_partition(db_path)
        evaluation = _make_evaluation(partition, symbol="NVDA")

        with pytest.raises(BaselineConstructionError, match="no longer matches"):
            compute_oos_unconditional_baseline(evaluation, db_path=db_path)

    def test_no_holdout_bars_raises(self, tmp_path):
        db_path = tmp_path / "baseline.db"
        historical_bar_repository.save_bars(_bars(DEVELOPMENT_START, 288), db_path=db_path)  # development only, no holdout bars saved
        partition = _make_partition(db_path)
        evaluation = _make_evaluation(partition)

        with pytest.raises(BaselineConstructionError, match="no holdout bars"):
            compute_oos_unconditional_baseline(evaluation, db_path=db_path)

    def test_missing_outcome_window_bars_raises(self, tmp_path):
        db_path = tmp_path / "baseline.db"
        _seed_bars(db_path)
        partition = _make_partition(db_path)
        evaluation = _make_evaluation(partition).model_copy(update={"outcome_window_bars": None})

        with pytest.raises(BaselineConstructionError, match="no outcome_window_bars"):
            compute_oos_unconditional_baseline(evaluation, db_path=db_path)


class TestDeterministic:
    def test_same_evaluation_produces_identical_baseline_across_calls(self, tmp_path):
        db_path = tmp_path / "baseline.db"
        _seed_bars(db_path)
        partition = _make_partition(db_path)
        evaluation = _make_evaluation(partition)

        first = compute_oos_unconditional_baseline(evaluation, db_path=db_path)
        second = compute_oos_unconditional_baseline(evaluation, db_path=db_path)
        assert first == second


class TestNoDevelopmentDataLeaksIn:
    def test_a_development_only_price_spike_never_appears_in_the_baseline(self, tmp_path):
        """Plants a huge, obvious price spike on the LAST development
        bar only -- proves the OOS-scoped baseline never reads it (no
        signal/entry/outcome derived from it), the same "development
        bar can never become an OOS observation" guarantee OOS
        Evaluation v1's own audit already established, checked again
        here at the baseline-construction layer."""
        db_path = tmp_path / "baseline.db"
        development_bars = _bars(DEVELOPMENT_START, 288)
        last = development_bars[-1]
        development_bars[-1] = HistoricalBar(
            symbol=last.symbol, timestamp=last.timestamp, open=last.open, high=last.open * 5,
            low=last.open, close=last.open * 5, volume=last.volume, provider=last.provider, timeframe=last.timeframe,
        )
        holdout_bars = _bars(HOLDOUT_START, 48, base_price=100.0)
        historical_bar_repository.save_bars(development_bars, db_path=db_path)
        historical_bar_repository.save_bars(holdout_bars, db_path=db_path)
        partition = _make_partition(db_path)
        evaluation = _make_evaluation(partition)

        signals = compute_oos_unconditional_baseline(evaluation, db_path=db_path)
        for signal in signals:
            assert signal.entry_price < last.open * 2  # nowhere near the planted spike's magnitude
