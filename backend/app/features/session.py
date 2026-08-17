"""NY-local trading-session grouping and annualization constants for
Feature Engine v1 (app/features/).

Nothing in this codebase had a session/trading-calendar concept before
this feature (confirmed: no `pytz`/`zoneinfo`/market-hours code
anywhere else in app/) -- everything here is new, and several of its
definitions are documented v1 simplifications rather than a precise
exchange calendar, since building one is out of this feature's scope:

  - **"Session" = NY-local calendar day** (the `America/New_York`
    civil date of a bar's UTC timestamp), not strict 9:30-16:00 ET
    trading hours. This dataset carries no extended-hours flag to
    filter on, so a session is simply "every bar sharing the same
    NY-local date" -- whatever hours the provider actually returned
    for that date. VWAP and intraday-range-position (app/features/
    price_position.py) are computed against this definition.
  - **Annualization** uses the standard 252-trading-days/year,
    390-minutes/trading-day (9:30-16:00 ET) convention -- the
    industry-standard assumption, not derived from this app's actual
    (currently nonexistent) trading calendar.
  - **"N distinct prior sessions"** (used by realized-volatility
    history and the relative-volume time-of-day baseline) is counted
    by walking the bar series backward and counting actual distinct
    NY-local dates present in the data -- exact with respect to
    whatever gaps/holidays the stored data already reflects, not a
    fixed bar-count multiplier that would assume every session
    contributes the same number of bars.

Uses the stdlib `zoneinfo` (Python 3.9+) rather than adding a `pytz`
dependency -- consistent with this app's existing "lean on the
standard library" judgment (see app/calculations/stats.py's own
docstring re: `math.erf` instead of scipy).
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.models.market_data import HistoricalBar

NY_TIMEZONE = ZoneInfo("America/New_York")

# Standard US equity-market annualization convention -- see the module
# docstring. Used only to compute periods_per_year() below; not an
# assertion that every symbol this app handles actually trades on this
# calendar (see app/features/volatility.py's own docstring for how a
# non-equity symbol like MCL is affected: the SAME convention is
# applied uniformly in v1, a documented simplification, not a
# per-symbol trading-calendar lookup).
TRADING_DAYS_PER_YEAR = 252
TRADING_MINUTES_PER_DAY = 390  # 9:30-16:00 ET


def session_date(timestamp: datetime) -> date:
    """The NY-local calendar date a bar's (UTC or any tz-aware)
    timestamp falls on -- this app's definition of "which session"."""
    return timestamp.astimezone(NY_TIMEZONE).date()


def periods_per_year(timeframe: str) -> float:
    """How many bars of `timeframe` fit in one trading year, under the
    252-day/390-minute convention above -- the scaling factor realized
    volatility is annualized by (see app/features/volatility.py):
    `annualized = per_bar_stdev * sqrt(periods_per_year(timeframe))`.
    A daily bar IS one trading day (252/year, not scaled through
    minutes); every other timeframe scales via minutes-per-bar.
    """
    from app.features.timeframes import timeframe_minutes  # local import: avoids a module-load cycle with timeframes.py

    if timeframe == "1d":
        return float(TRADING_DAYS_PER_YEAR)
    return (TRADING_DAYS_PER_YEAR * TRADING_MINUTES_PER_DAY) / timeframe_minutes(timeframe)


def current_session_start_index(bars: list[HistoricalBar], index: int) -> int:
    """The earliest index `j <= index` such that bars[j..index] all
    share bars[index]'s own session -- the start of "today" as of this
    bar. Used by VWAP/session-high/session-low (app/features/
    price_position.py): both accumulate only over the current
    session's own bars, up to and including `index` -- never a bar
    from a later session (this bar's own session cannot end in the
    future relative to itself) and never a bar from an earlier
    session (that would silently blend two different sessions'
    ranges/volumes together).
    """
    target_session = session_date(bars[index].timestamp)
    start = index
    i = index - 1
    while i >= 0 and session_date(bars[i].timestamp) == target_session:
        start = i
        i -= 1
    return start


def session_lookback_start_index(bars: list[HistoricalBar], end_index: int, session_count: int) -> int | None:
    """Walking backward from `end_index`, the earliest index whose
    bars[start_index..end_index] span exactly `session_count` distinct
    NY-local sessions -- e.g. session_lookback_start_index(bars, i - 1,
    252) is "the last 252 distinct trading sessions of history before
    bar i", used by realized-volatility history (app/features/
    volatility.py) for volatility_ratio/volatility_percentile.

    Returns None -- never a shorter, partial window -- when fewer than
    `session_count` distinct sessions exist in the data up to
    `end_index`: "252-trading-day rolling history where sufficient
    data exists" (spec) means exactly this -- insufficient history is
    an explicit null, not a smaller history silently substituted in.
    """
    if end_index < 0 or end_index >= len(bars) or session_count <= 0:
        return None

    distinct_sessions_seen = 0
    last_session: date | None = None
    start_index: int | None = None
    for i in range(end_index, -1, -1):
        this_session = session_date(bars[i].timestamp)
        if this_session != last_session:
            distinct_sessions_seen += 1
            last_session = this_session
            if distinct_sessions_seen > session_count:
                # This bar belongs to the (session_count + 1)-th
                # session -- one too many. Stop WITHOUT extending
                # start_index into it; the previous iteration already
                # holds the earliest bar of the session_count-th
                # session.
                break
        start_index = i

    if distinct_sessions_seen < session_count:
        return None  # ran out of bars before reaching session_count distinct sessions
    return start_index
