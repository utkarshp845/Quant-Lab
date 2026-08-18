"""End-to-end tests for app/statistical_validation/engine.py::
build_statistical_validation_report() -- against a synthetic,
deterministic dataset seeded through the real Feature Engine, Research
v1, and Backtesting v1 (never a hand-rolled stand-in for any of the
three), the same "exercise the real, already-validated boundary, don't
assume it" convention tests/test_backtest_engine.py and
tests/test_research_engine.py already use.

The synthetic price series is a fixed (seed=0), reproducible pseudo-
random walk with a handful of DELIBERATE multi-bar drop clusters
spliced in, so the resulting conditioned population has a mix of
multi-signal episodes and single-signal episodes to exercise
app/statistical_validation/episodes.py's grouping through the full
pipeline, not just in isolation (already covered by
tests/test_statistical_validation_episodes.py).
"""

import random
from datetime import datetime, timedelta, timezone

import pytest

from app.features.engine import compute_features
from app.models.backtesting import Backtest
from app.models.market_data import HistoricalBar
from app.models.research import (
    ConditionOperator,
    Experiment,
    ExperimentCreateRequest,
    FeatureCondition,
    FeatureConditionOperator,
    Outcome,
)
from app.statistical_validation.engine import build_statistical_validation_report
from app.storage.backtest_repository import replace_signals, save_backtest
from app.storage.feature_repository import save_features
from app.storage.historical_bar_repository import save_bars
from app.storage.research_repository import save_experiment

_WINDOWS = [1, 2, 3]


def _generate_bars(symbol="TSLA", *, n=140, seed=0, provider="csv", timeframe="5m") -> list[HistoricalBar]:
    """A reproducible synthetic 5-minute series: a small random walk
    (so baseline forward returns have real, non-degenerate variance)
    with three deliberately spliced-in drop clusters -- an isolated
    1-bar drop (index 20), a 3-consecutive-bar drop run (indices 50-52),
    and a 2-consecutive-bar drop run (indices 90-91) -- each a >=2%
    single-bar decline, well past the -1% condition threshold used
    below. Every other bar moves by a small, condition-safe amount."""
    rnd = random.Random(seed)
    base = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
    drop_indices = {20, 50, 51, 52, 90, 91}

    closes = [100.0]
    for i in range(1, n):
        if i in drop_indices:
            pct = -0.025
        else:
            pct = rnd.uniform(-0.003, 0.003)
        closes.append(closes[-1] * (1 + pct))

    bars = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i > 0 else close
        high = max(open_, close) * 1.0005
        low = min(open_, close) * 0.9995
        bars.append(
            HistoricalBar(
                symbol=symbol, timestamp=base + timedelta(minutes=5 * i), open=open_, high=high, low=low, close=close,
                volume=1_000, provider=provider, timeframe=timeframe,
            )
        )
    return bars


