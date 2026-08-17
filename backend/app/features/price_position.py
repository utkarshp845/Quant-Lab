"""PRICE POSITION features (app/features/): vwap_distance,
ma20_distance, ma50_distance, intraday_range_position.

VWAP and the intraday range are both computed over the CURRENT
session's own bars only, up to and including the bar being featured
(app/features/session.py::current_session_start_index) -- never a bar
from an earlier or later session, and never (by construction, since
`index` is always the last bar considered) a bar from later in time.
SMA20/SMA50 are plain trailing rolling windows over the whole series,
independent of session boundaries, the same convention app/features/
volatility.py's realized-volatility window already uses.
"""

from app.models.features import PricePositionFeatures
from app.models.market_data import HistoricalBar

from app.features.session import current_session_start_index
from app.features.timeframes import is_contiguous_window, timeframe_minutes

SMA20_WINDOW_BARS = 20
SMA50_WINDOW_BARS = 50


def compute_price_position_features(bars: list[HistoricalBar], index: int, timeframe: str) -> PricePositionFeatures:
    return PricePositionFeatures(
        vwap_distance=vwap_distance(bars, index),
        ma20_distance=moving_average_distance(bars, index, timeframe, SMA20_WINDOW_BARS),
        ma50_distance=moving_average_distance(bars, index, timeframe, SMA50_WINDOW_BARS),
        intraday_range_position=intraday_range_position(bars, index),
    )


def moving_average_distance(bars: list[HistoricalBar], index: int, timeframe: str, window_bars: int) -> float | None:
    """(close - SMA(window_bars)) / SMA(window_bars). None when the
    trailing window is not fully available and contiguous, or the
    average itself is 0 (division by zero -- not expected with real
    price data, but guarded per rule 6 regardless)."""
    start_index = index - (window_bars - 1)
    if not is_contiguous_window(bars, start_index, index, timeframe_minutes(timeframe)):
        return None
    closes = [bars[i].close for i in range(start_index, index + 1)]
    average = sum(closes) / len(closes)
    if average == 0:
        return None
    return (bars[index].close - average) / average


def vwap(bars: list[HistoricalBar], index: int) -> float | None:
    """Session-cumulative volume-weighted average price, from the start
    of bars[index]'s own session (app/features/session.py) through
    `index` inclusive. Typical price per bar is (high + low + close) /
    3, the standard VWAP convention. None when cumulative volume over
    the session so far is 0 (division by zero)."""
    session_start = current_session_start_index(bars, index)
    total_dollar_volume = 0.0
    total_volume = 0
    for i in range(session_start, index + 1):
        bar = bars[i]
        typical_price = (bar.high + bar.low + bar.close) / 3
        total_dollar_volume += typical_price * bar.volume
        total_volume += bar.volume
    if total_volume == 0:
        return None
    return total_dollar_volume / total_volume


def vwap_distance(bars: list[HistoricalBar], index: int) -> float | None:
    """(close - VWAP) / VWAP. None when VWAP itself is None or 0."""
    session_vwap = vwap(bars, index)
    if not session_vwap:  # None or 0.0 -- both make the distance undefined
        return None
    return (bars[index].close - session_vwap) / session_vwap


def intraday_range_position(bars: list[HistoricalBar], index: int) -> float | None:
    """(close - session_low) / (session_high - session_low), over the
    same current-session bars VWAP uses. None when the session's high
    equals its low (zero range -- e.g. a single flat bar), which would
    otherwise be a division by zero."""
    session_start = current_session_start_index(bars, index)
    session_bars = bars[session_start : index + 1]
    session_high = max(bar.high for bar in session_bars)
    session_low = min(bar.low for bar in session_bars)
    if session_high == session_low:
        return None
    return (bars[index].close - session_low) / (session_high - session_low)
