"""Tests for app/storage/research_repository.py -- the only module that
writes SQL for `experiments`/`experiment_events`. Every test uses an
explicit db_path pointing at a pytest tmp_path file, never the real
config.get_database_path() default -- same isolation convention as
tests/test_historical_bar_repository.py.
"""

from datetime import datetime, timezone

from app.models.research import (
    ConditionOperator,
    Experiment,
    ExperimentCreateRequest,
    ExperimentEvent,
    ExperimentResults,
    ExperimentStatus,
    FeatureCondition,
    FeatureConditionOperator,
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


def _conditions(feature_id="price.return_30m", operator=FeatureConditionOperator.LTE, value=-0.01) -> list[FeatureCondition]:
    return [FeatureCondition(feature_id=feature_id, operator=operator, value=value)]


def _experiment(**overrides) -> Experiment:
    request = ExperimentCreateRequest(
        name=overrides.pop("name", "TSLA Early Selling Continuation"),
        hypothesis=overrides.pop("hypothesis", "Declines >= 1% in 30m keep declining >= 0.5% over the next 60m."),
        symbol=overrides.pop("symbol", "TSLA"),
        start_date=overrides.pop("start_date", "2026-01-01"),
        end_date=overrides.pop("end_date", "2026-06-01"),
        timeframe=overrides.pop("timeframe", "5m"),
        provider=overrides.pop("provider", "csv"),
        conditions=overrides.pop("conditions", _conditions()),
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
        condition_values={"price.return_30m": -0.02, "volume.relative_volume": 1.8},
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

    def test_conditions_and_outcome_survive_the_round_trip_verbatim(self, tmp_path):
        db_path = tmp_path / "research.db"
        conditions = [
            FeatureCondition(feature_id="price.return_15m", operator=FeatureConditionOperator.LT, value=-0.02),
            FeatureCondition(
                feature_id="volatility.volatility_percentile", operator=FeatureConditionOperator.BETWEEN, value=0.5, value_max=0.7
            ),
        ]
        outcome = Outcome(metric="forward_return", horizon_minutes=45, operator=ConditionOperator.GTE, threshold=0.01)
        experiment = _experiment(conditions=conditions, outcome=outcome)

        save_experiment(experiment, db_path=db_path)
        loaded = get_experiment(experiment.id, db_path=db_path)

        assert loaded.conditions == conditions
        assert loaded.outcome == outcome

    def test_feature_contract_version_survives_the_round_trip(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()

        save_experiment(experiment, db_path=db_path)
        loaded = get_experiment(experiment.id, db_path=db_path)

        assert loaded.feature_contract_version == experiment.feature_contract_version


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
        assert loaded.conditions == experiment.conditions  # untouched

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
        assert loaded.conditions == experiment.conditions
        assert loaded.outcome == experiment.outcome
        assert loaded.feature_contract_version == experiment.feature_contract_version

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

    def test_condition_values_survive_the_round_trip(self, tmp_path):
        db_path = tmp_path / "research.db"
        experiment = _experiment()
        save_experiment(experiment, db_path=db_path)
        events = [_event(experiment.id)]

        replace_events(experiment.id, events, db_path=db_path)
        loaded = get_events(experiment.id, db_path=db_path)

        assert loaded[0].condition_values == {"price.return_30m": -0.02, "volume.relative_volume": 1.8}

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


class TestLegacyEventRowsWithoutConditionValues:
    """v0.1.24: an experiment_events row saved before this version has
    NULL condition_values_json (not individually data-migrated -- see
    the schema comment on experiment_events, app/storage/db.py). Reading
    it back must degrade gracefully to an empty dict, never crash.
    Builds an authentic pre-v0.1.24 database by hand (the OLD
    `experiment_events` shape, no `condition_values_json` column at
    all) and lets get_connection()'s own migration path add it --
    exactly what happens to a real developer's existing database file.
    """

    def test_a_pre_migration_row_reads_back_with_an_empty_condition_values_dict(self, tmp_path):
        import sqlite3

        db_path = tmp_path / "research.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE experiments (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, hypothesis TEXT NOT NULL, symbol TEXT NOT NULL,
                start_date TEXT NOT NULL, end_date TEXT NOT NULL, timeframe TEXT NOT NULL, provider TEXT NOT NULL,
                condition_json TEXT NOT NULL, outcome_json TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, completed_at TEXT, results_json TEXT, error_message TEXT
            );
            CREATE TABLE experiment_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL, symbol TEXT NOT NULL,
                signal_timestamp TEXT NOT NULL, signal_price REAL NOT NULL, condition_value REAL NOT NULL,
                outcome_timestamp TEXT NOT NULL, outcome_price REAL NOT NULL, outcome_value REAL NOT NULL,
                success INTEGER NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO experiments (id, name, hypothesis, symbol, start_date, end_date, timeframe, provider,
                condition_json, outcome_json, status, created_at)
            VALUES ('exp-legacy', 'Legacy', 'h', 'TSLA', '2026-01-01', '2026-06-01', '5m', 'csv',
                '{"metric":"30m_return","operator":"<=","threshold":-0.01}',
                '{"metric":"forward_return","horizon_minutes":60,"operator":"<=","threshold":-0.005}',
                'completed', '2026-01-01T00:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO experiment_events
                (experiment_id, symbol, signal_timestamp, signal_price, condition_value,
                 outcome_timestamp, outcome_price, outcome_value, success)
            VALUES ('exp-legacy', 'TSLA', '2026-01-05T14:00:00', 98.0, -0.02,
                    '2026-01-05T15:00:00', 97.0, -0.0102, 1)
            """
        )
        conn.commit()
        conn.close()

        loaded_experiment = get_experiment("exp-legacy", db_path=db_path)
        assert loaded_experiment.conditions == [
            FeatureCondition(feature_id="price.return_30m", operator=FeatureConditionOperator.LTE, value=-0.01)
        ]

        loaded_events = get_events("exp-legacy", db_path=db_path)
        assert len(loaded_events) == 1
        assert loaded_events[0].condition_values == {}
        assert loaded_events[0].signal_price == 98.0


class TestNewWritesAgainstAMigratedDatabase:
    """Regression test for a real bug a live end-to-end run found:
    additive ALTER TABLE alone (the _EXPERIMENTS_MIGRATIONS /
    _EXPERIMENT_EVENTS_MIGRATIONS lists) is not enough on a database
    that predates v0.1.24 -- `experiments.condition_json` and
    `experiment_events.condition_value` are still declared NOT NULL on
    such a database, and current code never populates either one on
    INSERT, so creating a brand-new experiment (or saving a brand-new
    event) against an old-shape-but-already-migrated database raised
    sqlite3.IntegrityError before app/storage/db.py::
    _drop_legacy_not_null_columns() existed to rebuild both tables and
    actually remove those constraints. Builds the same authentic
    pre-v0.1.24 database as TestLegacyEventRowsWithoutConditionValues
    above, but this time proves a NEW write succeeds after migration,
    not just that an old row still reads back.
    """

    def _legacy_db(self, tmp_path):
        import sqlite3

        db_path = tmp_path / "research.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE experiments (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, hypothesis TEXT NOT NULL, symbol TEXT NOT NULL,
                start_date TEXT NOT NULL, end_date TEXT NOT NULL, timeframe TEXT NOT NULL, provider TEXT NOT NULL,
                condition_json TEXT NOT NULL, outcome_json TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, completed_at TEXT, results_json TEXT, error_message TEXT
            );
            CREATE TABLE experiment_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL, symbol TEXT NOT NULL,
                signal_timestamp TEXT NOT NULL, signal_price REAL NOT NULL, condition_value REAL NOT NULL,
                outcome_timestamp TEXT NOT NULL, outcome_price REAL NOT NULL, outcome_value REAL NOT NULL,
                success INTEGER NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO experiments (id, name, hypothesis, symbol, start_date, end_date, timeframe, provider,
                condition_json, outcome_json, status, created_at)
            VALUES ('exp-legacy', 'Legacy', 'h', 'TSLA', '2026-01-01', '2026-06-01', '5m', 'csv',
                '{"metric":"30m_return","operator":"<=","threshold":-0.01}',
                '{"metric":"forward_return","horizon_minutes":60,"operator":"<=","threshold":-0.005}',
                'completed', '2026-01-01T00:00:00+00:00')
            """
        )
        conn.commit()
        conn.close()
        return db_path

    def test_a_brand_new_experiment_can_be_saved_after_migration(self, tmp_path):
        db_path = self._legacy_db(tmp_path)

        new_experiment = _experiment(name="Brand new, post-migration")
        save_experiment(new_experiment, db_path=db_path)  # must not raise sqlite3.IntegrityError

        loaded = get_experiment(new_experiment.id, db_path=db_path)
        assert loaded == new_experiment
        # The pre-existing legacy row is still there too, untouched by the rebuild.
        assert get_experiment("exp-legacy", db_path=db_path) is not None

    def test_a_brand_new_event_can_be_saved_after_migration(self, tmp_path):
        db_path = self._legacy_db(tmp_path)

        new_experiment = _experiment(name="Brand new, post-migration")
        save_experiment(new_experiment, db_path=db_path)
        events = [_event(new_experiment.id)]

        replace_events(new_experiment.id, events, db_path=db_path)  # must not raise sqlite3.IntegrityError

        loaded = get_events(new_experiment.id, db_path=db_path)
        assert loaded == events
