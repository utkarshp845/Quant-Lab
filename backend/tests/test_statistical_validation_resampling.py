"""Tests for app/statistical_validation/resampling.py -- bootstrap CIs,
the permutation test, and Cohen's d, in isolation from the engine and
from any real backtest data. Every randomized test asserts determinism
(same seed -> identical output) explicitly, since that guarantee is
this module's entire reason for taking a caller-supplied
numpy.random.Generator instead of seeding its own.
"""

import statistics as pystatistics

import numpy as np
import pytest

from app.statistical_validation.resampling import (
    bootstrap_mean_difference_ci,
    bootstrap_win_rate_ci,
    cohens_d,
    permutation_test_mean_difference,
)

# A clearly-negative, tightly-clustered "conditioned" sample and a
# clearly-different, roughly-zero-mean "baseline" sample -- large
# enough (n=40) that bootstrap/permutation results should be stable
# and unambiguous, small enough that tests run fast.
_CONDITIONED = [-0.02, -0.015, -0.025, -0.01, -0.03, -0.018, -0.022, -0.012] * 5
_BASELINE = ([0.001] * 50) + ([-0.001] * 50)


class TestBootstrapMeanDifferenceCI:
    def test_same_seed_reproduces_the_identical_interval(self):
        lo1, hi1 = bootstrap_mean_difference_ci(_CONDITIONED, _BASELINE, rng=np.random.default_rng(42), n_resamples=2000)
        lo2, hi2 = bootstrap_mean_difference_ci(_CONDITIONED, _BASELINE, rng=np.random.default_rng(42), n_resamples=2000)
        assert (lo1, hi1) == (lo2, hi2)

    def test_lower_bound_is_never_above_the_upper_bound(self):
        lo, hi = bootstrap_mean_difference_ci(_CONDITIONED, _BASELINE, rng=np.random.default_rng(1), n_resamples=2000)
        assert lo <= hi

    def test_a_clearly_negative_difference_produces_an_entirely_negative_interval(self):
        lo, hi = bootstrap_mean_difference_ci(_CONDITIONED, _BASELINE, rng=np.random.default_rng(7), n_resamples=5000)
        assert hi < 0

    def test_identical_populations_produce_an_interval_straddling_zero(self):
        same = [0.01, -0.005, 0.02, -0.01, 0.0, 0.015] * 10
        lo, hi = bootstrap_mean_difference_ci(same, same, rng=np.random.default_rng(3), n_resamples=3000)
        assert lo <= 0 <= hi

    def test_a_wider_confidence_level_produces_a_wider_or_equal_interval(self):
        rng_a, rng_b = np.random.default_rng(99), np.random.default_rng(99)
        lo95, hi95 = bootstrap_mean_difference_ci(_CONDITIONED, _BASELINE, rng=rng_a, n_resamples=3000, ci_level=0.95)
        lo80, hi80 = bootstrap_mean_difference_ci(_CONDITIONED, _BASELINE, rng=rng_b, n_resamples=3000, ci_level=0.80)
        assert (hi95 - lo95) >= (hi80 - lo80)

    def test_resampling_more_than_the_batch_size_still_produces_the_requested_count_worth_of_stable_output(self):
        """_bootstrapped_mean_diffs chunks internally at 1,000
        resamples -- exercise a resample count that spans multiple
        chunks and confirm determinism still holds across the
        boundary."""
        lo1, hi1 = bootstrap_mean_difference_ci(_CONDITIONED, _BASELINE, rng=np.random.default_rng(5), n_resamples=2500)
        lo2, hi2 = bootstrap_mean_difference_ci(_CONDITIONED, _BASELINE, rng=np.random.default_rng(5), n_resamples=2500)
        assert (lo1, hi1) == (lo2, hi2)


