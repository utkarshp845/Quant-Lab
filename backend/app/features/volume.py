"""VOLUME features (app/features/): volume, relative_volume,
volume_acceleration.

`volume` is carried straight through from the bar (never None -- it
is not a derived calculation). The other two are documented below.
"""

from app.models.features import VolumeFeatures
from app.models.market_data import HistoricalBar

from app.features.session import NY_TIMEZONE, session_date
from app.features.timeframes import is_contiguous_window, timeframe_minutes

# How many distinct prior sessions relative_volume's baseline averages
# over. Not specified by this feature's contract -- chosen for
# symmetry with the contract's other "20"-bar windows (SMA20,
# realized-volatility's own 20-bar window). A documented v1 default,
# not derived from anything else in this codebase.
RELATIVE_VOLUME_LOOKBACK_SESSIONS = 20


def compute_volume_features(bars: list[HistoricalBar], index: int, timeframe: str) -> VolumeFeatures:
    return VolumeFeatures(
        volume=bars[index].volume,
        relative_volume=relative_volume(bars, index),
        volume_acceleration=volume_acceleration(bars, index, timeframe),
    )


def volume_acceleration(bars: list[HistoricalBar], index: int, timeframe: str) -> float | None:
    """current bar volume / previous bar volume -- "previous" meaning
    literally the immediately preceding bar in time (verified via
    is_contiguous_window over exactly one bar-step, so a missing bar
    directly before this one yields None rather than silently
    comparing against a stale, further-back bar). None when there is
    no previous bar yet, the previous bar is missing, or its volume is
    0 (division by zero)."""
    if not is_contiguous_window(bars, index - 1, index, timeframe_minutes(timeframe)):
        return None
    previous_volume = bars[index - 1].volume
    if previous_volume == 0:
        return None
    return bars[index].volume / previous_volume


def relative_volume(bars: list[HistoricalBar], index: int) -> float | None:
    """current bar volume / the historical average volume of bars at
    the SAME NY-local time-of-day, over up to
    RELATIVE_VOLUME_LOOKBACK_SESSIONS distinct EARLIER sessions --
    "a time-of-day-aware historical baseline" (spec), so a bar at
    9:35am is compared against other 9:35am bars, not against a
    10:45am bar's typically-different volume.

    Walks backward from `index - 1` (never `index` itself, and never a
    later bar -- no look-ahead) collecting every bar whose NY-local
    time-of-day matches bars[index]'s, until either
    RELATIVE_VOLUME_LOOKBACK_SESSIONS distinct sessions have
    contributed a match or the start of the data is reached.

    A daily ("1d") series naturally degrades to a plain rolling
    average here rather than needing a separate code path: every daily
    bar in this app's data shares one canonical nominal time-of-day
    (see e.g. tests/test_research_engine.py's own 1d fixtures, all at
    the same wall-clock time), so "same time-of-day" already means
    "every prior daily bar" for that series -- there is no meaningful
    finer time-of-day distinction on a once-a-day series to match on.

    Returns None when there is not at least one historical match
    (insufficient history, rule 3) or the resulting baseline average
    is 0 (division by zero, rule 6).
    """
    target_time = bars[index].timestamp.astimezone(NY_TIMEZONE).time()

    matched_volumes: list[int] = []
    distinct_sessions_matched = 0
    last_matched_session = None
    for j in range(index - 1, -1, -1):
        candidate = bars[j]
        if candidate.timestamp.astimezone(NY_TIMEZONE).time() != target_time:
            continue
        candidate_session = session_date(candidate.timestamp)
        if candidate_session != last_matched_session:
            distinct_sessions_matched += 1
            last_matched_session = candidate_session
            if distinct_sessions_matched > RELATIVE_VOLUME_LOOKBACK_SESSIONS:
                break
        matched_volumes.append(candidate.volume)

    if not matched_volumes:
        return None

    baseline = sum(matched_volumes) / len(matched_volumes)
    if baseline == 0:
        return None
    return bars[index].volume / baseline
