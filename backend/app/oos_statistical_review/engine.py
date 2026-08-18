"""OOS Statistical Review V1's orchestrator (app/oos_statistical_review/):
given a frozen experiment's accumulated OOS evidence (app/oos_evidence/),
builds one immutable OOSStatisticalReview (app/models/
oos_statistical_review.py) -- READ-ONLY, never touches the frozen
hypothesis, the ExperimentFreezeSnapshot, an OOS partition, an OOS
evaluation, an OOS signal, a historical bar, or a historical feature.

EXACT PIPELINE:

  1. Load the immutable ExperimentFreezeSnapshot (the ONLY source of
     the hypothesis definition -- symbol/timeframe/provider/outcome/
     feature_contract_version below all come from here, never the live
     `Experiment` row).
  2. Load EVERY OOS evaluation ever run for this experiment
     (app.storage.oos_evaluation_repository.list_evaluations(),
     UNMODIFIED, already includes both the originally frozen-time-linked
     partition's own evaluation(s) AND every OOS Evidence Accumulation
     V1 period's own evaluation(s) -- both write into the SAME table).
     Split into COMPLETED (participate) and FAILED (excluded, but
     listed on the review as ExcludedEvaluation rows).
  3. Verify UNIFORM PROVENANCE across every COMPLETED evaluation
     (_verify_uniform_provenance() below) -- FAILS CLOSED
     (ProvenanceMismatchError) if even one disagrees on hypothesis_hash
     or any other research-defining fact. This is checked BEFORE any
     statistic is computed.
  4. Per COMPLETED evaluation (one OOS period): read its own,
     already-persisted OOSSignal rows (read-only -- never recomputed);
     group into episodes (app.statistical_validation.episodes.
     group_into_episodes(), reused UNMODIFIED) and take the episode
     representatives -- THE independent conditioned observation unit.
     Construct the OOS-scoped unconditional baseline for that SAME
     period (app/oos_statistical_review/baseline.py -- the SAME holdout
     bars that evaluation itself used, never development data).
  5. Pool episode representatives AND baseline signals across every
     period, in CHRONOLOGICAL (holdout_start) order -- episodes are
     NEVER grouped across periods (group_into_episodes() is applied
     PER PERIOD, in step 4, before any pooling happens), so two
     different periods' signals can never be merged into one episode.
  6. Run BOTH of Statistical Validation V2's dependence-aware baseline
     methods (app.statistical_validation.v2.baseline.
     non_overlapping_baseline() for Method A, app.statistical_validation.
     v2.resampling's moving_block_bootstrap_* for Method B -- both
     UNMODIFIED) against the pooled baseline, and app.statistical_validation.
     resampling's bootstrap/permutation functions (V1, UNMODIFIED)
     against the pooled episode-level conditioned sample -- ONLY when
     both the conditioned episode count and the baseline's own two
     effective sample sizes clear this review's documented minimums
     (MIN_EPISODES_FOR_FORMAL_TEST / MIN_BASELINE_OBSERVATIONS below);
     otherwise every method_*/effect_size/power/robustness field is
     left None and the verdict is INSUFFICIENT_DATA, unconditionally.
  7. Compute Cohen's d (against Method A's baseline, matching V2's own
     precedent) and the minimum detectable effect size
     (app.statistical_validation.v2.power.minimum_detectable_effect_size(),
     UNMODIFIED).
  8. Determine the verdict (app/oos_statistical_review/verdict.py::
     determine_verdict(), pure, deterministic, requires BOTH methods to
     independently agree for SUPPORTED or NOT_SUPPORTED).
  9. Assemble per-period consistency results (app.backtesting.
     aggregation.aggregate_results(), UNMODIFIED, applied to each
     period's own episode representatives).
  10. Return the fully-assembled, in-memory OOSStatisticalReview.
      app/api/oos_statistical_review.py is the only caller that
      persists it (app.storage.oos_statistical_review_repository,
      append-only, INSERT only).

EXPLORATORY HORIZONS (requirement 7): always an empty list in V1.
OOS Evaluation v1 evaluates EXACTLY ONE window (the frozen Outcome's
own horizon) per evaluation -- there is no OTHER, already-evaluated
conditioned population at any other horizon anywhere in this app's
persisted OOS evidence to report as "exploratory" without RE-DERIVING
new conditioned observations Statistical Validation never actually
produced. Recomputing additional horizons here would mean running the
frozen hypothesis' conditions against holdout data at horizons OOS
Evaluation v1 itself never evaluated -- exactly the kind of "additional
data mining" this feature's own instructions forbid, and a real,
if narrow, violation of "OOS data is sacred" (a horizon nobody
committed to evaluating in advance is, by definition, not pre-specified).
`OOSStatisticalReview.exploratory_horizons_note` states this plainly on
every review, rather than silently returning an empty list a reader
might mistake for "no other horizons existed to check."
"""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from app.backtesting.aggregation import aggregate_results
from app.models.backtesting import BacktestSignal
from app.models.experiment_freeze import ExperimentFreezeSnapshot
from app.models.oos_evaluation import OOSEvaluationResult, OOSEvaluationStatus, OOSSignal
from app.models.oos_statistical_review import (
    ExcludedEvaluation,
    OOSEpisodeSampleSizes,
    OOSPeriodBoundary,
    OOSPeriodConsistencyResult,
    OOSStatisticalReview,
    OOSStatisticalVerdict,
)
from app.models.statistical_validation_v2 import (
    BaselineMethodV2,
    DependenceAwareTestResultV2,
    EffectSizeResultV2,
    MeanDifferenceResultV2,
    PowerAnalysisResultV2,
    RobustnessComparisonV2,
    WinRateDifferenceResultV2,
)
from app.oos_statistical_review.baseline import (
    BaselineConstructionError,  # noqa: F401 -- re-exported so app/api/oos_statistical_review.py needs only this one module
    compute_oos_unconditional_baseline,
)
from app.oos_statistical_review.verdict import determine_verdict, hypothesized_direction
from app.research.metrics import bars_for_window, timeframe_minutes
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
from app.storage import experiment_freeze_repository, oos_evaluation_repository, research_repository

