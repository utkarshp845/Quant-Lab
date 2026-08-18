"""Statistical Validation V2's orchestrator (app/statistical_validation/v2/)
-- corrects V1's one flagged weakness (the unconditional baseline
treated ~4,381 heavily overlapping per-bar forward-return windows as
independent observations) while keeping V1's conditioned-side treatment
(episode-level, non-overlapping) and V1's OWN unconditional-baseline
construction exactly as they are.

Reuses, never modifies:
  - app.storage.research_repository / backtest_repository /
    historical_bar_repository / feature_repository -- read-only.
  - app.backtesting.engine.run_backtest() -- the UNMODIFIED function,
    via app.statistical_validation.baseline.compute_unconditional_baseline()
    (also unmodified) and this module's own small, local
    reproduce-and-verify step (duplicated from
    app.statistical_validation.engine's PRIVATE helper of the same
    purpose -- V1's module is intentionally left untouched, including
    its private functions, so this ~10-line verification is
    re-implemented here rather than importing a private symbol across
    modules; the STATISTICS are never duplicated, only this orchestration
    glue).
  - app.statistical_validation.episodes.group_into_episodes() /
    episode_representatives() -- V1's own, unmodified episode
    definition, per this feature's own "keep this definition exactly"
    requirement.
  - app.statistical_validation.resampling's bootstrap_mean_difference_ci()
    / bootstrap_win_rate_ci() / permutation_test_mean_difference() /
    cohens_d() -- V1's own, unmodified functions, applied to Method A's
    non-overlapping baseline subsample (see app/statistical_validation/
    v2/baseline.py).
  - app.research.metrics.timeframe_minutes() -- read-only, same
    precedent V1's own engine already established for not adding a
    third copy of the timeframe->minutes mapping.

New in V2: app/statistical_validation/v2/baseline.py (Method A:
non-overlapping windows), app/statistical_validation/v2/resampling.py
(Method B: moving block bootstrap), app/statistical_validation/v2/
power.py (post-hoc minimum detectable effect size).

No new database table, no HTTP route -- run via
scripts/run_statistical_validation_v2.py, matching V1's own "manual,
opt-in script" convention.
"""

from datetime import datetime, timedelta, timezone

import numpy as np

from app.backtesting.engine import run_backtest
from app.models.backtesting import Backtest, BacktestSignal
from app.models.research import Experiment
from app.models.statistical_validation_v2 import (
    BaselineMethodV2,
    DependenceAwareTestResultV2,
    DescriptiveHorizonResultV2,
    EffectSizeResultV2,
    MeanDifferenceResultV2,
    PopulationSummaryV2,
    PowerAnalysisResultV2,
    RobustnessComparisonV2,
    StatisticalValidationReportV2,
    WinRateDifferenceResultV2,
)
from app.research.metrics import timeframe_minutes
from app.statistical_validation.baseline import compute_unconditional_baseline
from app.statistical_validation.episodes import episode_representatives, group_into_episodes
from app.statistical_validation.resampling import (
    bootstrap_mean_difference_ci,
    bootstrap_win_rate_ci,
    cohens_d,
    permutation_test_mean_difference,
)
from app.statistical_validation.v2.baseline import non_overlapping_baseline
from app.statistical_validation.v2.power import minimum_detectable_effect_size
from app.statistical_validation.v2.resampling import (
    moving_block_bootstrap_mean_difference_ci,
    moving_block_bootstrap_p_value,
    moving_block_bootstrap_win_rate_ci,
)
from app.storage import backtest_repository, feature_repository, historical_bar_repository, research_repository

DEFAULT_SEED = 1337
DEFAULT_N_RESAMPLES = 10_000
DEFAULT_CI_LEVEL = 0.95
DEFAULT_BLOCK_LENGTH_MULTIPLIER = 4
DEFAULT_POWER = 0.80


