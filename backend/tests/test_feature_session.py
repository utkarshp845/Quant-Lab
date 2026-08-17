"""Tests for app/features/session.py -- NY-local session-day grouping,
annualization constants, and the "N distinct sessions back" lookback
walk. No database, no HTTP -- synthetic HistoricalBar lists only.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.market_data import HistoricalBar
from app.features.session import (
    TRADING_DAYS_PER_YEAR,
    TRADING_MINUTES_PER_DAY,
    current_session_start_index,
    periods_per_year,
    session_date,
    session_lookback_start_index,
)


def _bar(ts, close=100.0) -> HistoricalBar:
    return HistoricalBar(
        symbol="TSLA", timestamp=ts, open=close, high=close, low=close, close=close, volume=1_000,
        provider="csv", timeframe="5m",
    )


class TestSessionDate:
    def test_utc_midday_is_the_same_ny_calendar_date_in_winter(self):
        # 14:30 UTC in January is 09:30 EST -- same calendar date.
        ts = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        assert session_date(ts) == date(2026, 1, 5)

    def test_early_utc_morning_falls_on_the_previous_ny_calendar_date(self):
        # 04:00 UTC in January is 23:00 EST the PREVIOUS day -- this is
        # the whole point of doing the conversion rather than just
        # reading .date() off the UTC timestamp directly.
        ts = datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc)
        assert session_date(ts) == date(2026, 1, 4)

    def test_naive_and_aware_utc_timestamps_at_the_same_instant_agree(self):
        aware = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        assert session_date(aware) == date(2026, 1, 5)


class TestPeriodsPerYear:
    def test_daily_timeframe_is_just_trading_days_per_year(self):
        assert periods_per_year("1d") == float(TRADING_DAYS_PER_YEAR)

    def test_intraday_timeframes_scale_by_minutes(self):
        assert periods_per_year("5m") == pytest.approx((TRADING_DAYS_PER_YEAR * TRADING_MINUTES_PER_DAY) / 5)
        assert periods_per_year("1h") == pytest.approx((TRADING_DAYS_PER_YEAR * TRADING_MINUTES_PER_DAY) / 60)

    def test_unknown_timeframe_raises(self):
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            periods_per_year("3m")


class TestCurrentSessionStartIndex:
    def test_returns_the_first_bar_of_the_same_ny_calendar_date(self):
        day1 = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        day2 = datetime(2026, 1, 6, 14, 30, tzinfo=timezone.utc)
        bars = [
            _bar(day1),
            _bar(day1 + timedelta(minutes=5)),
            _bar(day1 + timedelta(minutes=10)),
            _bar(day2),
            _bar(day2 + timedelta(minutes=5)),
        ]

        assert current_session_start_index(bars, 2) == 0  # still day 1
        assert current_session_start_index(bars, 4) == 3  # day 2 starts fresh at index 3

    def test_a_single_bar_session_returns_its_own_index(self):
        bars = [_bar(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc))]
        assert current_session_start_index(bars, 0) == 0

    def test_never_reaches_into_a_later_session(self):
        """The start of TODAY's session must never accidentally include
        tomorrow's bars -- trivially true here since we only look
        backward, but asserted explicitly."""
        day1 = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        day2 = datetime(2026, 1, 6, 14, 30, tzinfo=timezone.utc)
        bars = [_bar(day1), _bar(day2)]

        assert current_session_start_index(bars, 0) == 0  # not pulled forward into day2


class TestSessionLookbackStartIndex:
    def test_finds_the_start_of_the_nth_distinct_session_back(self):
        base = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        # 3 sessions, 2 bars each: indices 0-1 = session A, 2-3 = session B, 4-5 = session C.
        bars = [
            _bar(base),
            _bar(base + timedelta(minutes=5)),
            _bar(base + timedelta(days=1)),
            _bar(base + timedelta(days=1, minutes=5)),
            _bar(base + timedelta(days=2)),
            _bar(base + timedelta(days=2, minutes=5)),
        ]

        # From end_index=5 (session C), 1 session back is just session C -> start index 4.
        assert session_lookback_start_index(bars, 5, 1) == 4
        # 2 sessions back (C, B) -> start index 2.
        assert session_lookback_start_index(bars, 5, 2) == 2
        # 3 sessions back (C, B, A) -> start index 0.
        assert session_lookback_start_index(bars, 5, 3) == 0

    def test_none_when_fewer_than_n_distinct_sessions_exist(self):
        base = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        bars = [_bar(base), _bar(base + timedelta(minutes=5))]  # only 1 session total

        assert session_lookback_start_index(bars, 1, 2) is None

    def test_none_for_an_out_of_range_end_index(self):
        bars = [_bar(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc))]
        assert session_lookback_start_index(bars, 5, 1) is None
        assert session_lookback_start_index(bars, -1, 1) is None

    def test_none_for_a_non_positive_session_count(self):
        bars = [_bar(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc))]
        assert session_lookback_start_index(bars, 0, 0) is None
