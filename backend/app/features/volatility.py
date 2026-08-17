"""VOLATILITY features (app/features/): realized_volatility, atr,
volatility_ratio, volatility_percentile.

realized_volatility and atr are self-contained per bar (each needs
only a small trailing window of the bars list itself). volatility_ratio
and volatility_percentile are different in kind: both compare "today's"
realized_volatility against a HISTORY of realized_volatility values at
many earlier bars, so computing either means recomputing
realized_volatility at every bar in that history too -- see
volatility_history() below.

v1 does not cache/memoize that recomputation across feature rows (each
call to volatility_ratio_at()/volatility_percentile_at() walks its own
up-to-252-distinct-session history from scratch). That keeps every
function here simple, independently testable, and correct by
construction (no shared mutable state to get out of sync) at the cost
of doing repeated work on very large intraday datasets -- an accepted
v1 performance tradeoff, not a correctness gap; see this feature's
final assumptions/gaps list.
"""

import math
import statistics as _statistics

from app.models.features import VolatilityFeatures
from app.models.market_data import HistoricalBar

from app.features.session import TRADING_DAYS_PER_YEAR, periods_per_year, session_lookback_start_index
from app.features.timeframes import is_contiguous_window, timeframe_minutes

# "rolling 20-bar standard deviation of log returns" (spec) -- 20 log
# returns need 21 consecutive closes.
REALIZED_VOLATILITY_WINDOW_BARS = 20

# "14-bar ATR" (spec) -- 14 true-range values need 15 consecutive bars
# (each TR also needs the prior bar's close).
ATR_WINDOW_BARS = 14

# "a 252-trading-day rolling history" (spec), shared by both
# volatility_ratio and volatility_percentile -- counted as distinct
# NY-local sessions (app/features/session.py), not a fixed bar-count
# multiplier.
VOLATILITY_HISTORY_SESSIONS = TRADING_DAYS_PER_YEAR


def compute_volatility_features(bars: list[HistoricalBar], index: int, timeframe: str) -> VolatilityFeatures:
    current_volatility = realized_volatility_at(bars, index, timeframe)
    return VolatilityFeatures(
        realized_volatility=current_volatility,
        atr=atr_at(bars, index, timeframe),
        volatility_ratio=volatility_ratio_at(bars, index, timeframe, current_volatility=current_volatility),
        volatility_percentile=volatility_percentile_at(bars, index, timeframe, current_volatility=current_volatility),
    )


def realized_volatility_at(bars: list[HistoricalBar], index: int, timeframe: str) -> float | None:
    """The trailing 20-bar (21-close) sample standard deviation of log
    returns ending at `index`, annualized via
    app/features/session.py::periods_per_year(timeframe).

    None when there are not REALIZED_VOLATILITY_WINDOW_BARS + 1
    contiguous closes ending at `index` (insufficient history or a
    missing bar somewhere in the window), or any close in that window
    is <= 0 (a log return is undefined there).
    """
    start_index = index - REALIZED_VOLATILITY_WINDOW_BARS
    if not is_contiguous_window(bars, start_index, index, timeframe_minutes(timeframe)):
        return None

    closes = [bars[i].close for i in range(start_index, index + 1)]
    if any(c <= 0 for c in closes):
        return None

    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    per_bar_stdev = _statistics.stdev(log_returns)  # sample stdev; len(log_returns) == 20 >= 2 always here
    return per_bar_stdev * math.sqrt(periods_per_year(timeframe))


def atr_at(bars: list[HistoricalBar], index: int, timeframe: str) -> float | None:
    """The classic 14-bar Average True Range: a simple (unweighted)
    rolling mean of True Range over the trailing 14 bars -- v1 uses a
    plain mean rather than Wilder's exponential smoothing, a documented
    simplification (the spec asks for "14-bar ATR" without specifying
    the smoothing method).

    True Range at bar j = max(high[j] - low[j], |high[j] - close[j-1]|,
    |low[j] - close[j-1]|) -- each TR value needs the PRIOR bar's
    close too, so 14 TR values need 15 contiguous bars. None when that
    window is not fully available and contiguous.
    """
    start_index = index - ATR_WINDOW_BARS
    if not is_contiguous_window(bars, start_index, index, timeframe_minutes(timeframe)):
        return None

    true_ranges = []
    for j in range(start_index + 1, index + 1):
        bar, previous_close = bars[j], bars[j - 1].close
        true_range = max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close))
        true_ranges.append(true_range)
    return sum(true_ranges) / len(true_ranges)


def volatility_history(bars: list[HistoricalBar], index: int, timeframe: str) -> list[float]:
    """realized_volatility_at(), recomputed at every bar in the last
    VOLATILITY_HISTORY_SESSIONS distinct sessions STRICTLY BEFORE
    `index` (i.e. over bars[start..index-1], never including `index`
    itself -- "today" is compared against "history", not against
    itself). None values inside that span (bars too close to the start
    of the whole series to have their own 20-bar window yet) are
    filtered out. Returns [] when there are fewer than
    VOLATILITY_HISTORY_SESSIONS distinct sessions of history at all
    (see session_lookback_start_index()) or the window happens to
    contain no computable realized_volatility values.
    """
    if index <= 0:
        return []
    start_index = session_lookback_start_index(bars, index - 1, VOLATILITY_HISTORY_SESSIONS)
    if start_index is None:
        return []
    values = (realized_volatility_at(bars, j, timeframe) for j in range(start_index, index))
    return [v for v in values if v is not None]


def volatility_ratio_at(
    bars: list[HistoricalBar], index: int, timeframe: str, *, current_volatility: float | None = None
) -> float | None:
    """current realized volatility / the mean of volatility_history()
    -- None if the current value is itself undefined, there is no
    usable history, or the historical mean is 0 (division by zero)."""
    if current_volatility is None:
        current_volatility = realized_volatility_at(bars, index, timeframe)
    if current_volatility is None:
        return None

    history = volatility_history(bars, index, timeframe)
    if not history:
        return None
    historical_average = sum(history) / len(history)
    if historical_average == 0:
        return None
    return current_volatility / historical_average


def volatility_percentile_at(
    bars: list[HistoricalBar], index: int, timeframe: str, *, current_volatility: float | None = None
) -> float | None:
    """Percentile rank (0..1) of the current realized volatility
    against volatility_history(): the fraction of historical values
    that are <= the current value. None under the same conditions as
    volatility_ratio_at()."""
    if current_volatility is None:
        current_volatility = realized_volatility_at(bars, index, timeframe)
    if current_volatility is None:
        return None

    history = volatility_history(bars, index, timeframe)
    if not history:
        return None
    at_or_below = sum(1 for v in history if v <= current_volatility)
    return at_or_below / len(history)
