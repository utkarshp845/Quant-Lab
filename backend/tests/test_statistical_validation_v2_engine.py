"""End-to-end tests for app/statistical_validation/v2/engine.py::
build_statistical_validation_report_v2() -- against a synthetic,
deterministic dataset seeded through the real Feature Engine, Research
v1, and Backtesting v1 (never a hand-rolled stand-in for any of the
three), the same convention tests/test_statistical_validation_engine.py
(V1's own engine test) already uses. Deliberately similar fixture
shape to V1's engine test, extended with more bars so the moving block
bootstrap (Method B) has enough baseline observations to draw multiple
distinct block-start positions from.
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
from app.statistical_validation.v2.engine import build_statistical_validation_report_v2
from app.storage.backtest_repository import replace_signals, save_backtest
from app.storage.feature_repository import save_features
from app.storage.historical_bar_repository import save_bars
from app.storage.research_repository import save_experiment

_WINDOWS = [1, 3, 5]
_PRIMARY = 3


def _generate_bars(symbol="TSLA", *, n=300, seed=0, provider="csv", timeframe="5m") -> list[HistoricalBar]:
    """A reproducible synthetic 5-minute series (larger than V1's own
    140-bar engine fixture, so Method B's moving block bootstrap has
    plenty of valid block-start positions to draw from) with several
    deliberately spliced-in drop clusters, mirroring
    tests/test_statistical_validation_engine.py's own fixture shape."""
    rnd = random.Random(seed)
    base = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
    drop_indices = {30, 80, 81, 82, 150, 151, 220}

    closes = [100.0]
    for i in range(1, n):
        pct = -0.025 if i in drop_indices else rnd.uniform(-0.003, 0.003)
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
    from app.backtesting.engine import run_backtest

    bars = _generate_bars(symbol=symbol, provider=provider, timeframe=timeframe)
    save_bars(bars, db_path=db_path)

    feature_records = compute_features(
        symbol=symbol, timeframe=timeframe, provider=provider, bars=bars, calculated_at=datetime.now(timezone.utc)
    )
    save_features(feature_records, db_path=db_path)

    request = ExperimentCreateRequest(
        name="Synthetic Selling Continuation V2", hypothesis="test fixture",
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
    def test_population_counts_reconcile(self, tmp_path):
        db_path = tmp_path / "sv2.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report_v2(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=_PRIMARY, n_resamples=500, db_path=db_path
        )

        pop = report.population
        assert pop.window_bars == _PRIMARY
        assert pop.raw_conditioned_signals >= pop.conditioned_episodes
        assert pop.conditioned_episodes == report.method_a_mean_difference.n_conditioned
        assert pop.conditioned_episodes == report.method_b_mean_difference.n_conditioned
        assert pop.method_a_effective_baseline_n == report.method_a_mean_difference.n_baseline
        assert pop.method_a_effective_baseline_n < pop.baseline_raw_observations  # Method A discards overlap
        assert pop.method_b_block_length == 4 * _PRIMARY
        assert pop.method_b_block_count == -(-pop.baseline_raw_observations // pop.method_b_block_length)

    def test_secondary_horizons_exclude_the_primary_window(self, tmp_path):
        db_path = tmp_path / "sv2.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report_v2(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=_PRIMARY, n_resamples=500, db_path=db_path
        )

        secondary_windows = [h.window_bars for h in report.secondary_horizons]
        assert _PRIMARY not in secondary_windows
        assert set(secondary_windows) == set(_WINDOWS) - {_PRIMARY}

    def test_cis_are_properly_ordered_for_both_methods(self, tmp_path):
        db_path = tmp_path / "sv2.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report_v2(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=_PRIMARY, n_resamples=500, db_path=db_path
        )

        assert report.method_a_mean_difference.ci_low <= report.method_a_mean_difference.ci_high
        assert report.method_b_mean_difference.ci_low <= report.method_b_mean_difference.ci_high
        assert report.method_a_win_rate_difference.ci_low_pp <= report.method_a_win_rate_difference.ci_high_pp
        assert report.method_b_win_rate_difference.ci_low_pp <= report.method_b_win_rate_difference.ci_high_pp

    def test_both_methods_p_values_are_valid_probabilities(self, tmp_path):
        db_path = tmp_path / "sv2.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report_v2(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=_PRIMARY, n_resamples=500, db_path=db_path
        )

        assert 0 < report.method_a_test.p_value_two_sided <= 1
        assert 0 < report.method_b_test.p_value_two_sided <= 1
        assert report.method_a_test.method.value == "non_overlapping_windows"
        assert report.method_b_test.method.value == "moving_block_bootstrap"


