"""Feature warm-up context for OOS Evaluation v1 (app/oos_evaluation/):
how many DEVELOPMENT bars, and over which calendar range, are read
purely so the Feature Engine has legitimate trailing history at the
FIRST holdout bar -- never to create eligible signal/entry/outcome
observations from development data (app/oos_evaluation/engine.py keeps
warm-up bars and holdout bars in entirely separate lists for exactly
that reason; see its own module docstring for the full pipeline).

Pure, no I/O -- mirrors every other small pure-computation module in
this app (app/research/metrics.py, app/features/timeframes.py).
"""

from datetime import datetime, timedelta

from app.features.price import RETURN_HORIZONS_MINUTES
from app.features.price_position import SMA50_WINDOW_BARS
from app.features.timeframes import bars_for_window, timeframe_minutes
from app.features.volatility import ATR_WINDOW_BARS, REALIZED_VOLATILITY_WINDOW_BARS

# How much calendar-time slack to read beyond the theoretical minimum
# bar count, to absorb non-trading gaps (weekends, holidays, session
# boundaries) this app's real intraday data already has -- a gap simply
# yields fewer CONTIGUOUS bars to the Feature Engine (app/features/
# timeframes.py::is_contiguous_window returns False across one, so the
# affected feature is None, never computed across the gap), so
# overshooting this buffer costs a slightly larger read, never a
# correctness problem. 3x is a documented, deliberately generous
# constant -- not tuned per-symbol, not reaching into
# app/features/session.py's own trading-calendar knowledge (that stays
# inside the Feature Engine, this module does not duplicate it).
_WARMUP_CALENDAR_BUFFER_MULTIPLIER = 3


def feature_warmup_bar_count(timeframe: str) -> int:
    """The largest trailing-window bar count any v1 feature -- OTHER
    THAN volatility_ratio/volatility_percentile, see below -- needs to
    be non-None at a given bar: ATR (app/features/volatility.py::
    ATR_WINDOW_BARS=14, +1 for the prior bar's own close that a True
    Range value also needs = 15), realized volatility
    (REALIZED_VOLATILITY_WINDOW_BARS=20, +1 for the same prior-close
    reason = 21), SMA50 (app/features/price_position.py::
    SMA50_WINDOW_BARS=50), and the largest configured return horizon
    (app/features/price.py::RETURN_HORIZONS_MINUTES, max 60 minutes,
    converted to this timeframe's own bar count via app/features/
    timeframes.py::bars_for_window() -- the SAME function the Feature
    Engine itself uses for the identical conversion).

    These are all PUBLISHED module constants/functions from the
    Feature Engine itself, imported here, never re-derived or
    hardcoded independently -- a future change to any of them is
    automatically reflected in this warm-up count with no second copy
    to keep in sync (requirement: "do not duplicate feature
    calculations" -- extended here to "do not duplicate the Feature
    Engine's own window-size FACTS" either).

    DELIBERATELY EXCLUDED: volatility_ratio/volatility_percentile
    (app/features/volatility.py::VOLATILITY_HISTORY_SESSIONS = 252
    TRADING SESSIONS -- roughly a full year). Warming up a full trading
    year of development bars for every OOS evaluation would violate
    this feature's own "use the MINIMUM required development context"
    instruction far more than it would help: most OOS partitions'
    development windows are nowhere near a year long in the first
    place, and even when they are, reading that much data just to
    populate two of twenty-odd features is not "minimum". Those two
    features may legitimately remain None for a while into the holdout
    period as a result -- the exact same "insufficient history yet"
    behavior the Feature Engine already exhibits at the START of ANY
    fresh dataset, unmodified, just naturally visible again here at the
    holdout boundary. See app/oos_evaluation/engine.py's own docstring,
    "WARM-UP HANDLING" section, for this same note restated at the
    orchestration layer.
    """
    horizon_bars = bars_for_window(max(RETURN_HORIZONS_MINUTES), timeframe) or 0
    return max(ATR_WINDOW_BARS + 1, REALIZED_VOLATILITY_WINDOW_BARS + 1, SMA50_WINDOW_BARS, horizon_bars)


def warmup_range(
    *, holdout_start: datetime, development_start: datetime, development_end: datetime, timeframe: str
) -> tuple[datetime, datetime] | None:
    """The [start, end] calendar range of DEVELOPMENT bars to read for
    warm-up context, or None if there is no usable warm-up room at all
    (see below).

    `end` is `holdout_start` minus one microsecond, clamped DOWN to
    never exceed `development_end` -- OOS Evaluation V1 Audit finding
    (2026-08-18): a partition's own validator (app/models/
    oos_partition.py) only requires `development_end < holdout_start`,
    not that the two be adjacent -- a partition MAY legitimately declare
    a gap between them (e.g. to wall off a known data-quality window).
    Before this fix, `end` was unconditionally `holdout_start - 1
    microsecond`, which could read bars from that UNDECLARED gap
    instead of the partition's own declared development window --
    confirmed by construction: a partition with development_end far
    before holdout_start, real bars seeded only in the gap and only 2
    bars in the declared development window, still produced a
    fully-warmed-up SMA50 feature at the first holdout bar (proving the
    gap bars, not the ~2 real development bars, were what warmed it
    up). Not a HOLDOUT-leakage issue (gap bars are still strictly
    before holdout_start, so no future information was ever used) --
    but a genuine violation of "use the minimum required DEVELOPMENT
    context" (this feature's own instruction): an undeclared gap is not
    development data. In the common case (development_end already set
    to `holdout_start - 1 microsecond`, i.e. no gap at all -- every
    example partition in this codebase's own tests/README does this),
    `min(holdout_start - 1us, development_end)` equals what `end`
    already was, so this is a no-op there.

    `start` is `feature_warmup_bar_count()` bars at this timeframe's own
    bar length, times `_WARMUP_CALENDAR_BUFFER_MULTIPLIER` for gap
    tolerance (see that constant's own comment), counting backward from
    `end` (not `holdout_start` -- see above) -- clamped to never precede
    `development_start` (nothing before that point is even this
    partition's OWN development data, let alone legitimate warm-up
    context reserved for it).

    Returns None when the resulting range would be empty or inverted
    (`start` lands at or after `end`) -- either `development_start` is
    at or effectively at `holdout_start` (no room for warm-up at all),
    or `development_end` itself leaves no usable room once clamped. The
    caller then simply skips the warm-up read and computes features
    from holdout bars alone, exactly as it would for any OTHER dataset
    with insufficient leading history: the Feature Engine returns None
    for the affected features, per its own existing, UNMODIFIED rule --
    never a crash, never a fabricated value.
    """
    warmup_bar_count = feature_warmup_bar_count(timeframe)
    tf_minutes = timeframe_minutes(timeframe)
    buffer_minutes = tf_minutes * warmup_bar_count * _WARMUP_CALENDAR_BUFFER_MULTIPLIER

    end = min(holdout_start - timedelta(microseconds=1), development_end)
    start = max(development_start, end - timedelta(minutes=buffer_minutes))
    if start >= end:
        return None
    return start, end
