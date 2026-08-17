"""Tests for app/storage/research_repository.py -- the only module that
writes SQL for `experiments`/`experiment_events`. Every test uses an
explicit db_path pointing at a pytest tmp_path file, never the real
config.get_database_path() default -- same isolation convention as
tests/test_historical_bar_repository.py.
"""

from datetime import datetime, timezone

from app.models.research import (
    Condition,
    ConditionOperator,
    Experiment,
    ExperimentCreateRequest,
    ExperimentEvent,
    ExperimentResults,
    ExperimentStatus,
    Outcome,
)
from app.storage.research_repository import (
    get_events,
    get_experiment,
    list_experiments,
    mark_running,
    replace_events,
    save_experiment,
    update_experiment_run,
)


def _experiment(**overrides) -> Experiment:
    request = ExperimentCreateRequest(
        name=overrides.pop("name", "TSLA Early Selling Continuation"),
        hypothesis=overrides.pop("hypothesis", "Declines >= 1% in 30m keep declining >= 0.5% over the next 60m."),
        symbol=overrides.pop("symbol", "TSLA"),
        start_date=overrides.pop("start_date", "2026-01-01"),
        end_date=overrides.pop("end_date", "2026-06-01"),
        timeframe=overrides.pop("timeframe", "5m"),
        provider=overrides.pop("provider", "csv"),
        condition=overrides.pop("condition", Condition(metric="30m_return", operator=ConditionOperator.LTE, threshold=-0.01)),
        outcome=overrides.pop(
            "outcome", Outcome(metric="forward_return", horizon_minutes=60, operator=ConditionOperator.LTE, threshold=-0.005)
        ),
    )
    return Experiment.new(request)


def _event(experiment_id: str, signal_timestamp="2026-01-05T14:00:00Z", success=True) -> ExperimentEvent:
    return ExperimentEvent(
        experiment_id=experiment_id,
        symbol="TSLA",
        signal_timestamp=signal_timestamp,
        signal_price=98.0,
        condition_value=-0.02,
        outcome_timestamp="2026-01-05T15:00:00Z",
        outcome_price=97.0 if success else 100.0,
        outcome_value=-0.0102 if success else 0.0204,
        success=success,
    )


