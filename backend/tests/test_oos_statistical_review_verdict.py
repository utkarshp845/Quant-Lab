"""Tests for app/oos_statistical_review/verdict.py -- pure, deterministic
verdict logic, no I/O, no database.

Covers requirement 13's "Statistics: conclusion consistency is
correctly detected" plus the explicit instruction "do not equate
p >= 0.05 with 'the hypothesis is false', and do not equate p < 0.05
with 'the strategy is profitable'".
"""

from app.models.oos_statistical_review import OOSStatisticalVerdict
from app.models.research import ConditionOperator, Outcome
from app.models.statistical_validation_v2 import BaselineMethodV2, DependenceAwareTestResultV2, MeanDifferenceResultV2
from app.oos_statistical_review.verdict import determine_verdict, hypothesized_direction


def _test_result(method: BaselineMethodV2, *, p_value: float, ci_low: float, ci_high: float, difference: float = None) -> tuple[DependenceAwareTestResultV2, MeanDifferenceResultV2]:
    difference = ci_low + (ci_high - ci_low) / 2 if difference is None else difference
    test = DependenceAwareTestResultV2(
        method=method, window_bars=3, observed_mean_difference=difference, p_value_two_sided=p_value,
        n_resamples=1000, n_conditioned=20, n_baseline=100, seed=1337,
    )
    mean_diff = MeanDifferenceResultV2(
        method=method, window_bars=3, conditioned_mean=difference, baseline_mean=0.0, difference=difference,
        ci_low=ci_low, ci_high=ci_high, ci_level=0.95, n_conditioned=20, n_baseline=100,
    )
    return test, mean_diff


class TestHypothesizedDirection:
    def test_gt_is_positive_direction(self):
        outcome = Outcome(metric="forward_return", horizon_minutes=15, operator=ConditionOperator.GT, threshold=0.0)
        assert hypothesized_direction(outcome) == 1

    def test_gte_is_positive_direction(self):
        outcome = Outcome(metric="forward_return", horizon_minutes=15, operator=ConditionOperator.GTE, threshold=0.0)
        assert hypothesized_direction(outcome) == 1

    def test_lt_is_negative_direction(self):
        outcome = Outcome(metric="forward_return", horizon_minutes=15, operator=ConditionOperator.LT, threshold=0.0)
        assert hypothesized_direction(outcome) == -1

    def test_lte_is_negative_direction(self):
        outcome = Outcome(metric="forward_return", horizon_minutes=15, operator=ConditionOperator.LTE, threshold=0.0)
        assert hypothesized_direction(outcome) == -1

    def test_eq_has_no_direction(self):
        outcome = Outcome(metric="forward_return", horizon_minutes=15, operator=ConditionOperator.EQ, threshold=0.0)
        assert hypothesized_direction(outcome) is None


class TestSupported:
    def test_both_methods_significant_in_hypothesized_direction_is_supported(self):
        a_test, a_mean = _test_result(BaselineMethodV2.NON_OVERLAPPING_WINDOWS, p_value=0.01, ci_low=0.001, ci_high=0.01, difference=0.005)
        b_test, b_mean = _test_result(BaselineMethodV2.MOVING_BLOCK_BOOTSTRAP, p_value=0.02, ci_low=0.0005, ci_high=0.009, difference=0.004)
        verdict, reasoning = determine_verdict(
            direction=1, method_a_test=a_test, method_a_mean_difference=a_mean,
            method_b_test=b_test, method_b_mean_difference=b_mean, effect_size_d=0.5,
        )
        assert verdict == OOSStatisticalVerdict.SUPPORTED
        assert "Method A" in reasoning and "Method B" in reasoning


class TestNotSupported:
    def test_both_methods_significant_but_wrong_direction_is_not_supported(self):
        a_test, a_mean = _test_result(BaselineMethodV2.NON_OVERLAPPING_WINDOWS, p_value=0.01, ci_low=-0.01, ci_high=-0.001, difference=-0.005)
        b_test, b_mean = _test_result(BaselineMethodV2.MOVING_BLOCK_BOOTSTRAP, p_value=0.02, ci_low=-0.009, ci_high=-0.0005, difference=-0.004)
        verdict, _reasoning = determine_verdict(
            direction=1, method_a_test=a_test, method_a_mean_difference=a_mean,
            method_b_test=b_test, method_b_mean_difference=b_mean, effect_size_d=-0.5,
        )
        assert verdict == OOSStatisticalVerdict.NOT_SUPPORTED