REVIEW_CONFIG_VERSION = "oos-statistical-review-v1"
DEFAULT_SEED = 1337
DEFAULT_N_RESAMPLES = 10_000
DEFAULT_CI_LEVEL = 0.95
DEFAULT_BLOCK_LENGTH_MULTIPLIER = 4
DEFAULT_POWER = 0.80

# Below this many independent episodes, a percentile bootstrap CI, a
# permutation test, and Cohen's d are all numerically unstable and not
# "a responsible formal test" (this feature's own words) -- a FIXED,
# documented threshold, never tuned per result. Deliberately well above
# the bare minimum (2) app.statistical_validation.resampling.cohens_d()
# itself requires just to avoid raising -- this is about a
# RESPONSIBLE test, not merely a computable one.
MIN_EPISODES_FOR_FORMAL_TEST = 10

# Same reasoning, applied to the OOS-scoped baseline's own two
# effective sample sizes (Method A's non-overlapping count, Method B's
# raw pooled series length) -- below this, standard deviation and
# resampling are undefined or degenerate.
MIN_BASELINE_OBSERVATIONS = 2

_EXPLORATORY_HORIZONS_NOTE = (
    "Empty in OOS Statistical Review V1: OOS Evaluation v1 evaluates exactly one window (the frozen Outcome's "
    "own horizon) per evaluation -- there is no other, already-evaluated conditioned population at any other "
    "horizon in this app's persisted OOS evidence to report without re-deriving new conditioned observations "
    "OOS Evaluation v1 itself never produced, which this review's own 'OOS data is sacred, never re-derive "
    "evidence' rule forbids."
)


class OOSStatisticalReviewError(Exception):
    """Base class for every precondition failure this module raises
    BEFORE any statistic is computed -- none of these ever result in a
    persisted OOSStatisticalReview row."""


class ExperimentNotFoundForReviewError(OOSStatisticalReviewError):
    pass


class ExperimentNeverFrozenError(OOSStatisticalReviewError):
    pass


class ProvenanceMismatchError(OOSStatisticalReviewError):
    """Requirement 1: "if the hypothesis hash or research-defining
    provenance differs across evaluations, fail closed." Raised by
    _verify_uniform_provenance() below."""


