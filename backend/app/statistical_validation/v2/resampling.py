"""Method B (moving block bootstrap) for Statistical Validation V2 --
see app/statistical_validation/v2/__init__.py for how this fits
alongside Method A (non-overlapping windows, app/statistical_validation/
v2/baseline.py).

The standard technique (Kunsch 1989; Politis & Romano's moving block
bootstrap) for building a valid bootstrap sampling distribution from a
SERIALLY DEPENDENT series without discarding most of the sample the
way Method A's subsampling does: instead of resampling individual
(correlated) observations independently -- which is what V1's own
resampling module correctly does for the ALREADY-independent
episode-level conditioned sample, but would be invalid for the
overlapping baseline series -- this resamples CONTIGUOUS BLOCKS of
`block_length` consecutive observations, preserving whatever local
(short-range) dependence structure those blocks carry.

Block length: DEFAULT_BLOCK_LENGTH_MULTIPLIER x the horizon's own
window_bars (documented, fixed choice -- not tuned per result, see
app/statistical_validation/v2/engine.py). Two adjacent per-bar forward-
return observations share dependence out to about `window_bars` bars
apart (the mechanical window-overlap horizon); a block several times
that length averages over more than one such overlapping cluster while
still leaving many valid starting positions to resample from in a
multi-thousand-observation series -- a standard, simple rule of thumb
for choosing a block length that is neither so short it fails to
capture the dependence nor so long that too few blocks remain to
resample meaningfully.

Every randomized function here takes a caller-seeded
`numpy.random.Generator`, exactly like app/statistical_validation/
resampling.py -- the same seed against the same data always reproduces
an identical result.
"""

import math

import numpy as np

_BATCH_SIZE = 1_000


def moving_block_bootstrap_mean(
    series: np.ndarray, *, block_length: int, rng: np.random.Generator, n_resamples: int, batch_size: int = _BATCH_SIZE
) -> np.ndarray:
    """The dependence-respecting bootstrap sampling distribution of
    `series`'s own mean: for each of `n_resamples` iterations, draws
    ceil(len(series) / block_length) block START positions uniformly
    at random WITH REPLACEMENT from every valid starting position (0
    through len(series) - block_length, inclusive), concatenates the
    corresponding length-`block_length` blocks, truncates to
    len(series), and takes the mean. Returns the array of `n_resamples`
    resampled means.

    Raises ValueError if `block_length` exceeds len(series) -- there
    would be no valid starting position to draw from at all.
    """
    n = len(series)
    if block_length > n:
        raise ValueError(f"block_length ({block_length}) must not exceed the series length ({n}).")
    if block_length < 1:
        raise ValueError(f"block_length must be a positive integer, got {block_length}.")

    n_blocks_needed = math.ceil(n / block_length)
    max_start = n - block_length  # inclusive

    means = np.empty(n_resamples, dtype=float)
    done = 0
    while done < n_resamples:
        take = min(batch_size, n_resamples - done)
        starts = rng.integers(0, max_start + 1, size=(take, n_blocks_needed))
        block_offsets = np.arange(block_length)
        indices = starts[:, :, None] + block_offsets[None, None, :]  # (take, n_blocks_needed, block_length)
        resampled = series[indices].reshape(take, n_blocks_needed * block_length)[:, :n]
        means[done : done + take] = resampled.mean(axis=1)
        done += take
    return means


def _iid_bootstrap_means(sample: np.ndarray, *, rng: np.random.Generator, n_resamples: int, batch_size: int = _BATCH_SIZE) -> np.ndarray:
    """Plain i.i.d. bootstrap of `sample`'s own mean -- used for the
    CONDITIONED (episode-level) side of every function below, since
    episodes are already the independent unit V1 established; only the
    baseline side needs block-based resampling. Not exported: this is
    the ordinary bootstrap V1's own resampling.py already implements
    for BOTH sides of a comparison; duplicated here in miniature (a few
    lines) rather than importing V1's differently-shaped, two-sided
    `_bootstrapped_mean_diffs` helper, which resamples both sides the
    SAME (i.i.d.) way -- exactly what Method B must NOT do to the
    baseline side."""
    means = np.empty(n_resamples, dtype=float)
    done = 0
    while done < n_resamples:
        take = min(batch_size, n_resamples - done)
        means[done : done + take] = rng.choice(sample, size=(take, len(sample)), replace=True).mean(axis=1)
        done += take
    return means


