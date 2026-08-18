"""Pure, deterministic verdict logic for OOS Statistical Review V1 --
no I/O, matching app/statistical_validation/v2/engine.py's own
"mechanical check, not a subjective judgment call baked into the data
model" precedent (RobustnessComparisonV2.conclusion_changes_materially),
extended here into the review's own top-level SUPPORTED/NOT_SUPPORTED/
INCONCLUSIVE/INSUFFICIENT_DATA verdict.

IMPORTANT, this feature's own explicit instruction: `p >= 0.05` is
NEVER equated with "the hypothesis is false" (that maps to
INCONCLUSIVE, not NOT_SUPPORTED); `p < 0.05` is NEVER equated with
"the strategy is profitable" (nothing in this module's inputs, or
anywhere in this feature, measures trading costs, execution, or
profitability -- only a forward-return difference from an unconditional
baseline).
"""

from app.models.research import ConditionOperator, Outcome
from app.models.statistical_validation_v2 import DependenceAwareTestResultV2, MeanDifferenceResultV2
from app.models.oos_statistical_review import OOSStatisticalVerdict

# Cohen's d magnitude below which an effect is "negligible" -- the SAME
# conventional threshold app.statistical_validation.engine::
# _interpret_cohens_d() (and its V2 counterpart) already label
# "negligible (below the conventional 'small' threshold of 0.2)" --
# reused as a NUMBER here (not re-derived) so SUPPORTED's own "not
# merely a tiny/noisy deviation" requirement uses the identical
# threshold this codebase already reports the effect size against.
_NEGLIGIBLE_EFFECT_SIZE_THRESHOLD = 0.2

# The conventional two-sided significance level this whole review's
# methodology is built around (Statistical Validation V1/V2's own
# fixed choice, reused here, never re-derived per result).
_ALPHA_TWO_SIDED = 0.05


def hypothesized_direction(outcome: Outcome) -> int | None:
    """+1 if the frozen Outcome expects the conditioned population's
    forward return to be GREATER than baseline (operator GT/GTE), -1 if
    LESS (LT/LTE), or None for EQ (no directional expectation --
    requirement 8's SUPPORTED/NOT_SUPPORTED logic below simply skips
    the directional check in that case, since Research v1's Outcome
    model (app/models/research.py) has never supported a directional
    claim for an exact-equality threshold in the first place)."""
    if outcome.operator in (ConditionOperator.GT, ConditionOperator.GTE):
        return 1
    if outcome.operator in (ConditionOperator.LT, ConditionOperator.LTE):
        return -1
    return None


def _is_significant(test: DependenceAwareTestResultV2, mean_difference: MeanDifferenceResultV2) -> bool:
    """A method's result counts as "statistically significant" only
    when BOTH its own empirical p-value is below alpha AND its own
    confidence interval for the mean difference excludes zero -- these
    are two views of the same underlying resampling distribution
    (requirement 4/5's own dependence-aware CI and p-value, computed
    from the identical resampled distribution in every caller of this
    function -- app/oos_statistical_review/engine.py), so requiring
    both is a consistency check, not a stricter alpha: the p-value's
    own +1/+1-corrected floor (app.statistical_validation.resampling::
    permutation_test_mean_difference()'s own docstring) means the two
    can occasionally disagree at the margin, and this review treats
    that disagreement as "not yet significant" rather than picking
    whichever of the two looks more favorable."""
    ci_excludes_zero = not (mean_difference.ci_low <= 0 <= mean_difference.ci_high)
    return test.p_value_two_sided < _ALPHA_TWO_SIDED and ci_excludes_zero


def _direction_matches(observed_mean_difference: float, direction: int | None) -> bool:
    """True if `direction` is None (EQ outcome -- no directional claim
    to check) or `observed_mean_difference` has the same sign as
    `direction`. A difference of exactly 0.0 never "matches" a
    non-None direction -- zero is not evidence of an effect in either
    direction."""
    if direction is None:
        return True
    return (observed_mean_difference > 0 and direction > 0) or (observed_mean_difference < 0 and direction < 0)