def _verify_uniform_provenance(snapshot: ExperimentFreezeSnapshot, completed: list[OOSEvaluationResult]) -> None:
    """Every COMPLETED evaluation must agree with the frozen snapshot
    (never the live Experiment row) on every research-defining fact:
    hypothesis_hash, frozen_snapshot_id (== snapshot.experiment_id),
    symbol/timeframe/provider, feature_contract_version, and the
    outcome's own horizon (evaluation.outcome_horizon_minutes vs.
    snapshot.outcome.horizon_minutes). Raises ProvenanceMismatchError
    on the FIRST disagreement found -- never silently drops the
    offending evaluation and continues with a partial, quietly-smaller
    review."""
    for evaluation in completed:
        if evaluation.hypothesis_hash != snapshot.hypothesis_hash:
            raise ProvenanceMismatchError(
                f"Evaluation {evaluation.id!r}'s hypothesis_hash ({evaluation.hypothesis_hash!r}) does not match "
                f"the frozen snapshot's own ({snapshot.hypothesis_hash!r}) -- refusing to build a review over "
                "evidence for more than one hypothesis."
            )
        if evaluation.frozen_snapshot_id != snapshot.experiment_id:
            raise ProvenanceMismatchError(
                f"Evaluation {evaluation.id!r}'s frozen_snapshot_id ({evaluation.frozen_snapshot_id!r}) does not "
                f"match the frozen snapshot's own experiment_id ({snapshot.experiment_id!r})."
            )
        if (evaluation.symbol, evaluation.timeframe, evaluation.provider) != (
            snapshot.symbol,
            snapshot.timeframe,
            snapshot.provider,
        ):
            raise ProvenanceMismatchError(
                f"Evaluation {evaluation.id!r} ({evaluation.symbol}/{evaluation.timeframe}/{evaluation.provider}) "
                f"does not match the frozen snapshot's own ({snapshot.symbol}/{snapshot.timeframe}/{snapshot.provider})."
            )
        if evaluation.feature_contract_version != snapshot.feature_contract_version:
            raise ProvenanceMismatchError(
                f"Evaluation {evaluation.id!r}'s feature_contract_version ({evaluation.feature_contract_version!r}) "
                f"does not match the frozen snapshot's own ({snapshot.feature_contract_version!r})."
            )
        if evaluation.outcome_horizon_minutes != snapshot.outcome.horizon_minutes:
            raise ProvenanceMismatchError(
                f"Evaluation {evaluation.id!r}'s outcome_horizon_minutes ({evaluation.outcome_horizon_minutes}) "
                f"does not match the frozen snapshot's own outcome horizon ({snapshot.outcome.horizon_minutes})."
            )


def _returns_for_window(signals: list[BacktestSignal] | list[OOSSignal], window_bars: int) -> list[float]:
    return [outcome.forward_return for signal in signals for outcome in signal.outcomes if outcome.window_bars == window_bars]


def _returns_in_chronological_order(signals: list[BacktestSignal], window_bars: int) -> list[float]:
    """Same values as _returns_for_window(), but sorted by
    entry_timestamp -- REQUIRED for the moving block bootstrap (Method
    B), which relies on the series' real order to capture genuine
    temporal dependence. Duplicated (not imported) from
    app.statistical_validation.v2.engine's private helper of the same
    purpose, matching that module's own precedent for why (V2's own
    module docstring: private helpers are re-implemented in a few
    lines rather than imported across modules, so each module's own
    internals stay its own)."""
    ordered = sorted(signals, key=lambda s: s.entry_timestamp)
    return [outcome.forward_return for signal in ordered for outcome in signal.outcomes if outcome.window_bars == window_bars]


def _interpret_cohens_d(d: float) -> str:
    magnitude = abs(d)
    if magnitude < 0.2:
        return "negligible (below the conventional 'small' threshold of 0.2)"
    if magnitude < 0.5:
        return "small (conventional Cohen's d threshold -- not a claim of statistical or economic significance)"
    if magnitude < 0.8:
        return "medium (conventional Cohen's d threshold -- not a claim of statistical or economic significance)"
    return "large (conventional Cohen's d threshold -- not a claim of statistical or economic significance)"


