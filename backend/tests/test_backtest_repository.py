"""Tests for app/storage/backtest_repository.py -- the only module that
writes SQL for `backtests`/`backtest_signals`. Every test uses an
explicit db_path pointing at a pytest tmp_path file, never the real
config.get_database_path() default -- same isolation convention as
tests/test_research_repository.py.
"""

from datetime import datetime, timezone

from app.models.backtesting import (
    Backtest,
    BacktestResults,
    BacktestSignal,
    BacktestStatus,
    BacktestWindowOutcome,
    BacktestWindowResults,
)
from app.storage.backtest_repository import (
    get_backtest,
    get_signals,
    list_backtests,
    mark_running,
    replace_signals,
    save_backtest,
    update_backtest_run,
)


def _backtest(**overrides) -> Backtest:
    return Backtest.new(
        experiment_id=overrides.pop("experiment_id", "exp-1"),
        symbol=overrides.pop("symbol", "TSLA"),
        timeframe=overrides.pop("timeframe", "5m"),
        provider=overrides.pop("provider", "csv"),
        windows=overrides.pop("windows", [5, 15, 30, 60]),
        feature_contract_version=overrides.pop("feature_contract_version", "v1"),
    )


def _signal(backtest_id: str, experiment_id="exp-1", signal_timestamp="2026-01-05T14:00:00Z") -> BacktestSignal:
    return BacktestSignal(
        backtest_id=backtest_id,
        experiment_id=experiment_id,
        symbol="TSLA",
        timeframe="5m",
        signal_timestamp=signal_timestamp,
        entry_timestamp="2026-01-05T14:05:00Z",
        entry_price=97.0,
        feature_values={"price.return_5m": -0.02, "volume.volume_acceleration": 1.5},
        outcomes=[
            BacktestWindowOutcome(window_bars=5, outcome_timestamp="2026-01-05T14:30:00Z", forward_return=0.01, mfe=0.02, mae=-0.005),
            BacktestWindowOutcome(window_bars=15, outcome_timestamp="2026-01-05T15:20:00Z", forward_return=0.02, mfe=0.03, mae=-0.01),
        ],
    )