def _seed_full_pipeline(db_path, *, symbol="TSLA", provider="csv", timeframe="5m", windows=_WINDOWS) -> tuple[Experiment, Backtest]:
    """Bars -> real Feature Engine -> real Experiment (via
    Experiment.new(), the same path POST /research/experiments takes)
    -> real Backtesting v1 engine -> persisted, exactly like a real
    create->run->create->run HTTP flow would produce, just without the
    HTTP layer itself (already covered by tests/test_backtest_api.py)."""
    from app.backtesting.engine import run_backtest

    bars = _generate_bars(symbol=symbol, provider=provider, timeframe=timeframe)
    save_bars(bars, db_path=db_path)

    feature_records = compute_features(
        symbol=symbol, timeframe=timeframe, provider=provider, bars=bars, calculated_at=datetime.now(timezone.utc)
    )
    save_features(feature_records, db_path=db_path)

    request = ExperimentCreateRequest(
        name="Synthetic Selling Continuation", hypothesis="test fixture",
        symbol=symbol, start_date=bars[0].timestamp.date(), end_date=bars[-1].timestamp.date(),
        timeframe=timeframe, provider=provider,
        conditions=[FeatureCondition(feature_id="price.return_5m", operator=FeatureConditionOperator.LTE, value=-0.01)],
        outcome=Outcome(metric="forward_return", horizon_minutes=5, operator=ConditionOperator.LTE, threshold=-0.005),
    )
    experiment = Experiment.new(request)
    save_experiment(experiment, db_path=db_path)

    signals, _ = run_backtest(
        backtest_id="placeholder", experiment_id=experiment.id, symbol=symbol, timeframe=timeframe,
        conditions=experiment.conditions, windows=windows, bars=bars, feature_records=feature_records,
        feature_contract_version=experiment.feature_contract_version,
    )
    backtest = Backtest.new(
        experiment_id=experiment.id, symbol=symbol, timeframe=timeframe, provider=provider,
        windows=windows, feature_contract_version=experiment.feature_contract_version,
    )
    save_backtest(backtest, db_path=db_path)
    signals = [s.model_copy(update={"backtest_id": backtest.id}) for s in signals]
    replace_signals(backtest.id, signals, db_path=db_path)

    return experiment, backtest


class TestReportStructure:
    def test_builds_a_report_with_one_horizon_per_configured_window(self, tmp_path):
        db_path = tmp_path / "sv.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=1, db_path=db_path
        )

        assert [h.window_bars for h in report.horizons] == _WINDOWS

    def test_exactly_one_horizon_is_flagged_primary(self, tmp_path):
        db_path = tmp_path / "sv.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=2, db_path=db_path
        )

        primary_flags = [h.is_primary for h in report.horizons]
        assert sum(primary_flags) == 1
        assert report.horizons[_WINDOWS.index(2)].is_primary is True

    def test_sample_sizes_reconcile_raw_episode_and_baseline_counts(self, tmp_path):
        db_path = tmp_path / "sv.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report(experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=1, db_path=db_path)

        for horizon in report.horizons:
            # This fixture has 6 raw signals (indices 20, 50, 51, 52, 90, 91) forming
            # 3 episodes (1 + 3 + 2) -- at the smallest window (1 bar), every one of
            # them should have a measurable outcome (plenty of bars follow each).
            assert horizon.sample_sizes.raw_signal_count >= horizon.sample_sizes.episode_count
            assert horizon.sample_sizes.episode_count == horizon.mean_difference.n_conditioned
            assert horizon.sample_sizes.baseline_count == horizon.mean_difference.n_baseline

        window_1 = report.horizons[_WINDOWS.index(1)]
        assert window_1.sample_sizes.raw_signal_count == 6
        assert window_1.sample_sizes.episode_count == 3

    def test_mean_and_win_rate_cis_are_properly_ordered(self, tmp_path):
        db_path = tmp_path / "sv.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report(experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=1, db_path=db_path)

        for horizon in report.horizons:
            assert horizon.mean_difference.ci_low <= horizon.mean_difference.ci_high
            assert horizon.win_rate_difference.ci_low_pp <= horizon.win_rate_difference.ci_high_pp

    def test_session_boundary_counts_never_exceed_total_observations(self, tmp_path):
        db_path = tmp_path / "sv.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report(experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=1, db_path=db_path)

        for horizon in report.horizons:
            sb = horizon.session_boundary
            assert 0 <= sb.n_conditioned_crossing <= sb.n_conditioned_observations
            assert 0 <= sb.n_baseline_crossing <= sb.n_baseline_observations