def build_oos_statistical_review(
    experiment_id: str,
    *,
    seed: int = DEFAULT_SEED,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    ci_level: float = DEFAULT_CI_LEVEL,
    block_length_multiplier: int = DEFAULT_BLOCK_LENGTH_MULTIPLIER,
    power: float = DEFAULT_POWER,
    min_episodes_for_formal_test: int = MIN_EPISODES_FOR_FORMAL_TEST,
    db_path: str | Path | None = None,
) -> OOSStatisticalReview:
    """The single entry point -- see the module docstring for the full
    pipeline. Raises ExperimentNotFoundForReviewError/
    ExperimentNeverFrozenError/ProvenanceMismatchError for a
    precondition failure (nothing computed, nothing persisted by this
    function itself either way -- see app/api/oos_statistical_review.py,
    the only caller that persists a returned review). Never raises for
    "too little evidence" -- that is a legitimate INSUFFICIENT_DATA
    verdict, not an error (the same "a computable-but-small result is a
    normal, reportable outcome" convention this app applies everywhere,
    e.g. app/models/oos_evaluation.py::OOSEvaluationStatus's own
    COMPLETED-with-zero-signals case)."""
    experiment = research_repository.get_experiment(experiment_id, db_path=db_path)
    if experiment is None:
        raise ExperimentNotFoundForReviewError(f"No experiment with id {experiment_id!r}")

    snapshot = experiment_freeze_repository.get_snapshot(experiment_id, db_path=db_path)
    if snapshot is None:
        raise ExperimentNeverFrozenError(
            f"Experiment {experiment_id!r} has never been frozen -- there is no ExperimentFreezeSnapshot to "
            "review evidence against."
        )

    all_evaluations = oos_evaluation_repository.list_evaluations(experiment_id, db_path=db_path)
    completed = sorted(
        (e for e in all_evaluations if e.status == OOSEvaluationStatus.COMPLETED), key=lambda e: e.holdout_start
    )
    failed = [e for e in all_evaluations if e.status == OOSEvaluationStatus.FAILED]

    _verify_uniform_provenance(snapshot, completed)

    bar_interval = timedelta(minutes=timeframe_minutes(snapshot.timeframe))
    primary_window_bars = (
        completed[0].outcome_window_bars if completed else bars_for_window(snapshot.outcome.horizon_minutes, snapshot.timeframe)
    )

    per_period_results: list[OOSPeriodConsistencyResult] = []
    oos_periods: list[OOSPeriodBoundary] = []
    pooled_episode_signals: list[OOSSignal] = []
    pooled_baseline_signals: list[BacktestSignal] = []
    total_raw_signal_count = 0
    total_episode_count = 0

    for evaluation in completed:
        signals = oos_evaluation_repository.get_signals(evaluation.id, db_path=db_path)
        episodes = group_into_episodes(signals, bar_interval=bar_interval)
        representatives = episode_representatives(episodes)
        pooled_episode_signals.extend(representatives)
        total_raw_signal_count += evaluation.signal_count
        total_episode_count += len(episodes)

        baseline_signals = compute_oos_unconditional_baseline(evaluation, db_path=db_path)  # raises BaselineConstructionError
        pooled_baseline_signals.extend(baseline_signals)

        oos_periods.append(
            OOSPeriodBoundary(
                evaluation_id=evaluation.id, oos_partition_id=evaluation.oos_partition_id,
                oos_start=evaluation.holdout_start, oos_end=evaluation.holdout_end,
            )
        )
        period_window = aggregate_results(representatives, windows=[primary_window_bars]).windows[0]
        per_period_results.append(
            OOSPeriodConsistencyResult(
                evaluation_id=evaluation.id, oos_partition_id=evaluation.oos_partition_id,
                oos_start=evaluation.holdout_start, oos_end=evaluation.holdout_end,
                raw_signal_count=evaluation.signal_count, episode_count=len(episodes),
                mean_return=period_window.mean_return, median_return=period_window.median_return,
                win_rate=period_window.win_rate, std_dev_return=period_window.std_dev_return,
            )
        )

    excluded_evaluations = [
        ExcludedEvaluation(evaluation_id=e.id, oos_partition_id=e.oos_partition_id, reason=f"status={e.status.value}")
        for e in failed
    ]

    episode_returns = _returns_for_window(pooled_episode_signals, primary_window_bars)
    baseline_returns_chronological = _returns_in_chronological_order(pooled_baseline_signals, primary_window_bars)
    method_a_signals = non_overlapping_baseline(pooled_baseline_signals, window_bars=primary_window_bars, bar_interval=bar_interval)
    method_a_returns = _returns_for_window(method_a_signals, primary_window_bars)

    sample_sizes = OOSEpisodeSampleSizes(
        evaluation_count=len(completed), raw_signal_count=total_raw_signal_count, episode_count=total_episode_count,
        baseline_raw_observations=len(baseline_returns_chronological), method_a_effective_baseline_n=len(method_a_returns),
    )

    has_enough_data = (
        len(episode_returns) >= min_episodes_for_formal_test
        and len(method_a_returns) >= MIN_BASELINE_OBSERVATIONS
        and len(baseline_returns_chronological) >= MIN_BASELINE_OBSERVATIONS
    )

    method_a_mean_diff = method_a_win_diff = method_a_test = None
    method_b_mean_diff = method_b_win_diff = method_b_test = None
    effect_size = power_analysis = robustness = None

    if not has_enough_data:
        verdict = OOSStatisticalVerdict.INSUFFICIENT_DATA
        reasoning = (
            f"{len(episode_returns)} independent conditioned episode(s) and {len(baseline_returns_chronological)} "
            f"OOS-scoped baseline observation(s) ({len(method_a_returns)} non-overlapping) accumulated -- below "
            f"this review's own minimums ({min_episodes_for_formal_test} episodes, {MIN_BASELINE_OBSERVATIONS} "
            "baseline observations) required to responsibly run a formal test. No p-value, confidence interval, "
            "or effect size was computed."
        )
    else:
        rng = np.random.default_rng(seed)
        block_length = block_length_multiplier * primary_window_bars

        mean_lo, mean_hi = bootstrap_mean_difference_ci(episode_returns, method_a_returns, rng=rng, n_resamples=n_resamples, ci_level=ci_level)
        cond_mean, base_mean = float(np.mean(episode_returns)), float(np.mean(method_a_returns))
        method_a_mean_diff = MeanDifferenceResultV2(
            method=BaselineMethodV2.NON_OVERLAPPING_WINDOWS, window_bars=primary_window_bars, conditioned_mean=cond_mean,
            baseline_mean=base_mean, difference=cond_mean - base_mean, ci_low=mean_lo, ci_high=mean_hi, ci_level=ci_level,
            n_conditioned=len(episode_returns), n_baseline=len(method_a_returns),
        )
        win_lo, win_hi = bootstrap_win_rate_ci(episode_returns, method_a_returns, rng=rng, n_resamples=n_resamples, ci_level=ci_level)
        cond_win = sum(1 for r in episode_returns if r > 0) / len(episode_returns)
        base_win_a = sum(1 for r in method_a_returns if r > 0) / len(method_a_returns)
        method_a_win_diff = WinRateDifferenceResultV2(
            method=BaselineMethodV2.NON_OVERLAPPING_WINDOWS, window_bars=primary_window_bars, conditioned_win_rate=cond_win,
            baseline_win_rate=base_win_a, difference_pp=(cond_win - base_win_a) * 100, ci_low_pp=win_lo * 100, ci_high_pp=win_hi * 100,
            ci_level=ci_level, n_conditioned=len(episode_returns), n_baseline=len(method_a_returns),
        )
        method_a_observed, method_a_p = permutation_test_mean_difference(episode_returns, method_a_returns, rng=rng, n_permutations=n_resamples)
        method_a_test = DependenceAwareTestResultV2(
            method=BaselineMethodV2.NON_OVERLAPPING_WINDOWS, window_bars=primary_window_bars,
            observed_mean_difference=method_a_observed, p_value_two_sided=method_a_p, n_resamples=n_resamples,
            n_conditioned=len(episode_returns), n_baseline=len(method_a_returns), seed=seed,
        )

        mb_mean_lo, mb_mean_hi = moving_block_bootstrap_mean_difference_ci(
            episode_returns, baseline_returns_chronological, block_length=block_length, rng=rng, n_resamples=n_resamples, ci_level=ci_level
        )
        base_mean_b = float(np.mean(baseline_returns_chronological))
        method_b_mean_diff = MeanDifferenceResultV2(
            method=BaselineMethodV2.MOVING_BLOCK_BOOTSTRAP, window_bars=primary_window_bars, conditioned_mean=cond_mean,
            baseline_mean=base_mean_b, difference=cond_mean - base_mean_b, ci_low=mb_mean_lo, ci_high=mb_mean_hi, ci_level=ci_level,
            n_conditioned=len(episode_returns), n_baseline=len(baseline_returns_chronological),
        )
        mb_win_lo, mb_win_hi = moving_block_bootstrap_win_rate_ci(
            episode_returns, baseline_returns_chronological, block_length=block_length, rng=rng, n_resamples=n_resamples, ci_level=ci_level
        )
        base_win_b = sum(1 for r in baseline_returns_chronological if r > 0) / len(baseline_returns_chronological)
        method_b_win_diff = WinRateDifferenceResultV2(
            method=BaselineMethodV2.MOVING_BLOCK_BOOTSTRAP, window_bars=primary_window_bars, conditioned_win_rate=cond_win,
            baseline_win_rate=base_win_b, difference_pp=(cond_win - base_win_b) * 100, ci_low_pp=mb_win_lo * 100, ci_high_pp=mb_win_hi * 100,
            ci_level=ci_level, n_conditioned=len(episode_returns), n_baseline=len(baseline_returns_chronological),
        )
        method_b_observed, method_b_p = moving_block_bootstrap_p_value(
            episode_returns, baseline_returns_chronological, block_length=block_length, rng=rng, n_resamples=n_resamples
        )
        method_b_test = DependenceAwareTestResultV2(
            method=BaselineMethodV2.MOVING_BLOCK_BOOTSTRAP, window_bars=primary_window_bars,
            observed_mean_difference=method_b_observed, p_value_two_sided=method_b_p, n_resamples=n_resamples,
            n_conditioned=len(episode_returns), n_baseline=len(baseline_returns_chronological), seed=seed,
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

        direction = hypothesized_direction(snapshot.outcome)
        verdict, reasoning = determine_verdict(
            direction=direction,
            method_a_test=method_a_test, method_a_mean_difference=method_a_mean_diff,
            method_b_test=method_b_test, method_b_mean_difference=method_b_mean_diff,
            effect_size_d=d,
        )

    return OOSStatisticalReview(
        id=str(uuid.uuid4()),
        experiment_id=experiment_id,
        frozen_snapshot_id=snapshot.experiment_id,
        hypothesis_hash=snapshot.hypothesis_hash,
        review_config_version=REVIEW_CONFIG_VERSION,
        created_at=datetime.now(timezone.utc),
        included_evaluation_ids=[e.id for e in completed],
        excluded_evaluations=excluded_evaluations,
        oos_periods=oos_periods,
        outcome_metric=snapshot.outcome.metric,
        outcome_operator=snapshot.outcome.operator.value,
        outcome_threshold=snapshot.outcome.threshold,
        outcome_horizon_minutes=snapshot.outcome.horizon_minutes,
        primary_window_bars=primary_window_bars,
        symbol=snapshot.symbol, timeframe=snapshot.timeframe, provider=snapshot.provider,
        feature_contract_version=snapshot.feature_contract_version,
        seed=seed, n_resamples=n_resamples, ci_level=ci_level, block_length_multiplier=block_length_multiplier,
        power_target=power, min_episodes_for_formal_test=min_episodes_for_formal_test,
        sample_sizes=sample_sizes,
        method_a_mean_difference=method_a_mean_diff, method_a_win_rate_difference=method_a_win_diff, method_a_test=method_a_test,
        method_b_mean_difference=method_b_mean_diff, method_b_win_rate_difference=method_b_win_diff, method_b_test=method_b_test,
        effect_size=effect_size, power_analysis=power_analysis, robustness=robustness,
        exploratory_horizons_note=_EXPLORATORY_HORIZONS_NOTE,
        per_period_results=per_period_results,
        verdict=verdict, verdict_reasoning=reasoning,
    )
