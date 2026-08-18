"""Tests for app/statistical_validation/v2/resampling.py -- the moving
block bootstrap (Method B), in isolation from the engine and real data.
"""

import numpy as np
import pytest

from app.statistical_validation.v2.resampling import (
    moving_block_bootstrap_mean,
    moving_block_bootstrap_mean_difference_ci,
    moving_block_bootstrap_p_value,
    moving_block_bootstrap_win_rate_ci,
)

# A periodic series (period 5) so its true mean is exactly hand-computable,
# and a block_length that's a multiple of the period so per-block content
# is deterministic regardless of block start offset -- useful for exact
# assertions on some tests below.
_PERIODIC_SERIES = np.array([0.01, -0.02, 0.005, 0.0, -0.01] * 200, dtype=float)  # n=1000, period=5
_PERIODIC_MEAN = _PERIODIC_SERIES[:5].mean()

_CONDITIONED = [-0.02, -0.015, -0.025, -0.01, -0.03] * 13  # 65 obs, clearly negative


class TestMovingBlockBootstrapMean:
    def test_same_seed_reproduces_the_identical_distribution(self):
        m1 = moving_block_bootstrap_mean(_PERIODIC_SERIES, block_length=20, rng=np.random.default_rng(1), n_resamples=1000)
        m2 = moving_block_bootstrap_mean(_PERIODIC_SERIES, block_length=20, rng=np.random.default_rng(1), n_resamples=1000)
        assert np.array_equal(m1, m2)

    def test_returns_the_requested_number_of_resamples(self):
        means = moving_block_bootstrap_mean(_PERIODIC_SERIES, block_length=13, rng=np.random.default_rng(2), n_resamples=2500)
        assert means.shape == (2500,)

    def test_a_block_length_that_evenly_divides_the_period_gives_an_exact_mean_every_time(self):
        """block_length=20 is a multiple of the period (5) -- any
        contiguous window of length 20 in this periodic series contains
        exactly 4 full periods, so every resampled mean must equal the
        true series mean exactly, regardless of which block start
        positions were drawn."""
        means = moving_block_bootstrap_mean(_PERIODIC_SERIES, block_length=20, rng=np.random.default_rng(3), n_resamples=500)
        assert np.allclose(means, _PERIODIC_MEAN)

    def test_block_length_exceeding_series_length_raises(self):
        with pytest.raises(ValueError, match="must not exceed"):
            moving_block_bootstrap_mean(np.array([1.0, 2.0, 3.0]), block_length=10, rng=np.random.default_rng(1), n_resamples=10)

    def test_non_positive_block_length_raises(self):
        with pytest.raises(ValueError, match="positive"):
            moving_block_bootstrap_mean(_PERIODIC_SERIES, block_length=0, rng=np.random.default_rng(1), n_resamples=10)

    def test_resample_count_spanning_multiple_internal_batches_stays_deterministic(self):
        """Internally batches at 1,000 resamples per chunk -- exercise a
        count spanning multiple chunks."""
        m1 = moving_block_bootstrap_mean(_PERIODIC_SERIES, block_length=15, rng=np.random.default_rng(9), n_resamples=2500)
        m2 = moving_block_bootstrap_mean(_PERIODIC_SERIES, block_length=15, rng=np.random.default_rng(9), n_resamples=2500)
        assert np.array_equal(m1, m2)


