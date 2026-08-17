"""Metric calculations for Research v1 (app/research/).

Two metric kinds, both computed as plain returns on the SAME bar series
an experiment is evaluating -- no separate indicator dataset:

  - CONDITION metrics: "{N}m_return" (e.g. "30m_return") -- the return
    from N minutes before an observation to the observation itself,
    using only bars up to and including that observation. See
    trailing_return()'s docstring for why that is what makes condition
    evaluation free of look-ahead by construction.
  - OUTCOME metrics: "forward_return" -- the return from an observation
    forward `horizon_minutes`, using bars AFTER the observation.
    Deliberately look-ahead: measuring what happens next is the whole
    point of an outcome.

v1 scope note: "{N}m_return" is evaluated as a literal trailing
bar-count window over the queried bar series -- it is NOT anchored to
a trading session's open (so a window can span a day boundary if the
dataset has consecutive bars there). Session-boundary awareness would
mean the condition model growing beyond "metric/operator/threshold",
which the spec explicitly rules out for v1 ("do not build a complex
expression language yet") -- see README's Research v1 limitations list.
"""

import re

from app.models.market_data import HistoricalBar

# The normalized timeframe vocabulary this app already uses end to end
# (see app/api/historical_data.py::ALLOWED_TIMEFRAMES), expressed in
# minutes so a "{N}m" window can be converted to a bar count.
_TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 1440}

_TRAILING_RETURN_RE = re.compile(r"^(\d+)m_return$")

FORWARD_RETURN_METRIC = "forward_return"


def timeframe_minutes(timeframe: str) -> int:
    """How many minutes one bar of `timeframe` spans. Raises ValueError
    for anything outside this app's normalized timeframe vocabulary --
    the same set every other route validates a `timeframe` query
    parameter against."""
    if timeframe not in _TIMEFRAME_MINUTES:
        raise ValueError(f"Unsupported timeframe {timeframe!r}. Allowed: {sorted(_TIMEFRAME_MINUTES)}")
    return _TIMEFRAME_MINUTES[timeframe]


def parse_trailing_return_metric(metric: str) -> int:
    """Returns N (minutes) for a "{N}m_return" condition metric name,
    raising ValueError for anything else -- the only condition metric
    v1 supports (see Condition's docstring in app/models/research.py)."""
    match = _TRAILING_RETURN_RE.match(metric)
    if not match:
        raise ValueError(
            f"Unsupported condition metric {metric!r}. Expected the form '<minutes>m_return', e.g. '30m_return'."
        )
    return int(match.group(1))


def bars_for_window(minutes: int, timeframe: str) -> int:
    """How many bars of `timeframe` make up a `minutes`-long window --
    e.g. 30 minutes of 5m bars is 6 bars. Raises ValueError if `minutes`
    is not a whole multiple of the timeframe's own bar length: a window
    that does not land on a real bar boundary has no honest answer
    (silently rounding would quietly change what the metric means), so
    this is a validation failure the caller surfaces at experiment-
    creation time (see app/api/research.py), not a runtime guess.
    """
    tf_minutes = timeframe_minutes(timeframe)
    if minutes <= 0:
        raise ValueError(f"A window must be a positive number of minutes, got {minutes}.")
    if minutes % tf_minutes != 0:
        raise ValueError(
            f"{minutes}m window is not a whole number of {timeframe} bars ({minutes} is not a multiple of {tf_minutes})."
        )
    return minutes // tf_minutes


def trailing_return(bars: list[HistoricalBar], index: int, window_bars: int) -> float | None:
    """The condition metric's value AT bars[index]: the return from
    window_bars bars earlier to bars[index] itself -- using ONLY
    bars[index - window_bars] through bars[index], never anything past
    `index`. That is what makes it safe to call at every observation in
    order without look-ahead: advancing `index` forward one bar at a
    time never lets a later bar influence an earlier decision.

    Returns None (never 0.0, never an exception) when there are not
    enough preceding bars to fill the window -- e.g. the first five
    bars of a 30-minute/5-minute-bar series have no six-bars-back
    neighbor yet. None means "not an eligible observation", not "the
    return was zero".
    """
    start_index = index - window_bars
    if start_index < 0:
        return None
    base_price = bars[start_index].close
    if base_price == 0:
        return None
    current_price = bars[index].close
    return (current_price - base_price) / base_price


def forward_return(bars: list[HistoricalBar], index: int, window_bars: int) -> tuple[float, HistoricalBar] | None:
    """The outcome metric's value for a signal AT bars[index]: the
    return from bars[index] forward window_bars bars, i.e.
    bars[index + window_bars]. Returns (outcome_value, outcome_bar), or
    None when that forward bar does not exist in the queried dataset --
    the signal is too close to the end of the requested date range to
    measure its outcome within this dataset (see the Data integrity
    requirements section of the spec this was built against: only the
    requested date range's data is ever used, never a follow-up call
    for "just a bit more" data beyond it).
    """
    target_index = index + window_bars
    if target_index >= len(bars):
        return None
    signal_price = bars[index].close
    if signal_price == 0:
        return None
    outcome_bar = bars[target_index]
    value = (outcome_bar.close - signal_price) / signal_price
    return value, outcome_bar