class TestSaveAndGetBacktest:
    def test_a_saved_backtest_round_trips_exactly(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        backtest = _backtest()

        save_backtest(backtest, db_path=db_path)
        loaded = get_backtest(backtest.id, db_path=db_path)

        assert loaded == backtest

    def test_missing_backtest_returns_none_not_an_error(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        assert get_backtest("does-not-exist", db_path=db_path) is None

    def test_windows_survive_the_round_trip_verbatim(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        backtest = _backtest(windows=[1, 3, 7])

        save_backtest(backtest, db_path=db_path)
        loaded = get_backtest(backtest.id, db_path=db_path)

        assert loaded.windows == [1, 3, 7]

    def test_feature_contract_version_survives_the_round_trip(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        backtest = _backtest(feature_contract_version="v7-not-real")

        save_backtest(backtest, db_path=db_path)
        loaded = get_backtest(backtest.id, db_path=db_path)

        assert loaded.feature_contract_version == "v7-not-real"


class TestListBacktests:
    def test_returns_every_saved_backtest(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        first = _backtest(experiment_id="exp-1")
        second = _backtest(experiment_id="exp-2")
        save_backtest(first, db_path=db_path)
        save_backtest(second, db_path=db_path)

        loaded = list_backtests(db_path=db_path)

        assert {b.id for b in loaded} == {first.id, second.id}

    def test_empty_when_nothing_saved(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        assert list_backtests(db_path=db_path) == []

    def test_filters_by_experiment_id(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        first = _backtest(experiment_id="exp-1")
        second = _backtest(experiment_id="exp-2")
        save_backtest(first, db_path=db_path)
        save_backtest(second, db_path=db_path)

        loaded = list_backtests(experiment_id="exp-1", db_path=db_path)

        assert [b.id for b in loaded] == [first.id]


class TestBacktestRunLifecycle:
    def test_mark_running_updates_status_only(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        backtest = _backtest()
        save_backtest(backtest, db_path=db_path)

        mark_running(backtest.id, db_path=db_path)

        loaded = get_backtest(backtest.id, db_path=db_path)
        assert loaded.status == BacktestStatus.RUNNING
        assert loaded.symbol == backtest.symbol  # untouched
        assert loaded.windows == backtest.windows  # untouched

    def test_update_backtest_run_completed_persists_results(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        backtest = _backtest()
        save_backtest(backtest, db_path=db_path)
        results = BacktestResults(
            windows=[
                BacktestWindowResults(
                    window_bars=5, signal_count=2, win_count=1, win_rate=0.5, mean_return=0.001, median_return=0.001,
                    std_dev_return=0.01, best_return=0.02, worst_return=-0.01, mean_mfe=0.03, mean_mae=-0.015,
                )
            ]
        )
        completed_at = datetime(2026, 6, 2, tzinfo=timezone.utc)

        update_backtest_run(
            backtest.id, status=BacktestStatus.COMPLETED, completed_at=completed_at, results=results, error_message=None, db_path=db_path
        )

        loaded = get_backtest(backtest.id, db_path=db_path)
        assert loaded.status == BacktestStatus.COMPLETED
        assert loaded.completed_at == completed_at
        assert loaded.results == results
        assert loaded.error_message is None
        # Parameters set at creation are untouched by a run.
        assert loaded.experiment_id == backtest.experiment_id
        assert loaded.symbol == backtest.symbol
        assert loaded.timeframe == backtest.timeframe
        assert loaded.provider == backtest.provider
        assert loaded.windows == backtest.windows
        assert loaded.feature_contract_version == backtest.feature_contract_version

    def test_update_backtest_run_failed_persists_the_error_message(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        backtest = _backtest()
        save_backtest(backtest, db_path=db_path)
        completed_at = datetime(2026, 6, 2, tzinfo=timezone.utc)

        update_backtest_run(
            backtest.id,
            status=BacktestStatus.FAILED,
            completed_at=completed_at,
            results=None,
            error_message="no bars available for this dataset",
            db_path=db_path,
        )

        loaded = get_backtest(backtest.id, db_path=db_path)
        assert loaded.status == BacktestStatus.FAILED
        assert loaded.results is None
        assert loaded.error_message == "no bars available for this dataset"


class TestSignals:
    def test_replace_signals_then_get_signals_round_trips(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        backtest = _backtest()
        save_backtest(backtest, db_path=db_path)
        signals = [
            _signal(backtest.id, signal_timestamp="2026-01-05T14:00:00Z"),
            _signal(backtest.id, signal_timestamp="2026-01-06T14:00:00Z"),
        ]

        replace_signals(backtest.id, signals, db_path=db_path)
        loaded = get_signals(backtest.id, db_path=db_path)

        assert loaded == signals

    def test_feature_values_and_outcomes_survive_the_round_trip(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        backtest = _backtest()
        save_backtest(backtest, db_path=db_path)
        signals = [_signal(backtest.id)]

        replace_signals(backtest.id, signals, db_path=db_path)
        loaded = get_signals(backtest.id, db_path=db_path)

        assert loaded[0].feature_values == {"price.return_5m": -0.02, "volume.volume_acceleration": 1.5}
        assert [o.window_bars for o in loaded[0].outcomes] == [5, 15]
        assert loaded[0].outcomes[0].forward_return == 0.01
        assert loaded[0].outcomes[1].mae == -0.01

    def test_no_signals_yet_returns_an_empty_list(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        backtest = _backtest()
        save_backtest(backtest, db_path=db_path)

        assert get_signals(backtest.id, db_path=db_path) == []

    def test_replace_signals_deletes_the_previous_runs_signals_not_appends(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        backtest = _backtest()
        save_backtest(backtest, db_path=db_path)

        first_run = [_signal(backtest.id, signal_timestamp="2026-01-05T14:00:00Z")]
        replace_signals(backtest.id, first_run, db_path=db_path)

        second_run = [
            _signal(backtest.id, signal_timestamp="2026-02-01T14:00:00Z"),
            _signal(backtest.id, signal_timestamp="2026-02-02T14:00:00Z"),
        ]
        replace_signals(backtest.id, second_run, db_path=db_path)

        loaded = get_signals(backtest.id, db_path=db_path)
        assert len(loaded) == 2  # not 3 -- the first run's signal is gone, not accumulated
        assert loaded == second_run

    def test_signals_are_ordered_oldest_signal_first(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        backtest = _backtest()
        save_backtest(backtest, db_path=db_path)
        signals = [
            _signal(backtest.id, signal_timestamp="2026-01-10T14:00:00Z"),
            _signal(backtest.id, signal_timestamp="2026-01-05T14:00:00Z"),
            _signal(backtest.id, signal_timestamp="2026-01-15T14:00:00Z"),
        ]

        replace_signals(backtest.id, signals, db_path=db_path)
        loaded = get_signals(backtest.id, db_path=db_path)

        assert [s.signal_timestamp.day for s in loaded] == [5, 10, 15]

    def test_signals_for_a_different_backtest_do_not_leak_in(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        backtest_a = _backtest(experiment_id="exp-a")
        backtest_b = _backtest(experiment_id="exp-b")
        save_backtest(backtest_a, db_path=db_path)
        save_backtest(backtest_b, db_path=db_path)

        replace_signals(backtest_a.id, [_signal(backtest_a.id)], db_path=db_path)

        assert len(get_signals(backtest_a.id, db_path=db_path)) == 1
        assert get_signals(backtest_b.id, db_path=db_path) == []
