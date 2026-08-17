"""PRICE features (app/features/): return_5m/15m/30m/60m.

    return_Xm = close[t] / close[t - Xm] - 1

Each is computed independently at a given bar index, reading only
bars at or before that index -- no look-ahead. `close[t - Xm]` means
literally the bar X minutes before this one, verified by timestamp
(app/features/timeframes.py::is_contiguous_window), not merely "X/tf
bars back by array position" -- a gap anywhere in that window (a
missing bar) yields None, never a value computed across it (rule 4).
"""

from app.models.features import PriceFeatures
from app.models.market_data import HistoricalBar

from app.features.timeframes import bars_for_window, is_contiguous_window, timeframe_minutes

# The four horizons this v1 contract fixes -- see the module docstring
# and app/models/features.py::PriceFeatures. Not user-configurable
# (rule 9: "do not add additional indicators/features beyond this
# contract"). Exported for reuse by app/features/market_context.py,
# which computes SPY/QQQ's own returns at these identical horizons.
RETURN_HORIZONS_MINUTES = (5, 15, 30, 60)


def trailing_return(bars: list[HistoricalBar], index: int, horizon_minutes: int, timeframe: str) -> float | None:
    """The single building block every return_Xm feature (and, via the
    same formula, SPY/QQQ's own returns in app/features/
    market_context.py) is computed from: close[index] / close[index -
    window] - 1, where `window` is `horizon_minutes` converted to a
    bar count for `timeframe`.

    Returns None -- never 0.0, never a raised exception -- when:
      - `horizon_minutes` does not evenly divide `timeframe`'s own bar
        length (bars_for_window() returns None: the window has no
        whole-bar answer on this timeframe),
      - there is not enough trailing history yet (index - window < 0),
      - a bar is missing somewhere in that window (not contiguous at
        exactly one bar per `timeframe`), or
      - the base close is 0 (division by zero).
    """
    tf_minutes = timeframe_minutes(timeframe)
    window_bars = bars_for_window(horizon_minutes, timeframe)
    if window_bars is None:
        return None

    base_index = index - window_bars
    if not is_contiguous_window(bars, base_index, index, tf_minutes):
        return None

    base_close = bars[base_index].close
    if base_close == 0:
        return None
    return bars[index].close / base_close - 1


def compute_price_features(bars: list[HistoricalBar], index: int, timeframe: str) -> PriceFeatures:
    """PriceFeatures for bars[index] -- one trailing_return() call per
    horizon in the fixed contract."""
    values = {m: trailing_return(bars, index, m, timeframe) for m in RETURN_HORIZONS_MINUTES}
    return PriceFeatures(
        return_5m=values[5],
        return_15m=values[15],
        return_30m=values[30],
        return_60m=values[60],
    )
