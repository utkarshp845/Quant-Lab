"""Tests for app/research/pipeline_status.py -- a pure function, no I/O,
so every test builds its own already-in-memory Experiment/Backtest/
OOSEvaluationResult objects rather than touching a database."""

from datetime import date, datetime, timezone

from app.models.backtesting import Backtest, BacktestStatus
from app.models.oos_evaluation import OOSEvaluationResult, OOSEvaluationStatus
from app.models.pipeline_status import PIPELINE_STAGE_IDS, PipelineStageStatus
from app.models.research import (
    Experiment,
    ExperimentCreateRequest,
    ExperimentLifecycleState,
    ExperimentResults,
    ExperimentStatus,
    FeatureCondition,
    Outcome,
)
from app.research.pipeline_status import build_pipeline_status


def _experiment(**overrides) -> Experiment:
    request = ExperimentCreateRequest(
        name="Test",
        hypothesis="Free text",
        symbol="TSLA",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        timeframe="5m",
        provider="csv",
        conditions=[FeatureCondition(feature_id="price.return_15m", operator="<=", value=-0.005)],
        outcome=Outcome(metric="forward_return", horizon_minutes=15, operator="<=", threshold=0.0),
    )
    experiment = Experiment.new(request)
    return experiment.model_copy(update=overrides)


def _status_by_id(response, stage_id):
    return next(s for s in response.stages if s.id == stage_id).status


def test_every_stage_id_present_exactly_once():
    response = build_pipeline_status(
        _experiment(), bars_count=0, features_count=0, decisions=[], backtests=[],
        oos_evaluations=[], oos_period_count=0, conclusions=[],
    )
    assert [s.id for s in response.stages] == PIPELINE_STAGE_IDS


def test_no_bars_gives_data_warning_and_is_the_next_action():
    response = build_pipeline_status(
        _experiment(), bars_count=0, features_count=0, decisions=[], backtests=[],
        oos_evaluations=[], oos_period_count=0, conclusions=[],
    )
    assert _status_by_id(response, "data") == PipelineStageStatus.WARNING
    assert "Fetch historical data" in response.next_action


def test_bars_but_no_features_gives_features_warning():
    response = build_pipeline_status(
        _experiment(), bars_count=100, features_count=0, decisions=[], backtests=[],
        oos_evaluations=[], oos_period_count=0, conclusions=[],
    )
    assert _status_by_id(response, "data") == PipelineStageStatus.COMPLETE
    assert _status_by_id(response, "features") == PipelineStageStatus.WARNING


def test_experiment_never_run_has_detect_not_started():
    response = build_pipeline_status(
        _experiment(), bars_count=100, features_count=100, decisions=[], backtests=[],
        oos_evaluations=[], oos_period_count=0, conclusions=[],
    )
    assert _status_by_id(response, "detect") == PipelineStageStatus.NOT_STARTED
    assert "Run this experiment" in response.next_action


def test_completed_run_with_zero_events_warns():
    experiment = _experiment(
        status=ExperimentStatus.COMPLETED,
        results=ExperimentResults(
            total_events=0, successful_events=0, failed_events=0, success_rate=None,
            average_outcome=None, median_outcome=None, min_outcome=None, max_outcome=None, std_dev_outcome=None,
        ),
    )
    response = build_pipeline_status(
        experiment, bars_count=100, features_count=100, decisions=[], backtests=[],
        oos_evaluations=[], oos_period_count=0, conclusions=[],
    )
    assert _status_by_id(response, "detect") == PipelineStageStatus.WARNING
    assert "No qualifying" in response.next_action


def test_completed_run_with_healthy_sample_unlocks_lock_next_action():
    experiment = _experiment(
        status=ExperimentStatus.COMPLETED,
        results=ExperimentResults(
            total_events=50, successful_events=30, failed_events=20, success_rate=0.6,
            average_outcome=0.01, median_outcome=0.01, min_outcome=-0.02, max_outcome=0.05, std_dev_outcome=0.01,
        ),
    )
    response = build_pipeline_status(
        experiment, bars_count=1000, features_count=1000, decisions=[], backtests=[],
        oos_evaluations=[], oos_period_count=0, conclusions=[],
    )
    assert _status_by_id(response, "detect") == PipelineStageStatus.COMPLETE
    assert _status_by_id(response, "measure") == PipelineStageStatus.COMPLETE
    assert _status_by_id(response, "lock") == PipelineStageStatus.NOT_STARTED
    assert "freeze" in response.next_action.lower()


def test_frozen_experiment_is_locked():
    experiment = _experiment(lifecycle_state=ExperimentLifecycleState.FROZEN)
    response = build_pipeline_status(
        experiment, bars_count=100, features_count=100, decisions=[], backtests=[],
        oos_evaluations=[], oos_period_count=0, conclusions=[],
    )
    assert _status_by_id(response, "lock") == PipelineStageStatus.COMPLETE
    assert _status_by_id(response, "oos") != PipelineStageStatus.BLOCKED


def test_draft_experiment_blocks_oos():
    response = build_pipeline_status(
        _experiment(), bars_count=100, features_count=100, decisions=[], backtests=[],
        oos_evaluations=[], oos_period_count=0, conclusions=[],
    )
    assert _status_by_id(response, "oos") == PipelineStageStatus.BLOCKED


def test_completed_backtest_unlocks_compare_and_validate():
    experiment = _experiment(lifecycle_state=ExperimentLifecycleState.FROZEN)
    backtest = Backtest.new(
        experiment_id=experiment.id, symbol="TSLA", timeframe="5m", provider="csv",
        windows=[5, 15], feature_contract_version=experiment.feature_contract_version,
    ).model_copy(update={"status": BacktestStatus.COMPLETED})
    response = build_pipeline_status(
        experiment, bars_count=100, features_count=100, decisions=[], backtests=[backtest],
        oos_evaluations=[], oos_period_count=0, conclusions=[],
    )
    assert _status_by_id(response, "compare") == PipelineStageStatus.COMPLETE
    assert _status_by_id(response, "validate") == PipelineStageStatus.COMPLETE
    assert _status_by_id(response, "backtest") == PipelineStageStatus.COMPLETE


def test_completed_oos_evaluation_completes_oos_stage():
    experiment = _experiment(lifecycle_state=ExperimentLifecycleState.OOS_EVALUATED)
    evaluation = OOSEvaluationResult(
        id="eval-1", experiment_id=experiment.id, hypothesis_hash="hash", frozen_snapshot_id=experiment.id,
        oos_partition_id="part-1", symbol="TSLA", timeframe="5m", provider="csv",
        holdout_start=datetime(2026, 2, 1, tzinfo=timezone.utc), holdout_end=datetime(2026, 2, 2, tzinfo=timezone.utc),
        feature_contract_version=experiment.feature_contract_version, outcome_horizon_minutes=15,
        outcome_window_bars=3, signal_count=10, results=None, status=OOSEvaluationStatus.COMPLETED,
        error_message=None, frozen_at=datetime.now(timezone.utc), evaluated_at=datetime.now(timezone.utc),
    )
    response = build_pipeline_status(
        experiment, bars_count=100, features_count=100, decisions=[], backtests=[],
        oos_evaluations=[evaluation], oos_period_count=1, conclusions=[],
    )
    assert _status_by_id(response, "oos") == PipelineStageStatus.COMPLETE


def test_current_stage_is_first_incomplete():
    response = build_pipeline_status(
        _experiment(), bars_count=0, features_count=0, decisions=[], backtests=[],
        oos_evaluations=[], oos_period_count=0, conclusions=[],
    )
    assert response.current_stage == "data"