def moving_block_bootstrap_mean_difference_ci(
    conditioned: list[float],
    baseline_series: list[float],
    *,
    block_length: int,
    rng: np.random.Generator,
    n_resamples: int = 10_000,
    ci_level: float = 0.95,
) -> tuple[float, float]:
    """A percentile CI for mean(conditioned) - mean(baseline_series).
    `conditioned` (the episode-level sample) is bootstrapped the
    ordinary i.i.d. way; `baseline_series` MUST be the full,
    chronologically-ordered (not shuffled) series of overlapping
    per-bar forward returns -- it is resampled via
    moving_block_bootstrap_mean() above, preserving its real serial
    dependence rather than assuming independence."""
    cond_arr = np.asarray(conditioned, dtype=float)
    base_arr = np.asarray(baseline_series, dtype=float)
    cond_means = _iid_bootstrap_means(cond_arr, rng=rng, n_resamples=n_resamples)
    base_means = moving_block_bootstrap_mean(base_arr, block_length=block_length, rng=rng, n_resamples=n_resamples)
    diffs = cond_means - base_means
    lower_q, upper_q = (1 - ci_level) / 2, 1 - (1 - ci_level) / 2
    return float(np.quantile(diffs, lower_q)), float(np.quantile(diffs, upper_q))


def moving_block_bootstrap_win_rate_ci(
    conditioned_returns: list[float],
    baseline_returns: list[float],
    *,
    block_length: int,
    rng: np.random.Generator,
    n_resamples: int = 10_000,
    ci_level: float = 0.95,
) -> tuple[float, float]:
    """Same block-aware machinery as moving_block_bootstrap_mean_difference_ci(),
    applied to win/loss indicators (forward_return > 0 -> 1.0, else
    0.0) instead of raw returns. Bounds are FRACTIONAL win-rate units."""
    cond_wins = np.asarray([1.0 if r > 0 else 0.0 for r in conditioned_returns], dtype=float)
    base_wins = np.asarray([1.0 if r > 0 else 0.0 for r in baseline_returns], dtype=float)
    cond_means = _iid_bootstrap_means(cond_wins, rng=rng, n_resamples=n_resamples)
    base_means = moving_block_bootstrap_mean(base_wins, block_length=block_length, rng=rng, n_resamples=n_resamples)
    diffs = cond_means - base_means
    lower_q, upper_q = (1 - ci_level) / 2, 1 - (1 - ci_level) / 2
    return float(np.quantile(diffs, lower_q)), float(np.quantile(diffs, upper_q))


def moving_block_bootstrap_p_value(
    conditioned: list[float],
    baseline_series: list[float],
    *,
    block_length: int,
    rng: np.random.Generator,
    n_resamples: int = 10_000,
) -> tuple[float, float]:
    """A dependence-aware analogue of V1's label-shuffling permutation
    test (app.statistical_validation.resampling.permutation_test_mean_difference),
    appropriate specifically because `baseline_series` is a long,
    serially-dependent time series that cannot be validly permuted
    point-by-point: shuffling individual points would destroy the
    baseline's real autocorrelation and produce an artificially
    NARROW null distribution (an invalidly liberal test, more likely
    to report significance than is warranted).

    Instead, this uses the standard "H0-centered bootstrap test"
    construction (Hall & Wilson 1991; see also Efron & Tibshirani 1993,
    ch.16): both samples are first SHIFTED so they share one common
    (pooled) mean -- satisfying H0 ("no difference in means") exactly,
    by construction, while leaving each sample's own internal shape and
    dependence structure untouched (only a constant is added/
    subtracted). The shifted samples are then resampled using the same
    dependence-respecting procedure as the CI functions above (i.i.d.
    for the already-independent conditioned episodes, moving-block for
    the autocorrelated baseline), producing a null distribution of the
    mean difference that reflects what would typically be observed if
    the two populations truly had equal means. The empirical two-sided
    p-value is the fraction of that null distribution at least as
    extreme (by absolute value) as the ACTUAL observed difference, with
    the same +1/+1 correction V1's permutation test uses (never exactly
    0.0, however extreme the observed difference).

    Returns (observed_difference, p_value_two_sided).
    """
    cond_arr = np.asarray(conditioned, dtype=float)
    base_arr = np.asarray(baseline_series, dtype=float)
    observed_diff = float(cond_arr.mean() - base_arr.mean())

    pooled_mean = float(np.concatenate([cond_arr, base_arr]).mean())
    cond_shifted = cond_arr - cond_arr.mean() + pooled_mean
    base_shifted = base_arr - base_arr.mean() + pooled_mean

    cond_null_means = _iid_bootstrap_means(cond_shifted, rng=rng, n_resamples=n_resamples)
    base_null_means = moving_block_bootstrap_mean(base_shifted, block_length=block_length, rng=rng, n_resamples=n_resamples)
    null_diffs = cond_null_means - base_null_means

    at_least_as_extreme = int(np.sum(np.abs(null_diffs) >= abs(observed_diff)))
    p_value = (at_least_as_extreme + 1) / (n_resamples + 1)
    return observed_diff, p_value