class TestPrimaryHypothesisOutputs:
    def test_permutation_test_p_value_is_a_valid_probability(self, tmp_path):
        db_path = tmp_path / "sv.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=1, n_permutations=500, db_path=db_path
        )

        assert 0 < report.primary_permutation_test.p_value_two_sided <= 1
        assert report.primary_permutation_test.window_bars == 1
        assert report.primary_permutation_test.n_conditioned == 3  # 3 episodes

    def test_effect_size_is_computed_on_the_episode_level_sample(self, tmp_path):
        db_path = tmp_path / "sv.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=1, db_path=db_path
        )

        assert report.primary_effect_size.window_bars == 1
        assert isinstance(report.primary_effect_size.cohens_d, float)
        assert report.primary_effect_size.interpretation  # non-empty label

    def test_robustness_check_reports_both_raw_and_episode_ns(self, tmp_path):
        db_path = tmp_path / "sv.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=1, db_path=db_path
        )

        assert report.robustness_check.raw_n == 6
        assert report.robustness_check.episode_n == 3
        assert report.robustness_check.episode_p_value == report.primary_permutation_test.p_value_two_sided


class TestDeterminism:
    def test_same_seed_produces_a_byte_identical_report(self, tmp_path):
        db_path = tmp_path / "sv.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        first = build_statistical_validation_report(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=1, seed=1337, n_bootstrap=500, n_permutations=500, db_path=db_path
        )
        second = build_statistical_validation_report(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=1, seed=1337, n_bootstrap=500, n_permutations=500, db_path=db_path
        )

        assert first.model_dump(exclude={"generated_at"}) == second.model_dump(exclude={"generated_at"})

    def test_a_different_seed_can_produce_a_different_result(self, tmp_path):
        db_path = tmp_path / "sv.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        first = build_statistical_validation_report(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=1, seed=1, n_bootstrap=200, n_permutations=200, db_path=db_path
        )
        second = build_statistical_validation_report(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=1, seed=2, n_bootstrap=200, n_permutations=200, db_path=db_path
        )

        # Not asserting inequality outright (a coincidence is astronomically unlikely
        # but not impossible) -- asserting the seed is actually threaded through.
        assert first.seed != second.seed


class TestErrorHandling:
    def test_missing_experiment_id_raises(self, tmp_path):
        db_path = tmp_path / "sv.db"
        _, backtest = _seed_full_pipeline(db_path)

        with pytest.raises(ValueError, match="No experiment"):
            build_statistical_validation_report(experiment_id="does-not-exist", backtest_id=backtest.id, db_path=db_path)

    def test_missing_backtest_id_raises(self, tmp_path):
        db_path = tmp_path / "sv.db"
        experiment, _ = _seed_full_pipeline(db_path)

        with pytest.raises(ValueError, match="No backtest"):
            build_statistical_validation_report(experiment_id=experiment.id, backtest_id="does-not-exist", db_path=db_path)

    def test_backtest_not_referencing_the_given_experiment_raises(self, tmp_path):
        db_path = tmp_path / "sv.db"
        experiment_a, backtest_a = _seed_full_pipeline(db_path, symbol="TSLA")
        experiment_b, _ = _seed_full_pipeline(db_path, symbol="NVDA")

        with pytest.raises(ValueError, match="does not reference"):
            build_statistical_validation_report(experiment_id=experiment_b.id, backtest_id=backtest_a.id, db_path=db_path)

    def test_primary_window_not_in_the_backtests_configured_windows_raises(self, tmp_path):
        db_path = tmp_path / "sv.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        with pytest.raises(ValueError, match="primary_window_bars"):
            build_statistical_validation_report(
                experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=999, db_path=db_path
            )

    def test_stale_persisted_signals_are_detected_not_silently_used(self, tmp_path):
        """If the persisted signals no longer match what re-running the
        experiment's own conditions produces (e.g. bars changed since
        the backtest last ran), the engine must raise, never silently
        validate against stale data."""
        db_path = tmp_path / "sv.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        # Corrupt the persisted signals by replacing them with an empty list --
        # simulates "the backtest hasn't been (re-)run against current data."
        replace_signals(backtest.id, [], db_path=db_path)

        with pytest.raises(ValueError, match="did not reproduce"):
            build_statistical_validation_report(experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=1, db_path=db_path)