def build_statistical_validation_report_v2(
    *,
    experiment_id: str,
    backtest_id: str,
    primary_window_bars: int = 5,
    seed: int = DEFAULT_SEED,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    ci_level: float = DEFAULT_CI_LEVEL,
    block_length_multiplier: int = DEFAULT_BLOCK_LENGTH_MULTIPLIER,
    power: float = DEFAULT_POWER,
    db_path=None,
) -> StatisticalValidationReportV2:
    """Same id/window validation and stale-data detection as V1's
    build_statistical_validation_report() (see that function's own
    docstring for the exact error conditions) -- raises ValueError,
    never silently produces a partial report, for: either id missing,
    the backtest not referencing the experiment, `primary_window_bars`
    not among the backtest's configured windows, or the persisted
    signals no longer matching what the experiment's own conditions
    reproduce.
    """
    experiment = research_repository.get_experiment(experiment_id, db_path=db_path)
    if experiment is None:
        raise ValueError(f"No experiment with id {experiment_id!r}")
    backtest = backtest_repository.get_backtest(backtest_id, db_path=db_path)
    if backtest is None:
        raise ValueError(f"No backtest with id {backtest_id!r}")
    if backtest.experiment_id != experiment_id:
        raise ValueError(f"Backtest {backtest_id!r} does not reference experiment {experiment_id!r}")
    if primary_window_bars not in backtest.windows:
        raise ValueError(
            f"primary_window_bars={primary_window_bars} is not one of this backtest's configured windows {backtest.windows}"
        )

    bars = historical_bar_repository.get_bars(
        symbol=experiment.symbol, timeframe=experiment.timeframe, provider=experiment.provider,
        start=experiment.start_date, end=experiment.end_date, db_path=db_path,
    )
    feature_records = feature_repository.get_features(
        symbol=experiment.symbol, timeframe=experiment.timeframe, provider=experiment.provider,
        start=experiment.start_date, end=experiment.end_date, db_path=db_path,
    )

    conditioned_signals = _reproduce_and_verify_conditioned_signals(
        experiment=experiment, backtest=backtest, bars=bars, feature_records=feature_records, db_path=db_path
    )
    baseline_signals = compute_unconditional_baseline(
        symbol=experiment.symbol, timeframe=experiment.timeframe, windows=backtest.windows,
        bars=bars, feature_records=feature_records, feature_contract_version=backtest.feature_contract_version,
    )

    bar_interval = timedelta(minutes=timeframe_minutes(experiment.timeframe))
    episodes = group_into_episodes(conditioned_signals, bar_interval=bar_interval)
    episode_signals = episode_representatives(episodes)

    rng = np.random.default_rng(seed)
    block_length = block_length_multiplier * primary_window_bars

    # ---- Primary horizon: both dependence-aware methods ----
    episode_returns = _returns_for_window(episode_signals, primary_window_bars)

    method_a_signals = non_overlapping_baseline(baseline_signals, window_bars=primary_window_bars, bar_interval=bar_interval)
    method_a_returns = [o.forward_return for s in method_a_signals for o in s.outcomes if o.window_bars == primary_window_bars]

    method_b_returns = _returns_in_chronological_order(baseline_signals, primary_window_bars)

    method_a_mean_diff = _method_a_mean_difference(episode_returns, method_a_returns, window_bars=primary_window_bars, rng=rng, n_resamples=n_resamples, ci_level=ci_level)
    method_a_win_diff = _method_a_win_rate_difference(episode_returns, method_a_returns, window_bars=primary_window_bars, rng=rng, n_resamples=n_resamples, ci_level=ci_level)
    method_a_observed, method_a_p = permutation_test_mean_difference(episode_returns, method_a_returns, rng=rng, n_permutations=n_resamples)
    method_a_test = DependenceAwareTestResultV2(
        method=BaselineMethodV2.NON_OVERLAPPING_WINDOWS, window_bars=primary_window_bars,
        observed_mean_difference=method_a_observed, p_value_two_sided=method_a_p, n_resamples=n_resamples,
        n_conditioned=len(episode_returns), n_baseline=len(method_a_returns), seed=seed,
    )

    method_b_mean_diff = _method_b_mean_difference(episode_returns, method_b_returns, window_bars=primary_window_bars, block_length=block_length, rng=rng, n_resamples=n_resamples, ci_level=ci_level)
    method_b_win_diff = _method_b_win_rate_difference(episode_returns, method_b_returns, window_bars=primary_window_bars, block_length=block_length, rng=rng, n_resamples=n_resamples, ci_level=ci_level)
    method_b_observed, method_b_p = moving_block_bootstrap_p_value(episode_returns, method_b_returns, block_length=block_length, rng=rng, n_resamples=n_resamples)
    method_b_test = DependenceAwareTestResultV2(
        method=BaselineMethodV2.MOVING_BLOCK_BOOTSTRAP, window_bars=primary_window_bars,
        observed_mean_difference=method_b_observed, p_value_two_sided=method_b_p, n_resamples=n_resamples,
        n_conditioned=len(episode_returns), n_baseline=len(method_b_returns), seed=seed,
    )

    d, pooled_stdev = cohens_d(episode_returns, method_a_returns)
    effect_size = EffectSizeResultV2(
        window_bars=primary_window_bars, cohens_d=d, pooled_stdev=pooled_stdev,
        interpretation=_interpret_cohens_d(d), method=BaselineMethodV2.NON_OVERLAPPING_WINDOWS,
    )

    mdes = minimum_detectable_effect_size(len(episode_returns), len(method_a_returns), power=power)
    power_analysis = PowerAnalysisResultV2(
        n_conditioned_episodes=len(episode_returns), n_baseline_effective=len(method_a_returns),
        alpha=0.05, power=power, minimum_detectable_effect_size=mdes, observed_effect_size=d,
        observed_effect_below_detectable_threshold=abs(d) < mdes,
    )

    zero_excluded_a = not (method_a_mean_diff.ci_low <= 0 <= method_a_mean_diff.ci_high)
    zero_excluded_b = not (method_b_mean_diff.ci_low <= 0 <= method_b_mean_diff.ci_high)
    robustness = RobustnessComparisonV2(
        window_bars=primary_window_bars,
        method_a_mean_difference=method_a_mean_diff, method_a_test=method_a_test,
        method_b_mean_difference=method_b_mean_diff, method_b_test=method_b_test,
        conclusion_changes_materially=(zero_excluded_a != zero_excluded_b),
    )

    population = PopulationSummaryV2(
        window_bars=primary_window_bars,
        raw_conditioned_signals=len(_returns_for_window(conditioned_signals, primary_window_bars)),
        conditioned_episodes=len(episode_returns),
        baseline_raw_observations=len(method_b_returns),
        method_a_effective_baseline_n=len(method_a_returns),
        method_b_block_length=block_length,
        method_b_block_count=-(-len(method_b_returns) // block_length),  # ceil division
    )

    # ---- Secondary horizons: descriptive only, Method A baseline, no inference ----
    secondary_horizons = [
        _descriptive_horizon(window_bars, episode_signals=episode_signals, baseline_signals=baseline_signals, conditioned_signals=conditioned_signals, bar_interval=bar_interval)
        for window_bars in backtest.windows
        if window_bars != primary_window_bars
    ]

    return StatisticalValidationReportV2(
        experiment_id=experiment_id, backtest_id=backtest_id, primary_window_bars=primary_window_bars,
        generated_at=datetime.now(timezone.utc), seed=seed, n_resamples=n_resamples, ci_level=ci_level,
        block_length_multiplier=block_length_multiplier, population=population,
        method_a_mean_difference=method_a_mean_diff, method_a_win_rate_difference=method_a_win_diff, method_a_test=method_a_test,
        method_b_mean_difference=method_b_mean_diff, method_b_win_rate_difference=method_b_win_diff, method_b_test=method_b_test,
        effect_size=effect_size, power_analysis=power_analysis, robustness=robustness, secondary_horizons=secondary_horizons,
    )


def _method_a_mean_difference(episode_returns, baseline_returns, *, window_bars, rng, n_resamples, ci_level) -> MeanDifferenceResultV2:
    lo, hi = bootstrap_mean_difference_ci(episode_returns, baseline_returns, rng=rng, n_resamples=n_resamples, ci_level=ci_level)
    cond_mean, base_mean = float(np.mean(episode_returns)), float(np.mean(baseline_returns))
    return MeanDifferenceResultV2(
        method=BaselineMethodV2.NON_OVERLAPPING_WINDOWS, window_bars=window_bars, conditioned_mean=cond_mean,
        baseline_mean=base_mean, difference=cond_mean - base_mean, ci_low=lo, ci_high=hi, ci_level=ci_level,
        n_conditioned=len(episode_returns), n_baseline=len(baseline_returns),
    )


def _method_a_win_rate_difference(episode_returns, baseline_returns, *, window_bars, rng, n_resamples, ci_level) -> WinRateDifferenceResultV2:
    lo, hi = bootstrap_win_rate_ci(episode_returns, baseline_returns, rng=rng, n_resamples=n_resamples, ci_level=ci_level)
    cond_win = sum(1 for r in episode_returns if r > 0) / len(episode_returns)
    base_win = sum(1 for r in baseline_returns if r > 0) / len(baseline_returns)
    return WinRateDifferenceResultV2(
        method=BaselineMethodV2.NON_OVERLAPPING_WINDOWS, window_bars=window_bars, conditioned_win_rate=cond_win,
        baseline_win_rate=base_win, difference_pp=(cond_win - base_win) * 100, ci_low_pp=lo * 100, ci_high_pp=hi * 100,
        ci_level=ci_level, n_conditioned=len(episode_returns), n_baseline=len(baseline_returns),
    )


def _method_b_mean_difference(episode_returns, baseline_series, *, window_bars, block_length, rng, n_resamples, ci_level) -> MeanDifferenceResultV2:
    lo, hi = moving_block_bootstrap_mean_difference_ci(episode_returns, baseline_series, block_length=block_length, rng=rng, n_resamples=n_resamples, ci_level=ci_level)
    cond_mean, base_mean = float(np.mean(episode_returns)), float(np.mean(baseline_series))
    return MeanDifferenceResultV2(
        method=BaselineMethodV2.MOVING_BLOCK_BOOTSTRAP, window_bars=window_bars, conditioned_mean=cond_mean,
        baseline_mean=base_mean, difference=cond_mean - base_mean, ci_low=lo, ci_high=hi, ci_level=ci_level,
        n_conditioned=len(episode_returns), n_baseline=len(baseline_series),
    )


def _method_b_win_rate_difference(episode_returns, baseline_series, *, window_bars, block_length, rng, n_resamples, ci_level) -> WinRateDifferenceResultV2:
    lo, hi = moving_block_bootstrap_win_rate_ci(episode_returns, baseline_series, block_length=block_length, rng=rng, n_resamples=n_resamples, ci_level=ci_level)
    cond_win = sum(1 for r in episode_returns if r > 0) / len(episode_returns)
    base_win = sum(1 for r in baseline_series if r > 0) / len(baseline_series)
    return WinRateDifferenceResultV2(
        method=BaselineMethodV2.MOVING_BLOCK_BOOTSTRAP, window_bars=window_bars, conditioned_win_rate=cond_win,
        baseline_win_rate=base_win, difference_pp=(cond_win - base_win) * 100, ci_low_pp=lo * 100, ci_high_pp=hi * 100,
        ci_level=ci_level, n_conditioned=len(episode_returns), n_baseline=len(baseline_series),
    )


def _descriptive_horizon(window_bars, *, episode_signals, baseline_signals, conditioned_signals, bar_interval) -> DescriptiveHorizonResultV2:
    episode_returns = _returns_for_window(episode_signals, window_bars)
    method_a_signals = non_overlapping_baseline(baseline_signals, window_bars=window_bars, bar_interval=bar_interval)
    baseline_returns = [o.forward_return for s in method_a_signals for o in s.outcomes if o.window_bars == window_bars]

    cond_mean = float(np.mean(episode_returns)) if episode_returns else 0.0
    base_mean = float(np.mean(baseline_returns)) if baseline_returns else 0.0
    cond_win = (sum(1 for r in episode_returns if r > 0) / len(episode_returns)) if episode_returns else 0.0
    base_win = (sum(1 for r in baseline_returns if r > 0) / len(baseline_returns)) if baseline_returns else 0.0

    return DescriptiveHorizonResultV2(
        window_bars=window_bars,
        raw_signal_count=len(_returns_for_window(conditioned_signals, window_bars)),
        episode_count=len(episode_returns),
        conditioned_episode_mean=cond_mean, baseline_mean=base_mean, difference=cond_mean - base_mean,
        conditioned_win_rate=cond_win, baseline_win_rate=base_win,
    )


def _returns_for_window(signals: list[BacktestSignal], window_bars: int) -> list[float]:
    return [outcome.forward_return for signal in signals for outcome in signal.outcomes if outcome.window_bars == window_bars]


def _returns_in_chronological_order(signals: list[BacktestSignal], window_bars: int) -> list[float]:
    """Same values as _returns_for_window(), but sorted by
    entry_timestamp -- REQUIRED for the moving block bootstrap (Method
    B), which relies on the series' real chronological order to
    capture genuine temporal dependence; _returns_for_window() alone
    makes no ordering guarantee about its input."""
    ordered_signals = sorted(signals, key=lambda s: s.entry_timestamp)
    return [outcome.forward_return for signal in ordered_signals for outcome in signal.outcomes if outcome.window_bars == window_bars]


def _reproduce_and_verify_conditioned_signals(*, experiment: Experiment, backtest: Backtest, bars, feature_records, db_path) -> list[BacktestSignal]:
    """See app.statistical_validation.engine's own, private function of
    the same purpose -- duplicated here (not imported) because V1's
    module is intentionally left untouched by V2, including its
    private helpers. Re-runs the experiment's OWN conditions through
    the unmodified run_backtest() and asserts the result is
    byte-for-byte identical to what is already persisted for this
    backtest, raising ValueError (not a silent mismatch) if not."""
    conditioned_signals, _ = run_backtest(
        backtest_id=backtest.id, experiment_id=experiment.id, symbol=experiment.symbol, timeframe=experiment.timeframe,
        conditions=experiment.conditions, windows=backtest.windows, bars=bars, feature_records=feature_records,
        feature_contract_version=backtest.feature_contract_version,
    )
    persisted_signals = backtest_repository.get_signals(backtest.id, db_path=db_path)
    if conditioned_signals != persisted_signals:
        raise ValueError(
            f"Re-running backtest {backtest.id!r}'s own conditions did not reproduce its persisted signals "
            "exactly -- the underlying bars/features have likely changed since it last ran. "
            "Re-run the backtest (POST /backtests/{id}/run) before validating it statistically."
        )
    return conditioned_signals


def _interpret_cohens_d(d: float) -> str:
    magnitude = abs(d)
    if magnitude < 0.2:
        return "negligible (below the conventional 'small' threshold of 0.2)"
    if magnitude < 0.5:
        return "small (conventional Cohen's d threshold -- not a claim of statistical or economic significance)"
    if magnitude < 0.8:
        return "medium (conventional Cohen's d threshold -- not a claim of statistical or economic significance)"
    return "large (conventional Cohen's d threshold -- not a claim of statistical or economic significance)"
