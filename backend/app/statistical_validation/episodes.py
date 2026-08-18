"""Episode grouping for Statistical Validation V1
(app/statistical_validation/).

Formalizes the non-overlapping "episode" rule established informally
during Baseline Analysis V1: raw Backtest signals (app/models/
backtesting.py::BacktestSignal, already produced and persisted by the
UNMODIFIED Backtesting v1 engine -- this module reads that output, it
never recomputes or alters a signal) are NOT independent observations
when the underlying research condition stays true across several
consecutive bars -- a condition that holds for 8 bars in a row produces
8 raw signals with heavily overlapping entry prices and outcome
windows, which is one underlying market event, not eight independent
ones. Treating all 124 raw signals as independent draws would overstate
the conditioned sample's true information content; this module is what
fixes that before any statistic gets computed from it (see
app/statistical_validation/engine.py).

THE RULE (fixed in v1, not configurable -- the exact rule used in
Baseline Analysis V1, carried over unchanged so results stay
comparable): signals are sorted by `signal_timestamp`. A signal joins
the immediately preceding episode only if its `signal_timestamp` is
EXACTLY one bar-interval after the immediately preceding signal's
`signal_timestamp` (the condition held true on the very next bar too);
any larger gap between two signals starts a brand-new episode. Each
episode's FIRST signal -- the bar where the condition first became
true, before it was already active -- is that episode's representative
observation for episode-level statistics.
"""

from datetime import timedelta

from app.models.backtesting import BacktestSignal


def group_into_episodes(signals: list[BacktestSignal], *, bar_interval: timedelta) -> list[list[BacktestSignal]]:
    """Groups `signals` into non-overlapping episodes per the rule
    above. Does not mutate `signals` -- returns a new list of lists,
    each inner list already in chronological order. An empty input
    returns an empty list; a single signal is always its own
    one-element episode."""
    sorted_signals = sorted(signals, key=lambda s: s.signal_timestamp)

    episodes: list[list[BacktestSignal]] = []
    for signal in sorted_signals:
        if episodes and (signal.signal_timestamp - episodes[-1][-1].signal_timestamp) == bar_interval:
            episodes[-1].append(signal)
        else:
            episodes.append([signal])
    return episodes


def episode_representatives(episodes: list[list[BacktestSignal]]) -> list[BacktestSignal]:
    """The first (onset) signal of each episode -- the non-overlapping,
    episode-level sample every statistic in
    app/statistical_validation/engine.py treats as its independent-
    observation unit for the conditioned population."""
    return [episode[0] for episode in episodes]