class TestInconclusive:
    def test_p_above_alpha_is_inconclusive_never_not_supported(self):
        """The feature's own explicit instruction: p >= 0.05 must NEVER
        be treated as evidence the hypothesis is false."""
        a_test, a_mean = _test_result(BaselineMethodV2.NON_OVERLAPPING_WINDOWS, p_value=0.6, ci_low=-0.002, ci_high=0.003)
        b_test, b_mean = _test_result(BaselineMethodV2.MOVING_BLOCK_BOOTSTRAP, p_value=0.55, ci_low=-0.002, ci_high=0.0025)
        verdict, _reasoning = determine_verdict(
            direction=1, method_a_test=a_test, method_a_mean_difference=a_mean,
            method_b_test=b_test, method_b_mean_difference=b_mean, effect_size_d=0.05,
        )
        assert verdict == OOSStatisticalVerdict.INCONCLUSIVE

    def test_only_one_method_significant_is_inconclusive(self):
        a_test, a_mean = _test_result(BaselineMethodV2.NON_OVERLAPPING_WINDOWS, p_value=0.01, ci_low=0.001, ci_high=0.01, difference=0.005)
        b_test, b_mean = _test_result(BaselineMethodV2.MOVING_BLOCK_BOOTSTRAP, p_value=0.4, ci_low=-0.002, ci_high=0.006, difference=0.002)
        verdict, reasoning = determine_verdict(
            direction=1, method_a_test=a_test, method_a_mean_difference=a_mean,
            method_b_test=b_test, method_b_mean_difference=b_mean, effect_size_d=0.4,
        )
        assert verdict == OOSStatisticalVerdict.INCONCLUSIVE
        assert "Method B" in reasoning

    def test_significant_but_negligible_effect_is_inconclusive(self):
        """Do NOT choose whichever method gives the more favorable
        result AND do not call a statistically-detectable-but-tiny
        effect SUPPORTED -- 'not merely a tiny/noisy deviation'."""
        a_test, a_mean = _test_result(BaselineMethodV2.NON_OVERLAPPING_WINDOWS, p_value=0.001, ci_low=0.0001, ci_high=0.0003, difference=0.0002)
        b_test, b_mean = _test_result(BaselineMethodV2.MOVING_BLOCK_BOOTSTRAP, p_value=0.002, ci_low=0.0001, ci_high=0.0004, difference=0.0002)
        verdict, reasoning = determine_verdict(
            direction=1, method_a_test=a_test, method_a_mean_difference=a_mean,
            method_b_test=b_test, method_b_mean_difference=b_mean, effect_size_d=0.05,  # negligible
        )
        assert verdict == OOSStatisticalVerdict.INCONCLUSIVE
        assert "negligible" in reasoning

    def test_ci_including_zero_treated_as_not_significant_even_with_low_p(self):
        """A method's own p-value and CI must AGREE for that method to
        count as significant -- see _is_significant()'s own docstring."""
        a_test, a_mean = _test_result(BaselineMethodV2.NON_OVERLAPPING_WINDOWS, p_value=0.001, ci_low=-0.001, ci_high=0.01, difference=0.005)
        b_test, b_mean = _test_result(BaselineMethodV2.MOVING_BLOCK_BOOTSTRAP, p_value=0.01, ci_low=0.001, ci_high=0.01, difference=0.005)
        verdict, _reasoning = determine_verdict(
            direction=1, method_a_test=a_test, method_a_mean_difference=a_mean,
            method_b_test=b_test, method_b_mean_difference=b_mean, effect_size_d=0.5,
        )
        assert verdict == OOSStatisticalVerdict.INCONCLUSIVE

    def test_methods_disagreeing_on_direction_is_inconclusive(self):
        a_test, a_mean = _test_result(BaselineMethodV2.NON_OVERLAPPING_WINDOWS, p_value=0.01, ci_low=0.001, ci_high=0.01, difference=0.005)
        b_test, b_mean = _test_result(BaselineMethodV2.MOVING_BLOCK_BOOTSTRAP, p_value=0.01, ci_low=-0.01, ci_high=-0.001, difference=-0.005)
        verdict, _reasoning = determine_verdict(
            direction=1, method_a_test=a_test, method_a_mean_difference=a_mean,
            method_b_test=b_test, method_b_mean_difference=b_mean, effect_size_d=0.0,
        )
        assert verdict == OOSStatisticalVerdict.INCONCLUSIVE

    def test_eq_outcome_has_no_directional_requirement(self):
        """direction=None (EQ outcome) -- a significant, meaningful
        effect in EITHER direction is SUPPORTED."""
        a_test, a_mean = _test_result(BaselineMethodV2.NON_OVERLAPPING_WINDOWS, p_value=0.01, ci_low=-0.01, ci_high=-0.001, difference=-0.005)
        b_test, b_mean = _test_result(BaselineMethodV2.MOVING_BLOCK_BOOTSTRAP, p_value=0.02, ci_low=-0.009, ci_high=-0.0005, difference=-0.004)
        verdict, _reasoning = determine_verdict(
            direction=None, method_a_test=a_test, method_a_mean_difference=a_mean,
            method_b_test=b_test, method_b_mean_difference=b_mean, effect_size_d=-0.5,
        )
        assert verdict == OOSStatisticalVerdict.SUPPORTED