class TestMovingBlockBootstrapMeanDifferenceCI:
    def test_same_seed_reproduces_the_identical_interval(self):
        lo1, hi1 = moving_block_bootstrap_mean_difference_ci(_CONDITIONED, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(42), n_resamples=1000)
        lo2, hi2 = moving_block_bootstrap_mean_difference_ci(_CONDITIONED, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(42), n_resamples=1000)
        assert (lo1, hi1) == (lo2, hi2)

    def test_lower_bound_never_exceeds_upper_bound(self):
        lo, hi = moving_block_bootstrap_mean_difference_ci(_CONDITIONED, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(5), n_resamples=1000)
        assert lo <= hi

    def test_a_clearly_negative_conditioned_sample_produces_a_negative_interval(self):
        lo, hi = moving_block_bootstrap_mean_difference_ci(_CONDITIONED, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(6), n_resamples=3000)
        assert hi < 0

    def test_identical_populations_produce_an_interval_straddling_zero(self):
        same = list(_PERIODIC_SERIES[:65])
        lo, hi = moving_block_bootstrap_mean_difference_ci(same, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(7), n_resamples=3000)
        assert lo <= 0 <= hi


class TestMovingBlockBootstrapWinRateCI:
    def test_same_seed_reproduces_the_identical_interval(self):
        lo1, hi1 = moving_block_bootstrap_win_rate_ci(_CONDITIONED, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(1), n_resamples=1000)
        lo2, hi2 = moving_block_bootstrap_win_rate_ci(_CONDITIONED, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(1), n_resamples=1000)
        assert (lo1, hi1) == (lo2, hi2)

    def test_bounds_are_within_the_valid_range(self):
        lo, hi = moving_block_bootstrap_win_rate_ci(_CONDITIONED, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(4), n_resamples=1000)
        assert -1.0 <= lo <= hi <= 1.0


class TestMovingBlockBootstrapPValue:
    def test_same_seed_reproduces_the_identical_result(self):
        obs1, p1 = moving_block_bootstrap_p_value(_CONDITIONED, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(42), n_resamples=1000)
        obs2, p2 = moving_block_bootstrap_p_value(_CONDITIONED, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(42), n_resamples=1000)
        assert (obs1, p1) == (obs2, p2)

    def test_observed_difference_matches_the_plain_mean_difference(self):
        obs, _ = moving_block_bootstrap_p_value(_CONDITIONED, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(1), n_resamples=200)
        assert obs == pytest.approx(np.mean(_CONDITIONED) - _PERIODIC_SERIES.mean())

    def test_p_value_is_strictly_between_zero_and_one(self):
        _, p = moving_block_bootstrap_p_value(_CONDITIONED, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(2), n_resamples=1000)
        assert 0 < p <= 1

    def test_p_value_is_never_exactly_zero(self):
        extreme_conditioned = [-10.0] * 20
        _, p = moving_block_bootstrap_p_value(extreme_conditioned, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(3), n_resamples=500)
        assert p == pytest.approx(1 / 501)

    def test_identical_populations_produce_a_large_p_value(self):
        same = list(_PERIODIC_SERIES[:65])
        _, p = moving_block_bootstrap_p_value(same, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(4), n_resamples=2000)
        assert p > 0.3

    def test_a_clearly_different_population_produces_a_small_p_value(self):
        _, p = moving_block_bootstrap_p_value(_CONDITIONED, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(5), n_resamples=3000)
        assert p < 0.05

    def test_p_value_is_invariant_to_adding_the_same_constant_to_both_samples(self):
        """Only the DIFFERENCE between the two samples' means should
        matter, never their absolute level -- adding the same constant
        to both `conditioned` and `baseline_series` must leave the
        observed difference and the p-value unchanged (confirms the
        internal H0-shift is relative, not tied to any absolute
        scale)."""
        shift = 0.5
        shifted_conditioned = [x + shift for x in _CONDITIONED]
        shifted_baseline = list(_PERIODIC_SERIES + shift)

        obs1, p1 = moving_block_bootstrap_p_value(_CONDITIONED, list(_PERIODIC_SERIES), block_length=20, rng=np.random.default_rng(11), n_resamples=2000)
        obs2, p2 = moving_block_bootstrap_p_value(shifted_conditioned, shifted_baseline, block_length=20, rng=np.random.default_rng(11), n_resamples=2000)

        assert obs1 == pytest.approx(obs2)
        assert p1 == pytest.approx(p2)