class TestSaveAndGetExperiment:
    def test_a_saved_experiment_round_trips_exactly(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()

        save_experiment(experiment, db_path=db_path)
        loaded = get_experiment(experiment.id, db_path=db_path)

        assert loaded == experiment

    def test_missing_experiment_returns_none_not_an_error(self, tmp_path):
        db_path = tmp_path / "research.db"
        assert get_experiment("does-not-exist", db_path=db_path) is None

    def test_condition_and_outcome_survive_the_round_trip_verbatim(self, tmp_path):
        db_path = tmp_path / "research.db"
        condition = Condition(metric="15m_return", operator=ConditionOperator.LT, threshold=-0.02)
        outcome = Outcome(metric="forward_return", horizon_minutes=45, operator=ConditionOperator.GTE, threshold=0.01)
        experiment = _experiment(condition=condition, outcome=outcome)

        save_experiment(experiment, db_path=db_path)
        loaded = get_experiment(experiment.id, db_path=db_path)

        assert loaded.condition == condition
        assert loaded.outcome == outcome


class TestListExperiments:
    def test_returns_every_saved_experiment(self, tmp_path):
        db_path = tmp_path / "research.db"
        first = _experiment(name="First")
        second = _experiment(name="Second")
        save_experiment(first, db_path=db_path)
        save_experiment(second, db_path=db_path)

        loaded = list_experiments(db_path=db_path)

        assert {e.id for e in loaded} == {first.id, second.id}

    def test_empty_when_nothing_saved(self, tmp_path):
        db_path = tmp_path / "research.db"
        assert list_experiments(db_path=db_path) == []


class TestExperimentRunLifecycle:
    def test_mark_running_updates_status_only(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)

        mark_running(experiment.id, db_path=db_path)

        loaded = get_experiment(experiment.id, db_path=db_path)
        assert loaded.status == ExperimentStatus.RUNNING
        assert loaded.symbol == experiment.symbol  # untouched
        assert loaded.condition == experiment.condition  # untouched

    def test_update_experiment_run_completed_persists_results(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)
        results = ExperimentResults(
            total_events=2,
            successful_events=1,
            failed_events=1,
            success_rate=0.5,
            average_outcome=0.001,
            median_outcome=0.001,
            min_outcome=-0.01,
            max_outcome=0.012,
            std_dev_outcome=0.011,
        )
        completed_at = datetime(2026, 6, 2, tzinfo=timezone.utc)

        update_experiment_run(
            experiment.id,
            status=ExperimentStatus.COMPLETED,
            completed_at=completed_at,
            results=results,
            error_message=None,
            db_path=db_path,
        )

        loaded = get_experiment(experiment.id, db_path=db_path)
        assert loaded.status == ExperimentStatus.COMPLETED
        assert loaded.completed_at == completed_at
        assert loaded.results == results
        assert loaded.error_message is None
        # Parameters set at creation are still exactly what was saved --
        # a run never mutates them (spec section 8).
        assert loaded.symbol == experiment.symbol
        assert loaded.start_date == experiment.start_date
        assert loaded.end_date == experiment.end_date
        assert loaded.timeframe == experiment.timeframe
        assert loaded.provider == experiment.provider
        assert loaded.condition == experiment.condition
        assert loaded.outcome == experiment.outcome

    def test_update_experiment_run_failed_persists_the_error_message(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)
        completed_at = datetime(2026, 6, 2, tzinfo=timezone.utc)

        update_experiment_run(
            experiment.id,
            status=ExperimentStatus.FAILED,
            completed_at=completed_at,
            results=None,
            error_message="no bars available for this dataset",
            db_path=db_path,
        )

        loaded = get_experiment(experiment.id, db_path=db_path)
        assert loaded.status == ExperimentStatus.FAILED
        assert loaded.results is None
        assert loaded.error_message == "no bars available for this dataset"


class TestEvents:
    def test_replace_events_then_get_events_round_trips(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)
        events = [
            _event(experiment.id, signal_timestamp="2026-01-05T14:00:00Z", success=True),
            _event(experiment.id, signal_timestamp="2026-01-06T14:00:00Z", success=False),
        ]

        replace_events(experiment.id, events, db_path=db_path)
        loaded = get_events(experiment.id, db_path=db_path)

        assert loaded == events

    def test_no_events_yet_returns_an_empty_list(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)

        assert get_events(experiment.id, db_path=db_path) == []

    def test_replace_events_deletes_the_previous_runs_events_not_appends(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)

        first_run_events = [_event(experiment.id, signal_timestamp="2026-01-05T14:00:00Z", success=True)]
        replace_events(experiment.id, first_run_events, db_path=db_path)

        second_run_events = [
            _event(experiment.id, signal_timestamp="2026-02-01T14:00:00Z", success=False),
            _event(experiment.id, signal_timestamp="2026-02-02T14:00:00Z", success=True),
        ]
        replace_events(experiment.id, second_run_events, db_path=db_path)

        loaded = get_events(experiment.id, db_path=db_path)
        assert len(loaded) == 2  # not 3 -- the first run's event is gone, not accumulated
        assert loaded == second_run_events

    def test_events_are_ordered_oldest_signal_first(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)
        events = [
            _event(experiment.id, signal_timestamp="2026-01-10T14:00:00Z"),
            _event(experiment.id, signal_timestamp="2026-01-05T14:00:00Z"),
            _event(experiment.id, signal_timestamp="2026-01-15T14:00:00Z"),
        ]

        replace_events(experiment.id, events, db_path=db_path)
        loaded = get_events(experiment.id, db_path=db_path)

        assert [e.signal_timestamp.day for e in loaded] == [5, 10, 15]

    def test_events_for_a_different_experiment_do_not_leak_in(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment_a = _experiment(name="A")
        experiment_b = _experiment(name="B")
        save_experiment(experiment_a, db_path=db_path)
        save_experiment(experiment_b, db_path=db_path)

        replace_events(experiment_a.id, [_event(experiment_a.id)], db_path=db_path)

        assert len(get_events(experiment_a.id, db_path=db_path)) == 1
        assert get_events(experiment_b.id, db_path=db_path) == []
