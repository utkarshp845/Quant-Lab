"""Pure output shapes for Statistical Validation V1 (app/
statistical_validation/, scripts/run_statistical_validation.py):
answers one question -- does an already-run Backtest's conditioned
population look different from the unconditional baseline by more than
random variation would explain? -- on top of Backtesting v1's own
already-validated, already-persisted output, without adding a database
table of its own (see app/statistical_validation/engine.py's module
docstring for why: this is a derived, on-demand computation, always
re-run from the same underlying bars/features/signals rather than
snapshotted, so it can never drift from what the Backtest it validates
actually contains).

Not a leaf module the way app/models/research.py or app/models/
backtesting.py are (those import only pydantic/stdlib/each other) --
these shapes exist purely to describe app/statistical_validation/'s own
output in one typed place, the same "typed, validated shape everywhere"
convention this app already applies to every other computed result
(ExperimentResults, BacktestResults, ...), even though nothing here is
ever written to SQL.

Every episode-level statistic in this module treats the 65 non-
overlapping episodes (app/statistical_validation/episodes.py), never
the 124 raw, clustered signals, as the independent-observation unit for
inference -- see EpisodeGroupingRule and SampleSizes below, both of
which exist specifically so raw vs. episode counts are never silently
conflated in the report.
"""

from datetime import datetime

from pydantic import BaseModel


class EpisodeGroupingRule(BaseModel):
    """The fixed, documented rule app/statistical_validation/episodes.py
    used to turn raw signals into non-overlapping episodes -- carried
    on the report itself so a reader never has to go find the rule in
    source to know what "episode" means here."""

    description: str
    bar_interval_minutes: int


class SampleSizes(BaseModel):
    """Every sample size relevant at one horizon, together, so a reader
    can never mistake `raw_signal_count` for `episode_count` -- the
    spec's own "do NOT silently discard the raw results" requirement,
    in the type system."""

    window_bars: int
    raw_signal_count: int
    episode_count: int
    baseline_count: int


class MeanDifferenceCI(BaseModel):
    """Conditioned (episode-level) vs. baseline mean forward return at
    one horizon, and a percentile-bootstrap confidence interval for
    their difference (app/statistical_validation/resampling.py) -- the
    conditioned side is ALWAYS the episode-level sample
    (`n_conditioned` equals SampleSizes.episode_count at the same
    horizon, never raw_signal_count); see the module's own docstring
    for why treating the 124 raw, clustered signals as independent
    would invalidate this interval."""

    window_bars: int
    conditioned_mean: float
    baseline_mean: float
    difference: float
    ci_low: float
    ci_high: float
    ci_level: float
    n_conditioned: int
    n_baseline: int


class WinRateDifferenceCI(BaseModel):
    """Same shape and same episode-level convention as
    MeanDifferenceCI, for win rate (forward_return > 0) instead of mean
    return. `difference_pp`/`ci_low_pp`/`ci_high_pp` are in PERCENTAGE
    POINTS (e.g. 9.58, not 0.0958) -- the natural unit for a win-rate
    gap, and a deliberately different scale from MeanDifferenceCI's
    fractional-return units so the two are never mistaken for each
    other."""

    window_bars: int
    conditioned_win_rate: float
    baseline_win_rate: float
    difference_pp: float
    ci_low_pp: float
    ci_high_pp: float
    ci_level: float
    n_conditioned: int
    n_baseline: int


class SessionBoundaryStats(BaseModel):
    """How many of this horizon's outcome windows crossed a trading-
    session/calendar-day boundary (entry_timestamp's UTC date !=
    outcome_timestamp's UTC date) -- for BOTH populations, at every
    horizon (most informative at 60 bars, per this feature's own spec,
    but computed uniformly rather than special-cased so a reader can
    compare across horizons). This measures Backtesting v1's EXISTING,
    unchanged behavior (positional, not calendar-aware, forward
    windows) -- it does not alter or gate on it."""

    window_bars: int
    n_conditioned_observations: int
    n_conditioned_crossing: int
    n_baseline_observations: int
    n_baseline_crossing: int


class HorizonResult(BaseModel):
    """Everything computed at one forward-return horizon. `is_primary`
    is True for exactly one horizon (5 bars, per this feature's own
    spec: "treat 5 bars as the primary hypothesis... 15/30/60 as
    secondary/exploratory") -- a report reader (or a future UI) can
    filter on this instead of hardcoding "5" a second time."""

    window_bars: int
    is_primary: bool
    sample_sizes: SampleSizes
    mean_difference: MeanDifferenceCI
    win_rate_difference: WinRateDifferenceCI
    session_boundary: SessionBoundaryStats


class PermutationTestResult(BaseModel):
    """The primary hypothesis's bootstrap/permutation test (app/
    statistical_validation/resampling.py::permutation_test_mean_difference()),
    ALWAYS computed on the episode-level conditioned sample -- see
    RobustnessCheck below for the explicit raw-vs-episode comparison.
    `p_value_two_sided` is an EMPIRICAL p-value from resampling, not a
    closed-form test statistic -- see the computing function's own
    docstring for the +1/+1 correction that keeps it strictly positive.
    `seed` is recorded so this exact p-value is reproducible by anyone
    re-running the same analysis against the same data."""

    window_bars: int
    observed_mean_difference: float
    p_value_two_sided: float
    n_permutations: int
    n_conditioned: int
    n_baseline: int
    seed: int


class EffectSizeResult(BaseModel):
    """Cohen's d (pooled-standard-deviation-standardized mean
    difference) for the primary horizon's conditioned-vs-baseline
    difference -- "a simple standardized effect size," per this
    feature's own spec, not a more elaborate model. `interpretation` is
    the conventional small/medium/large label ONLY -- it is
    deliberately not phrased as "significant" or "meaningful" anywhere,
    since Cohen's d says nothing about statistical significance on its
    own (see PermutationTestResult for that) or about real-world
    trading significance (which this app never claims, per its own
    scope statement)."""

    window_bars: int
    cohens_d: float
    pooled_stdev: float
    interpretation: str


class RobustnessCheck(BaseModel):
    """The spec's explicit "repeat the primary 5-bar analysis under
    both raw and episode-level populations, show how much inference
    changes" requirement -- both permutation-test results, side by
    side, at the primary horizon only. `episode_*` fields are the
    SAME values as PermutationTestResult above (duplicated here rather
    than cross-referenced, so this one section is readable on its own
    without flipping back to another part of the report) --
    `episode_p_value`/`episode_mean_difference` are the authoritative
    result; `raw_*` exists only for comparison, never as an alternative
    conclusion to report instead."""

    window_bars: int
    raw_mean_difference: float
    raw_p_value: float
    raw_n: int
    episode_mean_difference: float
    episode_p_value: float
    episode_n: int


class StatisticalValidationReport(BaseModel):
    """The full output of app.statistical_validation.engine::
    build_statistical_validation_report() -- everything needed to
    reproduce it again (`experiment_id`/`backtest_id`/`seed`/
    `n_bootstrap`/`n_permutations`) plus every requested result. Never
    persisted to a database table (see this module's own docstring) --
    a caller that wants a permanent record of one report should save
    this model's own JSON serialization, which is why every field here
    is a plain, JSON-serializable value."""

    experiment_id: str
    backtest_id: str
    primary_window_bars: int
    generated_at: datetime
    seed: int
    n_bootstrap: int
    n_permutations: int
    episode_rule: EpisodeGroupingRule
    horizons: list[HorizonResult]
    primary_permutation_test: PermutationTestResult
    primary_effect_size: EffectSizeResult
    robustness_check: RobustnessCheck
