"""Statistical Validation V1's orchestrator (app/statistical_validation/)
-- turns an already-run Backtest (app/backtesting/, already validated
by the Backtesting v1 audit and Baseline Analysis V1) into a
StatisticalValidationReport (app/models/statistical_validation.py):
does the conditioned population look different from the unconditional
baseline by more than random variation would explain, using the
EPISODE-level (non-overlapping) conditioned sample as the unit of
inference rather than the raw, clustered signal list.

Reuses, never modifies:
  - app.storage.research_repository / backtest_repository /
    historical_bar_repository / feature_repository -- read-only.
  - app.backtesting.engine.run_backtest() -- the UNMODIFIED function,
    called TWICE: once with the real experiment's own conditions (to
    reproduce, and verify against, the already-persisted signals --
    see _reproduce_and_verify_conditioned_signals() below), and once
    with a trivial control condition to build the unconditional
    baseline (app/statistical_validation/baseline.py).
  - app.research.metrics.timeframe_minutes() -- to convert this
    backtest's own timeframe into the bar interval
    app/statistical_validation/episodes.py needs, rather than adding a
    THIRD copy of that mapping (app/research/metrics.py and
    app/features/timeframes.py already each keep their own, per this
    app's established precedent of small, per-layer copies -- reading
    one of the two existing ones is not "duplicating logic," it is the
    one place this module needs that single fact).

No new database table and no new HTTP route in v1 -- this is a pure,
deterministic (given a fixed `seed`) computation. A human runs it via
scripts/run_statistical_validation.py, matching this app's existing
"manual, opt-in script for a real analysis, not baked into the HTTP
surface" convention (see scripts/backfill_historical_data.py).
"""

from datetime import datetime, timedelta, timezone

import numpy as np

