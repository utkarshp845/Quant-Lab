"""Resampling-based inference for Statistical Validation V1 -- bootstrap
confidence intervals, a permutation test, and a standardized effect
size, all computed on top of already-produced `forward_return` values
(app.backtesting.engine's own outcomes -- this module performs no
forward-return, MFE, or MAE math of its own; it only consumes numbers
the unmodified Backtesting v1 engine already produced).

Every randomized function here takes a `numpy.random.Generator` the
CALLER seeds (app/statistical_validation/engine.py seeds exactly one,
from one `seed` parameter, and threads it through every call in a
fixed order) rather than seeding its own -- this is what keeps this
app's existing "deterministic and reproducible" rule (see
app/research/engine.py, app/backtesting/engine.py) true for randomized
inference too: the same `seed` against the same underlying data always
reproduces the identical confidence interval / p-value / effect size,
never a different answer on every run.

Nothing here claims or tests causality -- a permutation test's null
hypothesis is "these two samples come from the same distribution,"
which is a statement about association, not about what caused what.
"""

import statistics as _statistics

import numpy as np

# Caps how many resamples are held in memory at once during a bootstrap
# (see _bootstrapped_mean_diffs below) -- the baseline sample can be
# several thousand observations, and materializing n_resamples x
# len(baseline) floats in one shot (e.g. 10,000 x 4,381 = ~350MB) is
# unnecessary memory pressure for a number this function only ever
# needs to reduce to its mean; chunking keeps peak memory bounded to
# roughly BATCH_SIZE x len(sample) floats instead.
_BOOTSTRAP_BATCH_SIZE = 1_000


def _bootstrapped_mean_diffs(
    treatment: np.ndarray, control: np.ndarray, *, rng: np.random.Generator, n_resamples: int, batch_size: int = _BOOTSTRAP_BATCH_SIZE
) -> np.ndarray:
    """The shared bootstrap machinery behind both
    bootstrap_mean_difference_ci() and bootstrap_win_rate_ci() below --
    a win rate IS a mean (of a 0/1 indicator array), so both are
    literally the same resampling computation applied to a differently
    encoded input. Resamples `treatment` and `control` INDEPENDENTLY,
    with replacement, `n_resamples` times, and returns the array of
    resampled mean(treatment) - mean(control) differences -- the
    empirical bootstrap distribution of the difference, from which a
    caller reads off percentile bounds for a confidence interval.
    """
    diffs = np.empty(n_resamples, dtype=float)
    done = 0
    while done < n_resamples:
        take = min(batch_size, n_resamples - done)
        treatment_batch = rng.choice(treatment, size=(take, len(treatment)), replace=True).mean(axis=1)
        control_batch = rng.choice(control, size=(take, len(control)), replace=True).mean(axis=1)
        diffs[done : done + take] = treatment_batch - control_batch
        done += take
    return diffs


def bootstrap_mean_difference_ci(
    conditioned: list[float],
    baseline: list[float],
    *,
    rng: np.random.Generator,
    n_resamples: int = 10_000,
    ci_level: float = 0.95,
) -> tuple[float, float]:
    """A percentile-bootstrap confidence interval (`ci_level`, default
    95%) for mean(conditioned) - mean(baseline). `conditioned` MUST
    already be the episode-level (non-overlapping) sample --
    app/statistical_validation/episodes.py -- resampling the raw,
    clustered 124-signal list here would treat correlated observations
    as independent draws and produce an interval that is too narrow
    (overconfident) to trust.
    """
    diffs = _bootstrapped_mean_diffs(
        np.asarray(conditioned, dtype=float), np.asarray(baseline, dtype=float), rng=rng, n_resamples=n_resamples
    )
    lower_q, upper_q = (1 - ci_level) / 2, 1 - (1 - ci_level) / 2
    return float(np.quantile(diffs, lower_q)), float(np.quantile(diffs, upper_q))