class TestPowerAnalysis:
    def test_power_analysis_uses_the_episode_and_method_a_baseline_counts(self, tmp_path):
        db_path = tmp_path / "sv2.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report_v2(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=_PRIMARY, n_resamples=500, db_path=db_path
        )

        pa = report.power_analysis
        assert pa.n_conditioned_episodes == report.population.conditioned_episodes
        assert pa.n_baseline_effective == report.population.method_a_effective_baseline_n
        assert pa.minimum_detectable_effect_size > 0
        assert pa.observed_effect_size == pytest.approx(report.effect_size.cohens_d)
        assert pa.observed_effect_below_detectable_threshold == (abs(pa.observed_effect_size) < pa.minimum_detectable_effect_size)


class TestRobustnessComparison:
    def test_robustness_section_carries_both_methods_full_results(self, tmp_path):
        db_path = tmp_path / "sv2.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report_v2(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=_PRIMARY, n_resamples=500, db_path=db_path
        )

        rc = report.robustness
        assert rc.method_a_mean_difference == report.method_a_mean_difference
        assert rc.method_b_mean_difference == report.method_b_mean_difference
        assert isinstance(rc.conclusion_changes_materially, bool)

    def test_conclusion_changes_materially_matches_a_zero_exclusion_disagreement(self, tmp_path):
        db_path = tmp_path / "sv2.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report_v2(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=_PRIMARY, n_resamples=500, db_path=db_path
        )

        a = report.method_a_mean_difference
        b = report.method_b_mean_difference
        zero_excluded_a = not (a.ci_low <= 0 <= a.ci_high)
        zero_excluded_b = not (b.ci_low <= 0 <= b.ci_high)
        assert report.robustness.conclusion_changes_materially == (zero_excluded_a != zero_excluded_b)


class TestDeterminism:
    def test_same_seed_produces_a_byte_identical_report(self, tmp_path):
        db_path = tmp_path / "sv2.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        first = build_statistical_validation_report_v2(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=_PRIMARY, seed=1337, n_resamples=300, db_path=db_path
        )
        second = build_statistical_validation_report_v2(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=_PRIMARY, seed=1337, n_resamples=300, db_path=db_path
        )

        assert first.model_dump(exclude={"generated_at"}) == second.model_dump(exclude={"generated_at"})

    def test_seed_1337_is_the_documented_default(self, tmp_path):
        db_path = tmp_path / "sv2.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        report = build_statistical_validation_report_v2(
            experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=_PRIMARY, n_resamples=300, db_path=db_path
        )

        assert report.seed == 1337


class TestErrorHandling:
    def test_missing_experiment_id_raises(self, tmp_path):
        db_path = tmp_path / "sv2.db"
        _, backtest = _seed_full_pipeline(db_path)

        with pytest.raises(ValueError, match="No experiment"):
            build_statistical_validation_report_v2(experiment_id="does-not-exist", backtest_id=backtest.id, primary_window_bars=_PRIMARY, db_path=db_path)

    def test_missing_backtest_id_raises(self, tmp_path):
        db_path = tmp_path / "sv2.db"
        experiment, _ = _seed_full_pipeline(db_path)

        with pytest.raises(ValueError, match="No backtest"):
            build_statistical_validation_report_v2(experiment_id=experiment.id, backtest_id="does-not-exist", primary_window_bars=_PRIMARY, db_path=db_path)

    def test_backtest_not_referencing_the_given_experiment_raises(self, tmp_path):
        db_path = tmp_path / "sv2.db"
        experiment_a, backtest_a = _seed_full_pipeline(db_path, symbol="TSLA")
        experiment_b, _ = _seed_full_pipeline(db_path, symbol="NVDA")

        with pytest.raises(ValueError, match="does not reference"):
            build_statistical_validation_report_v2(experiment_id=experiment_b.id, backtest_id=backtest_a.id, primary_window_bars=_PRIMARY, db_path=db_path)

    def test_primary_window_not_in_the_backtests_configured_windows_raises(self, tmp_path):
        db_path = tmp_path / "sv2.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        with pytest.raises(ValueError, match="primary_window_bars"):
            build_statistical_validation_report_v2(experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=999, db_path=db_path)

    def test_stale_persisted_signals_are_detected_not_silently_used(self, tmp_path):
        db_path = tmp_path / "sv2.db"
        experiment, backtest = _seed_full_pipeline(db_path)

        replace_signals(backtest.id, [], db_path=db_path)

        with pytest.raises(ValueError, match="did not reproduce"):
            build_statistical_validation_report_v2(experiment_id=experiment.id, backtest_id=backtest.id, primary_window_bars=_PRIMARY, db_path=db_path)
