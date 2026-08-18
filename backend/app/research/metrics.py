"""Metric calculations for Research v1 (app/research/).

v0.1.24 (Feature <-> Research integration): this module used to compute
BOTH condition metrics ("{N}m_return", a trailing return evaluated
directly on bars) and the outcome metric ("forward_return"). Condition
evaluation is now feature-based instead (app/research/conditions.py::
evaluate_feature_conditions(), reading an already-computed FeatureRecord
-- see app/features/vocabulary.py, and "Do NOT recalculate features
inside Research" in this integration's own spec) -- trailing_return()
and parse_trailing_return_metric() were removed rather than left
unused, since app/features/price.py::trailing_return() is now the one
place that calculation happens at all.

What remains is OUTCOME-only: "forward_return" -- the return from an
observation forward `horizon_minutes`, using bars AFTER the
observation. Deliberately look-ahead: measuring what happens next is
the whole point of an outcome. bars_for_window()/timeframe_minutes()
are shared by both the outcome's own window math and
app/api/research.py's request-time validation of `horizon_minutes`
against the experiment's timeframe.
"""

from app.models.market_data import HistoricalBar

# The normalized timeframe vocabulary this app already uses end to end
# (see app/api/historical_data.py::ALLOWED_TIMEFRAMES), expressed in
# minutes so an "{N} minute" window can be converted to a bar count.
_TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 1440}

FORWARD_RETURN_METRIC = "forward_return"


def timeframe_minutes(timeframe: str) -> int:
    """How many minutes one bar of `timeframe` spans. Raises ValueError
    for anything outside this app's normalized timeframe vocabulary --
    the same set every other route validates a `timeframe` query
    parameter against."""
    if timeframe not in _TIMEFRAME_MINUTES:
        raise ValueError(f"Unsupported timeframe {timeframe!r}. Allowed: {sorted(_TIMEFRAME_MINUTES)}")
    return _TIMEFRAME_MINUTES[timeframe]


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
