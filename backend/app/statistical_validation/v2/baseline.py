"""Method A (non-overlapping windows) for Statistical Validation V2 --
see app/statistical_validation/v2/__init__.py for how this fits
alongside Method B (moving block bootstrap, app/statistical_validation/
v2/resampling.py).

Subsamples V1's own unconditional baseline (app.statistical_validation.
baseline.compute_unconditional_baseline() -- imported UNCHANGED, never
reimplemented) down to a subset whose `window_bars`-bar forward-return
windows never overlap. Once non-overlapping, these observations are
free of the MECHANICAL dependence V1's full baseline had (adjacent
bars' 5-bar windows sharing 4 of their 5 bars) -- an approximation
(residual serial correlation from the underlying price process beyond
the window-overlap mechanism may still exist), but a standard, simple,
and directly interpretable one: "one observation per non-overlapping
block of time" is the same logic behind V1's own episode definition,
applied here to the baseline side instead of the conditioned side.

Because this subsample is (approximately) independent, it needs NO new
resampling machinery: app.statistical_validation.resampling's own
bootstrap_mean_difference_ci() / bootstrap_win_rate_ci() /
permutation_test_mean_difference() / cohens_d() (all V1, all
unmodified) apply to it directly -- see app/statistical_validation/v2/
engine.py.
"""

from datetime import timedelta

from app.models.backtesting import BacktestSignal


def non_overlapping_baseline(baseline_signals: list[BacktestSignal], *, window_bars: int, bar_interval: timedelta) -> list[BacktestSignal]:
    """Greedily selects a subset of `baseline_signals` (the FULL,
    every-eligible-bar baseline from V1's compute_unconditional_baseline(),
    unfiltered) whose `window_bars` outcome windows do not overlap.

    Only considers signals that actually HAVE a `window_bars` outcome
    (a signal near the end of the dataset may lack one -- see
    app/backtesting/engine.py's own "insufficient future data" rule,
    unchanged). Walks the remaining signals in chronological
    (entry_timestamp) order; the first eligible signal starts the first
    window; the next SELECTED signal is the first subsequent one whose
    entry_timestamp is at least `window_bars` bar-intervals after the
    previously selected signal's entry_timestamp -- this produces
    genuinely non-overlapping windows even if `baseline_signals` itself
    has a gap (it does not assume perfectly dense, gap-free 1-bar
    spacing; it verifies it via timestamp arithmetic).
    """
    eligible = [s for s in baseline_signals if any(o.window_bars == window_bars for o in s.outcomes)]
    eligible.sort(key=lambda s: s.entry_timestamp)

    selected: list[BacktestSignal] = []
    next_allowed_entry = None
    for signal in eligible:
        if next_allowed_entry is None or signal.entry_timestamp >= next_allowed_entry:
            selected.append(signal)
            next_allowed_entry = signal.entry_timestamp + window_bars * bar_interval
    return selected
