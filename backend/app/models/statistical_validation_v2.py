"""Pure output shapes for Statistical Validation V2 (app/
statistical_validation/v2/, scripts/run_statistical_validation_v2.py).

A SEPARATE, ADDITIVE set of shapes from app/models/statistical_validation.py
(V1) -- nothing here is imported by, or modifies, that module. V2
answers the same question V1 did (does the conditioned population
differ from baseline by more than random variation would explain?) but
corrects V1's one flagged weakness: V1's baseline treated every
eligible bar as an independent observation despite adjacent bars'
forward-return windows overlapping heavily. V2 keeps V1's conditioned-
side treatment (episode-level, non-overlapping -- see
app.statistical_validation.episodes, reused unchanged) and adds TWO
independent corrections to the baseline side:

  Method A -- non-overlapping baseline windows (app/statistical_validation/
  v2/baseline.py): subsample the baseline to non-overlapping windows,
  then reuse V1's OWN bootstrap/permutation functions unmodified.

  Method B -- moving block bootstrap (app/statistical_validation/v2/
  resampling.py): resample the full, overlapping baseline series in
  contiguous blocks rather than individual points.

Every result below is tagged with WHICH method produced it
(BaselineMethodV2) so a reader never has to guess.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class BaselineMethodV2(str, Enum):
    """Which dependence correction produced a given result -- see this
    module's own docstring for what each one does."""

    NON_OVERLAPPING_WINDOWS = "non_overlapping_windows"
    MOVING_BLOCK_BOOTSTRAP = "moving_block_bootstrap"


class PopulationSummaryV2(BaseModel):
    """Every population size relevant to the primary horizon, together
    -- the spec's own "do not silently discard the raw results" rule,
    extended from V1's raw-vs-episode distinction to also cover the
    baseline side's own effective/independent sample size under each
    method."""

    window_bars: int
    raw_conditioned_signals: int
    conditioned_episodes: int
    baseline_raw_observations: int
    method_a_effective_baseline_n: int
    method_b_block_length: int
    method_b_block_count: int


class MeanDifferenceResultV2(BaseModel):
    """Same shape as V1's MeanDifferenceCI, tagged with which baseline
    method produced it -- conditioned is ALWAYS the episode-level
    sample (unchanged from V1); only the baseline side differs by
    method."""

    method: BaselineMethodV2
    window_bars: int
    conditioned_mean: float
    baseline_mean: float
    difference: float
    ci_low: float
    ci_high: float
    ci_level: float
    n_conditioned: int
    n_baseline: int


class WinRateDifferenceResultV2(BaseModel):
    """Same shape as V1's WinRateDifferenceCI, tagged with method.
    `difference_pp`/`ci_low_pp`/`ci_high_pp` are in PERCENTAGE POINTS."""

    method: BaselineMethodV2
    window_bars: int
    conditioned_win_rate: float
    baseline_win_rate: float
    difference_pp: float
    ci_low_pp: float
    ci_high_pp: float
    ci_level: float
    n_conditioned: int
    n_baseline: int


class DependenceAwareTestResultV2(BaseModel):
    """The primary hypothesis test under ONE method -- for
    NON_OVERLAPPING_WINDOWS this is V1's own, unmodified
    permutation_test_mean_difference() applied to the non-overlapping
    subsample; for MOVING_BLOCK_BOOTSTRAP this is the H0-centered
    block-bootstrap test (app.statistical_validation.v2.resampling.
    moving_block_bootstrap_p_value()) -- see that function's own
    docstring for why a plain point-by-point permutation is NOT valid
    for the block method's overlapping baseline series."""

    method: BaselineMethodV2
    window_bars: int
    observed_mean_difference: float
    p_value_two_sided: float
    n_resamples: int
    n_conditioned: int
    n_baseline: int
    seed: int


class EffectSizeResultV2(BaseModel):
    """Cohen's d for the primary horizon, computed against Method A's
    non-overlapping baseline (the method with directly comparable,
    non-overlapping observations on both sides -- the natural setting
    for a classical standardized-mean-difference statistic; Method B's
    block-resampled baseline does not have an equally natural single
    "sample standard deviation" to standardize by, so no second d is
    computed for it -- see app/statistical_validation/v2/engine.py)."""

    window_bars: int
    cohens_d: float
    pooled_stdev: float
    interpretation: str
    method: BaselineMethodV2


class PowerAnalysisResultV2(BaseModel):
    """A post-hoc, sensitivity-only statement about what this sample
    size could realistically detect -- NEVER evidence that H0 is true
    (see app/statistical_validation/v2/power.py's own docstring).
    `observed_effect_below_detectable_threshold` is True when the
    OBSERVED |cohens_d| is smaller than `minimum_detectable_effect_size`
    -- i.e. this study was not adequately powered to reliably
    distinguish the observed effect from noise, which is a statement
    about the STUDY's sensitivity, not about whether a real effect
    exists."""

    n_conditioned_episodes: int
    n_baseline_effective: int
    alpha: float
    power: float
    minimum_detectable_effect_size: float
    observed_effect_size: float
    observed_effect_below_detectable_threshold: bool


class DescriptiveHorizonResultV2(BaseModel):
    """15/30/60-bar results -- DESCRIPTIVE ONLY, no CI, no p-value, no
    effect size: this feature's own spec explicitly forbids testing
    significance at any horizon but the primary one. Uses Method A's
    non-overlapping baseline for consistency with the primary result's
    baseline construction, but reports no inference on top of it."""

    window_bars: int
    raw_signal_count: int
    episode_count: int
    conditioned_episode_mean: float
    baseline_mean: float
    difference: float
    conditioned_win_rate: float
    baseline_win_rate: float


class RobustnessComparisonV2(BaseModel):
    """The spec's explicit "run at least two dependence-aware baseline
    constructions, report whether the conclusion changes materially"
    requirement -- both methods' mean-difference results and test
    results, side by side, at the primary horizon only.
    `conclusion_changes_materially` is a simple, mechanical check (do
    the two methods' 95% CIs disagree on whether they exclude zero?),
    not a subjective judgment call baked into the data model."""

    window_bars: int
    method_a_mean_difference: MeanDifferenceResultV2
    method_a_test: DependenceAwareTestResultV2
    method_b_mean_difference: MeanDifferenceResultV2
    method_b_test: DependenceAwareTestResultV2
    conclusion_changes_materially: bool


class StatisticalValidationReportV2(BaseModel):
    """The full output of app.statistical_validation.v2.engine::
    build_statistical_validation_report_v2(). Self-contained -- does
    not embed or require a V1 StatisticalValidationReport; comparison
    to V1's own numbers is a narrative exercise for whoever reads both
    reports, not a field this model carries."""

    experiment_id: str
    backtest_id: str
    primary_window_bars: int
    generated_at: datetime
    seed: int
    n_resamples: int
    ci_level: float
    block_length_multiplier: int
    population: PopulationSummaryV2
    method_a_mean_difference: MeanDifferenceResultV2
    method_a_win_rate_difference: WinRateDifferenceResultV2
    method_a_test: DependenceAwareTestResultV2
    method_b_mean_difference: MeanDifferenceResultV2
    method_b_win_rate_difference: WinRateDifferenceResultV2
    method_b_test: DependenceAwareTestResultV2
    effect_size: EffectSizeResultV2
    power_analysis: PowerAnalysisResultV2
    robustness: RobustnessComparisonV2
    secondary_horizons: list[DescriptiveHorizonResultV2]