class TestBootstrapWinRateCI:
    def test_same_seed_reproduces_the_identical_interval(self):
        lo1, hi1 = bootstrap_win_rate_ci(_CONDITIONED, _BASELINE, rng=np.random.default_rng(42), n_resamples=2000)
        lo2, hi2 = bootstrap_win_rate_ci(_CONDITIONED, _BASELINE, rng=np.random.default_rng(42), n_resamples=2000)
        assert (lo1, hi1) == (lo2, hi2)

    def test_bounds_are_within_the_valid_win_rate_difference_range(self):
        lo, hi = bootstrap_win_rate_ci(_CONDITIONED, _BASELINE, rng=np.random.default_rng(11), n_resamples=2000)
        assert -1.0 <= lo <= hi <= 1.0

    def test_all_losses_vs_all_wins_produces_an_interval_at_negative_one(self):
        all_losses = [-0.01] * 20
        all_wins = [0.01] * 20
        lo, hi = bootstrap_win_rate_ci(all_losses, all_wins, rng=np.random.default_rng(1), n_resamples=1000)
        assert lo == hi == pytest.approx(-1.0)


class TestPermutationTest:
    def test_same_seed_reproduces_the_identical_result(self):
        obs1, p1 = permutation_test_mean_difference(_CONDITIONED, _BASELINE, rng=np.random.default_rng(42), n_permutations=2000)
        obs2, p2 = permutation_test_mean_difference(_CONDITIONED, _BASELINE, rng=np.random.default_rng(42), n_permutations=2000)
        assert (obs1, p1) == (obs2, p2)

    def test_observed_difference_matches_the_plain_mean_difference(self):
        obs, _ = permutation_test_mean_difference(_CONDITIONED, _BASELINE, rng=np.random.default_rng(1), n_permutations=100)
        assert obs == pytest.approx(pystatistics.mean(_CONDITIONED) - pystatistics.mean(_BASELINE))

    def test_p_value_is_strictly_between_zero_and_one(self):
        _, p = permutation_test_mean_difference(_CONDITIONED, _BASELINE, rng=np.random.default_rng(2), n_permutations=1000)
        assert 0 < p <= 1

    def test_p_value_is_never_zero_even_with_an_extreme_difference(self):
        """The +1/+1 correction guarantees p >= 1/(n_permutations+1),
        never exactly 0.0, however extreme the observed difference."""
        extreme_conditioned = [-10.0] * 20
        ordinary_baseline = [0.0] * 20
        _, p = permutation_test_mean_difference(extreme_conditioned, ordinary_baseline, rng=np.random.default_rng(3), n_permutations=500)
        assert p == pytest.approx(1 / 501)

    def test_identical_populations_produce_a_large_p_value(self):
        same = [0.01, -0.005, 0.02, -0.01, 0.0, 0.015] * 10
        _, p = permutation_test_mean_difference(same, same, rng=np.random.default_rng(4), n_permutations=2000)
        assert p > 0.5

    def test_a_clearly_different_population_produces_a_small_p_value(self):
        _, p = permutation_test_mean_difference(_CONDITIONED, _BASELINE, rng=np.random.default_rng(5), n_permutations=5000)
        assert p < 0.01


class TestCohensD:
    def test_matches_hand_computed_value(self):
        # Two simple groups where the pooled stdev is hand-computable.
        group_a = [1.0, 2.0, 3.0]  # mean=2, stdev=1
        group_b = [4.0, 5.0, 6.0]  # mean=5, stdev=1
        d, pooled_stdev = cohens_d(group_a, group_b)
        assert pooled_stdev == pytest.approx(1.0)
        assert d == pytest.approx((2.0 - 5.0) / 1.0)

    def test_sign_reflects_direction_conditioned_minus_baseline(self):
        d, _ = cohens_d(_CONDITIONED, _BASELINE)
        assert d < 0  # conditioned is more negative than baseline

    def test_fewer_than_two_observations_in_either_group_returns_zero_not_a_crash(self):
        assert cohens_d([0.01], _BASELINE) == (0.0, 0.0)
        assert cohens_d(_CONDITIONED, [0.01]) == (0.0, 0.0)
        assert cohens_d([], []) == (0.0, 0.0)

    def test_zero_variance_in_both_groups_returns_zero_not_a_division_error(self):
        assert cohens_d([1.0, 1.0, 1.0], [1.0, 1.0, 1.0]) == (0.0, 0.0)