from app.backtesting.engine import run_backtest
from app.models.backtesting import Backtest, BacktestSignal
from app.models.research import Experiment
from app.models.statistical_validation import (
    EffectSizeResult,
    EpisodeGroupingRule,
    HorizonResult,
    MeanDifferenceCI,
    PermutationTestResult,
    RobustnessCheck,
    SampleSizes,
    SessionBoundaryStats,
    StatisticalValidationReport,
    WinRateDifferenceCI,
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
from app.storage import backtest_repository, feature_repository, historical_bar_repository, research_repository

DEFAULT_SEED = 1337
DEFAULT_N_BOOTSTRAP = 10_000
DEFAULT_N_PERMUTATIONS = 10_000
DEFAULT_CI_LEVEL = 0.95


def build_statistical_validation_report(
    *,
    experiment_id: str,
    backtest_id: str,
    primary_window_bars: int = 5,
    seed: int = DEFAULT_SEED,
    n_bootstrap: int = DEFAULT_N_BOOTSTRAP,
    n_permutations: int = DEFAULT_N_PERMUTATIONS,
    ci_level: float = DEFAULT_CI_LEVEL,
    db_path=None,
) -> StatisticalValidationReport:
    """Reads `experiment_id`/`backtest_id`, rebuilds both populations
    from the same underlying bars/features the real backtest used, and
    returns the full report. Raises ValueError (never silently
    produces a partial or stale report) if: either id does not exist,
    the backtest does not reference the experiment, `primary_window_bars`
    is not one of the backtest's own configured windows, or re-running
    the experiment's conditions through the unmodified engine does not
    reproduce its already-persisted signals exactly (a genuine
    correctness gate, not a formality -- see
    _reproduce_and_verify_conditioned_signals()).
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

    horizons = [
        _build_horizon_result(
            window_bars=window_bars, is_primary=(window_bars == primary_window_bars),
            conditioned_signals=conditioned_signals, episode_signals=episode_signals, baseline_signals=baseline_signals,
            rng=rng, n_bootstrap=n_bootstrap, ci_level=ci_level,
        )
        for window_bars in backtest.windows
    ]

    primary_episode_returns = _returns_for_window(episode_signals, primary_window_bars)
    primary_baseline_returns = _returns_for_window(baseline_signals, primary_window_bars)
    primary_raw_returns = _returns_for_window(conditioned_signals, primary_window_bars)

    observed_diff, p_value = permutation_test_mean_difference(
        primary_episode_returns, primary_baseline_returns, rng=rng, n_permutations=n_permutations
    )
    d, pooled_stdev = cohens_d(primary_episode_returns, primary_baseline_returns)
    raw_observed_diff, raw_p_value = permutation_test_mean_difference(
        primary_raw_returns, primary_baseline_returns, rng=rng, n_permutations=n_permutations
    )

    return StatisticalValidationReport(
        experiment_id=experiment_id,
        backtest_id=backtest_id,
        primary_window_bars=primary_window_bars,
        generated_at=datetime.now(timezone.utc),
        seed=seed,
        n_bootstrap=n_bootstrap,
        n_permutations=n_permutations,
        episode_rule=EpisodeGroupingRule(
            description=(
                "Signals sorted by signal_timestamp; a signal joins the immediately preceding episode "
                "only if its signal_timestamp is exactly one bar-interval after the preceding signal's "
                "-- any larger gap starts a new episode. Each episode's first signal is its "
                "representative observation for episode-level statistics."
            ),
            bar_interval_minutes=int(bar_interval.total_seconds() // 60),
        ),
        horizons=horizons,
        primary_permutation_test=PermutationTestResult(
            window_bars=primary_window_bars, observed_mean_difference=observed_diff, p_value_two_sided=p_value,
            n_permutations=n_permutations, n_conditioned=len(primary_episode_returns),
            n_baseline=len(primary_baseline_returns), seed=seed,
        ),
        primary_effect_size=EffectSizeResult(
            window_bars=primary_window_bars, cohens_d=d, pooled_stdev=pooled_stdev, interpretation=_interpret_cohens_d(d)
        ),
        robustness_check=RobustnessCheck(
            window_bars=primary_window_bars,
            raw_mean_difference=raw_observed_diff, raw_p_value=raw_p_value, raw_n=len(primary_raw_returns),
            episode_mean_difference=observed_diff, episode_p_value=p_value, episode_n=len(primary_episode_returns),
        ),
    )


def _reproduce_and_verify_conditioned_signals(
    *, experiment: Experiment, backtest: Backtest, bars, feature_records, db_path
) -> list[BacktestSignal]:
    """Re-runs the experiment's OWN conditions through the unmodified
    run_backtest() and asserts the result is byte-for-byte identical to
    what is already persisted for this backtest -- never trusting a
    database row without confirming it is still exactly reproducible
    from the same conditions/bars/features it was produced from. Raises
    ValueError (not a silent mismatch) if bars/features have changed
    (e.g. recomputed) since this backtest last ran; the fix is always
    to re-run the backtest itself (POST /backtests/{id}/run), never to
    patch around the mismatch here.
    """
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


def _build_horizon_result(
    *, window_bars: int, is_primary: bool, conditioned_signals, episode_signals, baseline_signals, rng, n_bootstrap, ci_level
) -> HorizonResult:
    episode_returns = _returns_for_window(episode_signals, window_bars)
    baseline_returns = _returns_for_window(baseline_signals, window_bars)

    mean_ci_low, mean_ci_high = bootstrap_mean_difference_ci(
        episode_returns, baseline_returns, rng=rng, n_resamples=n_bootstrap, ci_level=ci_level
    )
    win_ci_low, win_ci_high = bootstrap_win_rate_ci(
        episode_returns, baseline_returns, rng=rng, n_resamples=n_bootstrap, ci_level=ci_level
    )

    cond_mean = float(np.mean(episode_returns))
    base_mean = float(np.mean(baseline_returns))
    cond_win_rate = sum(1 for r in episode_returns if r > 0) / len(episode_returns)
    base_win_rate = sum(1 for r in baseline_returns if r > 0) / len(baseline_returns)

    cond_crossing, cond_total = _session_crossings(episode_signals, window_bars)
    base_crossing, base_total = _session_crossings(baseline_signals, window_bars)

    return HorizonResult(
        window_bars=window_bars,
        is_primary=is_primary,
        sample_sizes=SampleSizes(
            window_bars=window_bars,
            raw_signal_count=len(_returns_for_window(conditioned_signals, window_bars)),
            episode_count=len(episode_returns),
            baseline_count=len(baseline_returns),
        ),
        mean_difference=MeanDifferenceCI(
            window_bars=window_bars, conditioned_mean=cond_mean, baseline_mean=base_mean,
            difference=cond_mean - base_mean, ci_low=mean_ci_low, ci_high=mean_ci_high, ci_level=ci_level,
            n_conditioned=len(episode_returns), n_baseline=len(baseline_returns),
        ),
        win_rate_difference=WinRateDifferenceCI(
            window_bars=window_bars, conditioned_win_rate=cond_win_rate, baseline_win_rate=base_win_rate,
            difference_pp=(cond_win_rate - base_win_rate) * 100, ci_low_pp=win_ci_low * 100, ci_high_pp=win_ci_high * 100,
            ci_level=ci_level, n_conditioned=len(episode_returns), n_baseline=len(baseline_returns),
        ),
        session_boundary=SessionBoundaryStats(
            window_bars=window_bars,
            n_conditioned_observations=cond_total, n_conditioned_crossing=cond_crossing,
            n_baseline_observations=base_total, n_baseline_crossing=base_crossing,
        ),
    )


def _returns_for_window(signals: list[BacktestSignal], window_bars: int) -> list[float]:
    return [outcome.forward_return for signal in signals for outcome in signal.outcomes if outcome.window_bars == window_bars]


def _session_crossings(signals: list[BacktestSignal], window_bars: int) -> tuple[int, int]:
    """(n_crossing, n_total) -- how many of this horizon's outcomes had
    an outcome_timestamp on a different UTC calendar date than their
    own entry_timestamp. See app/models/statistical_validation.py::
    SessionBoundaryStats' own docstring for what this measures and does
    NOT attempt to change."""
    crossing = total = 0
    for signal in signals:
        for outcome in signal.outcomes:
            if outcome.window_bars == window_bars:
                total += 1
                if signal.entry_timestamp.date() != outcome.outcome_timestamp.date():
                    crossing += 1
    return crossing, total


def _interpret_cohens_d(d: float) -> str:
    """Conventional Cohen's d magnitude labels only -- deliberately NOT
    phrased as "significant" (that is PermutationTestResult's job) or
    as a claim about real-world trading value (which this label never
    asserts)."""
    magnitude = abs(d)
    if magnitude < 0.2:
        return "negligible (below the conventional 'small' threshold of 0.2)"
    if magnitude < 0.5:
        return "small (conventional Cohen's d threshold -- not a claim of statistical significance or economic significance)"
    if magnitude < 0.8:
        return "medium (conventional Cohen's d threshold -- not a claim of statistical significance or economic significance)"
    return "large (conventional Cohen's d threshold -- not a claim of statistical significance or economic significance)"
