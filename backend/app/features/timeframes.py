"""Timeframe <-> minutes <-> bar-count conversion, and the timestamp-
contiguity check every trailing-window feature in this package relies
on (app/features/).

This intentionally duplicates, rather than imports, app/research/
metrics.py's own `_TIMEFRAME_MINUTES` mapping and bar-count conversion:
that module already established the precedent in this codebase of each
layer keeping its own small, purpose-specific copy of the timeframe
vocabulary (see also app/api/historical_data.py's ALLOWED_TIMEFRAMES
and its provider-translation dicts) rather than a shared central
module -- and per this feature's own rule 10 ("do not change
production behavior outside the feature layer"), app/research/ is left
untouched. If this mapping needs to change, it is changed here, not by
reaching into app/research/.
"""

from datetime import datetime, timedelta

from app.models.market_data import HistoricalBar

# The normalized timeframe vocabulary this app already uses end to end
# (see app/api/historical_data.py::ALLOWED_TIMEFRAMES), expressed in
# minutes so an "{N} minute" window can be converted to a bar count.
_TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 1440}


def timeframe_minutes(timeframe: str) -> int:
    """How many minutes one bar of `timeframe` spans. Raises ValueError
    for anything outside this app's normalized timeframe vocabulary."""
    if timeframe not in _TIMEFRAME_MINUTES:
        raise ValueError(f"Unsupported timeframe {timeframe!r}. Allowed: {sorted(_TIMEFRAME_MINUTES)}")
    return _TIMEFRAME_MINUTES[timeframe]


def bars_for_window(minutes: int, timeframe: str) -> int | None:
    """How many bars of `timeframe` make up a `minutes`-long window --
    e.g. 30 minutes of 5m bars is 6 bars. Returns None (never raises,
    never rounds) when `minutes` is not a whole multiple of the
    timeframe's own bar length -- e.g. a 5-minute window has no whole-
    bar answer on a 1-hour timeframe. A feature whose required window
    does not align with the configured timeframe is undefined for
    every observation in that run, not an error that aborts the whole
    engine -- see app/features/price.py for where this return value
    becomes a None feature rather than a crash.
    """
    tf_minutes = _TIMEFRAME_MINUTES.get(timeframe)
    if tf_minutes is None:
        raise ValueError(f"Unsupported timeframe {timeframe!r}. Allowed: {sorted(_TIMEFRAME_MINUTES)}")
    if minutes <= 0:
        raise ValueError(f"A window must be a positive number of minutes, got {minutes}.")
    if minutes % tf_minutes != 0:
        return None
    return minutes // tf_minutes


def is_contiguous_window(bars: list[HistoricalBar], start_index: int, end_index: int, timeframe_minutes_value: int) -> bool:
    """Whether bars[start_index..end_index] (inclusive) form an
    unbroken run at exactly one bar every `timeframe_minutes_value`
    minutes -- this app's "handle missing bars explicitly" rule, in
    code. A trailing-window feature (a return, an SMA, ATR, realized
    volatility) must never silently compute across a gap where a bar
    is absent from the dataset (a halt, a provider outage, an
    unfetched range): this returns False the instant any consecutive
    pair's timestamp delta is not exactly one bar-length, so the
    caller can return None for that observation instead of a value
    computed over stale/wrong-distance data.

    `start_index` may be negative (not enough history yet) -- this
    returns False rather than raising, so a caller can use it as a
    single "is this window usable at all" check covering both
    insufficient-history (rule 3) and missing-bars (rule 4) at once.
    """
    if start_index < 0 or end_index >= len(bars) or start_index > end_index:
        return False
    expected_step = _minutes_delta(timeframe_minutes_value)
    for i in range(start_index, end_index):
        if bars[i + 1].timestamp - bars[i].timestamp != expected_step:
            return False
    return True


def expected_timestamp_before(timestamp: datetime, minutes: int) -> datetime:
    """`timestamp` minus exactly `minutes` -- used to verify a
    candidate "base bar" for a return/lookup genuinely sits at the
    expected point in time, not merely at the expected array position
    (see app/features/price.py, volume.py)."""
    return timestamp - _minutes_delta(minutes)


def _minutes_delta(minutes: int) -> timedelta:
    return timedelta(minutes=minutes)