def bootstrap_win_rate_ci(
    conditioned_returns: list[float],
    baseline_returns: list[float],
    *,
    rng: np.random.Generator,
    n_resamples: int = 10_000,
    ci_level: float = 0.95,
) -> tuple[float, float]:
    """Same percentile-bootstrap machinery as
    bootstrap_mean_difference_ci(), applied to win/loss indicators
    (forward_return > 0 -> 1.0, else 0.0) instead of raw returns -- the
    resulting bounds are in FRACTIONAL win-rate units (0.0958, not
    9.58); app/statistical_validation/engine.py converts to percentage
    points when assembling WinRateDifferenceCI. Same episode-level
    requirement on `conditioned_returns` as above.
    """
    cond_wins = np.asarray([1.0 if r > 0 else 0.0 for r in conditioned_returns], dtype=float)
    base_wins = np.asarray([1.0 if r > 0 else 0.0 for r in baseline_returns], dtype=float)
    diffs = _bootstrapped_mean_diffs(cond_wins, base_wins, rng=rng, n_resamples=n_resamples)
    lower_q, upper_q = (1 - ci_level) / 2, 1 - (1 - ci_level) / 2
    return float(np.quantile(diffs, lower_q)), float(np.quantile(diffs, upper_q))


def permutation_test_mean_difference(
    conditioned: list[float], baseline: list[float], *, rng: np.random.Generator, n_permutations: int = 10_000
) -> tuple[float, float]:
    """Two-sided permutation test for H0: "the condition provides no
    information about this horizon's forward return beyond the
    unconditional baseline" -- i.e. `conditioned` and `baseline` are
    drawn from the same underlying distribution, and the group label
    (conditioned vs. baseline) carries no information. `conditioned`
    MUST be the episode-level sample, for the same reason as the
    bootstrap functions above.

    Pools both samples together, then repeatedly reassigns the pooled
    values to two groups of the ORIGINAL sizes uniformly at random
    (label-shuffling -- the standard construction of a permutation
    test's null distribution), recomputing the mean difference each
    time. The empirical two-sided p-value is the fraction of shuffled
    differences at least as extreme (by absolute value) as the
    OBSERVED difference, with Davison & Hinkley's standard +1/+1
    correction (numerator and denominator both incremented by one) --
    this guarantees a p-value that is never exactly 0.0 regardless of
    `n_permutations`, and is never smaller than 1/(n_permutations + 1)
    is capable of representing honestly.

    Returns (observed_difference, p_value_two_sided). Says nothing
    about WHY any difference exists -- only whether the observed
    association is larger than label-shuffling alone would typically
    produce.
    """
    cond_arr = np.asarray(conditioned, dtype=float)
    base_arr = np.asarray(baseline, dtype=float)
    observed = float(cond_arr.mean() - base_arr.mean())

    pooled = np.concatenate([cond_arr, base_arr])
    n_cond = len(cond_arr)

    at_least_as_extreme = 0
    for _ in range(n_permutations):
        rng.shuffle(pooled)  # in place; each call re-randomizes the full pooled array
        permuted_diff = pooled[:n_cond].mean() - pooled[n_cond:].mean()
        if abs(permuted_diff) >= abs(observed):
            at_least_as_extreme += 1

    p_value = (at_least_as_extreme + 1) / (n_permutations + 1)
    return observed, p_value


def cohens_d(conditioned: list[float], baseline: list[float]) -> tuple[float, float]:
    """Cohen's d: the mean difference standardized by the two samples'
    POOLED standard deviation -- "a simple standardized effect size,"
    per this feature's own spec, not a more elaborate model. Returns
    (d, pooled_stdev); `conditioned` should be the episode-level
    sample, matching every other statistic in this module.

    Returns (0.0, 0.0) -- rather than raising or producing NaN/inf --
    when either sample has fewer than 2 observations (sample stdev is
    undefined) or the pooled stdev is exactly 0 (no variance to
    standardize by at all); a caller distinguishes this degenerate case
    from a genuine zero effect by checking sample sizes directly (see
    app/models/statistical_validation.py::EffectSizeResult, which
    always carries the sample sizes alongside this result).
    """
    n1, n2 = len(conditioned), len(baseline)
    if n1 < 2 or n2 < 2:
        return 0.0, 0.0

    s1, s2 = _statistics.stdev(conditioned), _statistics.stdev(baseline)
    pooled_stdev = (((n1 - 1) * s1**2 + (n2 - 1) * s2**2) / (n1 + n2 - 2)) ** 0.5
    if pooled_stdev == 0:
        return 0.0, 0.0

    d = (_statistics.mean(conditioned) - _statistics.mean(baseline)) / pooled_stdev
    return d, pooled_stdev
