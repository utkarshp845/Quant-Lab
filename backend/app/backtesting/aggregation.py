"""Aggregate statistics for a completed Backtesting v1 run (app/
backtesting/) -- turns a flat list of BacktestSignal into one
BacktestWindowResults per configured window. Same "lean on the stdlib
`statistics` module, no new dependency" judgment app/research/
aggregation.py already applies, extended here to group by window_bars
first.
"""

import statistics as _statistics

from app.models.backtesting import BacktestResults, BacktestSignal, BacktestWindowOutcome, BacktestWindowResults

# Sample standard deviation (statistics.stdev) is mathematically
# undefined for fewer than two observations -- see
# app/research/aggregation.py::MIN_OBSERVATIONS_FOR_STDEV, the same
# constant, the same reasoning, applied per-window here instead of once
# per experiment.
MIN_OBSERVATIONS_FOR_STDEV = 2


def aggregate_results(signals: list[BacktestSignal], windows: list[int]) -> BacktestResults:
    """Computes BacktestResults from `signals` -- the signals already
    produced by app/backtesting/engine.py::run_backtest(), never a
    database read of its own (this function is pure). Produces exactly
    one BacktestWindowResults per entry in `windows`, in that order,
    even when a window ended up with zero measurable outcomes across
    every signal (a legitimate result, not an error -- see
    BacktestWindowResults' own docstring).
    """
    return BacktestResults(windows=[_aggregate_window(signals, window_bars) for window_bars in windows])


def _aggregate_window(signals: list[BacktestSignal], window_bars: int) -> BacktestWindowResults:
    outcomes: list[BacktestWindowOutcome] = [
        outcome for signal in signals for outcome in signal.outcomes if outcome.window_bars == window_bars
    ]
    total = len(outcomes)

    if total == 0:
        return BacktestWindowResults(
            window_bars=window_bars,
            signal_count=0,
            win_count=0,
            win_rate=None,
            mean_return=None,
            median_return=None,
            std_dev_return=None,
            best_return=None,
            worst_return=None,
            mean_mfe=None,
            mean_mae=None,
        )

    returns = [outcome.forward_return for outcome in outcomes]
    mfes = [outcome.mfe for outcome in outcomes]
    maes = [outcome.mae for outcome in outcomes]
    wins = sum(1 for r in returns if r > 0)

    return BacktestWindowResults(
        window_bars=window_bars,
        signal_count=total,
        win_count=wins,
        win_rate=wins / total,
        mean_return=_statistics.mean(returns),
        median_return=_statistics.median(returns),
        std_dev_return=_statistics.stdev(returns) if total >= MIN_OBSERVATIONS_FOR_STDEV else None,
        best_return=max(returns),
        worst_return=min(returns),
        mean_mfe=_statistics.mean(mfes),
        mean_mae=_statistics.mean(maes),
    )
