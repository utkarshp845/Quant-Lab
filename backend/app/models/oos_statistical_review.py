"""Persistent/response shapes for OOS Statistical Review V1
(app/oos_statistical_review/, app/storage/oos_statistical_review_repository.py,
app/api/oos_statistical_review.py): a formal, READ-ONLY statistical
review layer answering exactly one question -- "given all COMPLETED
OOS periods accumulated for this frozen hypothesis (app/oos_evidence/),
is there sufficient statistical evidence that the observed effect
differs from the appropriate baseline?" -- for the SAME immutable
hypothesis, over the SAME already-evaluated OOS evidence, never a new
experiment, a new backtest, or new data mining.

Deliberately REUSES Statistical Validation V2's own output shapes
(app.models.statistical_validation_v2 -- imported, never modified,
never duplicated) for every piece of machinery this feature's own spec
explicitly asks it to reuse: `BaselineMethodV2` (which dependence
correction produced a result), `MeanDifferenceResultV2`/
`WinRateDifferenceResultV2`/`DependenceAwareTestResultV2`/
`EffectSizeResultV2`/`PowerAnalysisResultV2`/`RobustnessComparisonV2` --
all field-for-field exactly what this review needs, computed by the
SAME (also reused, unmodified) functions in app.statistical_validation/
and app.statistical_validation.v2/, just against OOS-scoped populations
instead of Backtesting v1's development-side ones. Only the genuinely
NEW shapes this feature adds -- the verdict, the OOS-specific
provenance/input record, and per-period consistency -- are defined
below.

Kept a leaf-ish module: pydantic, the stdlib, and this app's own
sibling leaf/near-leaf models (app.models.statistical_validation_v2)
only -- never app.oos_statistical_review, app.storage, or app.api.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from app.models.statistical_validation_v2 import (
    DependenceAwareTestResultV2,
    EffectSizeResultV2,
    MeanDifferenceResultV2,
    PowerAnalysisResultV2,
    RobustnessComparisonV2,
    WinRateDifferenceResultV2,
)


class OOSStatisticalVerdict(str, Enum):
    """The four possible, mutually exclusive outcomes of one review --
    see app/oos_statistical_review/verdict.py::determine_verdict() for
    the exact, deterministic, mechanical rule that assigns one of these
    (never a subjective judgment call baked into a route handler).

    This verdict is a statement about EVIDENCE FOR THE FORWARD-RETURN
    HYPOTHESIS ONLY -- never a trading recommendation, and never a
    claim about profitability. `p >= 0.05` does NOT mean "the
    hypothesis is false" (that is INCONCLUSIVE, not NOT_SUPPORTED); and
    `p < 0.05` does NOT mean "the strategy is profitable" (this review
    tests a forward-return difference from an unconditional baseline,
    nothing about trading costs, execution, or capital allocation).
    """

    SUPPORTED = "supported"
    NOT_SUPPORTED = "not_supported"
    INCONCLUSIVE = "inconclusive"
    INSUFFICIENT_DATA = "insufficient_data"


class ExcludedEvaluation(BaseModel):
    """One OOS evaluation that exists for this experiment but did NOT
    participate in the review -- requirement 1's "FAILED evaluations
    are excluded but remain visible as excluded evidence": a reader of
    the review can always see exactly what was left out and why,
    rather than a FAILED evaluation simply vanishing from the record."""

    evaluation_id: str
    oos_partition_id: str
    reason: str


class OOSPeriodBoundary(BaseModel):
    """One COMPLETED OOS evaluation's own identity and OOS window --
    requirement 1's "OOS period boundaries" and "completed OOS
    evaluation IDs", together, so a reader never has to cross-reference
    a separate evaluation_id list against a separate boundary list."""

    evaluation_id: str
    oos_partition_id: str
    oos_start: datetime
    oos_end: datetime


class OOSEpisodeSampleSizes(BaseModel):
    """Requirement 2's own explicit "report separately: raw signal
    count, episode count, evaluation/period count" -- summed across
    every INCLUDED (completed) period, plus the baseline's own two
    effective sample sizes (Method A's non-overlapping count, Method
    B's full pooled series length) so a reader never has to compute any
    of these by re-deriving them from the per-period results."""

    evaluation_count: int
    raw_signal_count: int
    episode_count: int
    baseline_raw_observations: int
    method_a_effective_baseline_n: int


class OOSPeriodConsistencyResult(BaseModel):
    """Requirement 10: per-period descriptive results, so a reader can
    see whether the effect is consistent, concentrated in one period,
    or directionally mixed -- computed over that period's OWN
    episode-representative returns (the independent unit this whole
    review treats as primary, applied consistently at the period level
    too -- never the raw, clustered signal list) via
    app.backtesting.aggregation.aggregate_results() (reused,
    UNMODIFIED). Descriptive only -- no CI, no p-value, no verdict at
    the per-period level; this feature's own methodology only supports
    formal inference on the POOLED, cross-period sample (requirement 4)."""

    evaluation_id: str
    oos_partition_id: str
    oos_start: datetime
    oos_end: datetime
    raw_signal_count: int
    episode_count: int
    mean_return: float | None
    median_return: float | None
    win_rate: float | None
    std_dev_return: float | None


class OOSStatisticalReview(BaseModel):
    """The full, immutable, append-only review record (app/storage/
    oos_statistical_review_repository.py -- `id` is a random id, like
    `oos_evaluations.id`, not a deterministic hash: running the review
    again against the SAME evidence produces a brand-new row with
    IDENTICAL analytical content, never overwriting or replacing a
    prior review, matching this app's `oos_evaluations`/
    `experiment_oos_periods` append-only precedent exactly).

    Every research-defining fact (`hypothesis_hash`, `symbol`/
    `timeframe`/`provider`/`feature_contract_version`/
    `outcome_*`/`primary_window_bars`) is read from the immutable
    ExperimentFreezeSnapshot and cross-checked against every included
    evaluation's own recorded copy of the same facts (app/
    oos_statistical_review/engine.py::_verify_uniform_provenance()) --
    NEVER from the live, mutable `Experiment` row. `review_config_version`
    plus every resampling parameter (`seed`/`n_resamples`/`ci_level`/
    `block_length_multiplier`/`power_target`/`min_episodes_for_formal_test`)
    are stored on the record itself -- requirement 11's own
    "reproducible" guarantee: anyone can look at this ONE record and
    know EXACTLY how to reproduce it byte-for-byte, without needing to
    know what today's code defaults happen to be.

    `method_a_*`/`method_b_*`/`effect_size`/`power_analysis`/
    `robustness` are all `None` together when
    `sample_sizes.episode_count < min_episodes_for_formal_test` (or the
    OOS-scoped baseline came back with fewer than two observations) --
    `verdict` is then always `INSUFFICIENT_DATA` and no formal
    statistic is fabricated from too little data (requirement 9's own
    "do NOT post-hoc declare a hypothesis successful because it is
    underpowered", extended here to "do not even attempt a formal test
    on a sample too small to responsibly run one at all").

    `exploratory_horizons_note` explains, in every review, why
    `exploratory_horizons` is always empty in V1 -- see that field's own
    docstring below.
    """

    id: str
    experiment_id: str
    frozen_snapshot_id: str
    hypothesis_hash: str
    review_config_version: str
    created_at: datetime

    # ---- Input / provenance (requirement 1) ----
    included_evaluation_ids: list[str]
    excluded_evaluations: list[ExcludedEvaluation]
    oos_periods: list[OOSPeriodBoundary]
    outcome_metric: str
    outcome_operator: str
    outcome_threshold: float
    outcome_horizon_minutes: int
    primary_window_bars: int
    symbol: str
    timeframe: str
    provider: str
    feature_contract_version: str

    # ---- Deterministic configuration (requirement 6, immutable per record) ----
    seed: int
    n_resamples: int
    ci_level: float
    block_length_multiplier: int
    power_target: float
    min_episodes_for_formal_test: int

    # ---- Sample sizes (requirement 2) ----
    sample_sizes: OOSEpisodeSampleSizes

    # ---- Primary test (requirement 4/5/6), None together when underpowered (requirement 9) ----
    method_a_mean_difference: MeanDifferenceResultV2 | None
    method_a_win_rate_difference: WinRateDifferenceResultV2 | None
    method_a_test: DependenceAwareTestResultV2 | None
    method_b_mean_difference: MeanDifferenceResultV2 | None
    method_b_win_rate_difference: WinRateDifferenceResultV2 | None
    method_b_test: DependenceAwareTestResultV2 | None
    effect_size: EffectSizeResultV2 | None
    power_analysis: PowerAnalysisResultV2 | None
    robustness: RobustnessComparisonV2 | None

    # ---- Multiple horizons (requirement 7) ----
    exploratory_horizons_note: str

    # ---- Per-period consistency (requirement 10) ----
    per_period_results: list[OOSPeriodConsistencyResult]

    # ---- Verdict (requirement 8) ----
    verdict: OOSStatisticalVerdict
    verdict_reasoning: str