def determine_verdict(
    *,
    direction: int | None,
    method_a_test: DependenceAwareTestResultV2,
    method_a_mean_difference: MeanDifferenceResultV2,
    method_b_test: DependenceAwareTestResultV2,
    method_b_mean_difference: MeanDifferenceResultV2,
    effect_size_d: float,
) -> tuple[OOSStatisticalVerdict, str]:
    """The SUPPORTED/NOT_SUPPORTED/INCONCLUSIVE decision, GIVEN that a
    formal test was already run (both dependence-aware methods fully
    computed). The caller (app/oos_statistical_review/engine.py) is
    responsible for the INSUFFICIENT_DATA gate BEFORE ever calling this
    function -- "too few independent OOS episodes (or too little
    OOS-scoped baseline data) to perform a responsible formal test" is
    decided once, in one place, on the actual sample sizes, never
    re-derived from a statistic this function computes. Returns
    (verdict, human-readable reasoning) -- the reasoning string is
    stored on the review record itself (app/models/
    oos_statistical_review.py::OOSStatisticalReview.verdict_reasoning)
    so a reader never has to re-derive WHY a verdict was assigned from
    the raw numbers alone.

    Rules, in order (requirement 8):

      1. SUPPORTED -- BOTH Method A and Method B are independently
         statistically significant (_is_significant() above, for each)
         AND both point in the hypothesized direction (or `direction`
         is None) AND the effect size is not negligible
         (|cohens_d| >= 0.2). Requiring BOTH dependence-aware methods
         to agree is this review's own answer to requirement 5's "do
         NOT choose whichever method gives the more favorable result" --
         a result that only ONE method finds significant is, by
         construction, never SUPPORTED here.

      2. NOT_SUPPORTED -- BOTH methods are independently statistically
         significant, but BOTH point AWAY from the hypothesized
         direction (i.e. a real, dependence-robust effect exists, in
         the OPPOSITE direction from what the hypothesis predicted).

      3. INCONCLUSIVE -- everything else: only one method significant,
         neither method significant, the two methods disagree on
         direction, or an effect exists in the right direction but is
         negligible in magnitude. `p >= 0.05` alone lands here, NEVER
         at NOT_SUPPORTED (this function's own explicit instruction).
    """
    a_significant = _is_significant(method_a_test, method_a_mean_difference)
    b_significant = _is_significant(method_b_test, method_b_mean_difference)
    a_direction_ok = _direction_matches(method_a_mean_difference.difference, direction)
    b_direction_ok = _direction_matches(method_b_mean_difference.difference, direction)
    meaningful_effect = abs(effect_size_d) >= _NEGLIGIBLE_EFFECT_SIZE_THRESHOLD

    if a_significant and b_significant and a_direction_ok and b_direction_ok and meaningful_effect:
        return (
            OOSStatisticalVerdict.SUPPORTED,
            "Both Method A (non-overlapping windows) and Method B (moving block bootstrap) independently found "
            f"a statistically significant (p < {_ALPHA_TWO_SIDED}, 95% CI excludes zero) difference in the "
            f"hypothesized direction, with a non-negligible effect size (|Cohen's d| = {abs(effect_size_d):.3f}).",
        )

    if a_significant and b_significant and not a_direction_ok and not b_direction_ok:
        return (
            OOSStatisticalVerdict.NOT_SUPPORTED,
            "Both Method A and Method B independently found a statistically significant "
            f"(p < {_ALPHA_TWO_SIDED}, 95% CI excludes zero) difference, but in the OPPOSITE direction from the "
            "frozen hypothesis' own prediction.",
        )

    reasons = []
    if not a_significant:
        reasons.append("Method A did not reach significance (p >= 0.05 or its CI includes zero)")
    if not b_significant:
        reasons.append("Method B did not reach significance (p >= 0.05 or its CI includes zero)")
    if a_significant and not a_direction_ok:
        reasons.append("Method A's significant effect points opposite the hypothesized direction")
    if b_significant and not b_direction_ok:
        reasons.append("Method B's significant effect points opposite the hypothesized direction")
    if a_significant and b_significant and a_direction_ok and b_direction_ok and not meaningful_effect:
        reasons.append(f"the effect size is negligible (|Cohen's d| = {abs(effect_size_d):.3f} < {_NEGLIGIBLE_EFFECT_SIZE_THRESHOLD})")
    return (
        OOSStatisticalVerdict.INCONCLUSIVE,
        "Evidence is mixed or insufficient to distinguish the hypothesis from noise: " + "; ".join(reasons) + ".",
    )
