"""Tests for app/oos_evaluation/warmup.py -- pure, no I/O, no database.

TestWarmupRange::test_a_gap_between_development_end_and_holdout_start_is_never_read
is a regression test for an OOS Evaluation V1 Audit finding
(2026-08-18): warmup_range() used to bound `end` at
`holdout_start - 1 microsecond` unconditionally, which could read bars
from an UNDECLARED gap between a partition's own `development_end` and
`holdout_start` (a partition's validator only requires
`development_end < holdout_start`, not adjacency) -- not a holdout-leak
(gap bars are still strictly pre-holdout), but a violation of "use the
minimum required DEVELOPMENT context". Fixed by also clamping `end` to
`development_end`.
"""

from datetime import datetime, timedelta, timezone

from app.oos_evaluation.warmup import feature_warmup_bar_count, warmup_range


class TestFeatureWarmupBarCount:
    def test_5m_timeframe_is_dominated_by_sma50(self):
        # ATR+1=15, realized_vol+1=21, SMA50=50, 60m/5m=12 bars -> max is 50.
        assert feature_warmup_bar_count("5m") == 50

    def test_1h_timeframe_is_dominated_by_sma50_too(self):
        # 60m horizon on a 1h timeframe is exactly 1 bar -- SMA50 (50) still dominates.
        assert feature_warmup_bar_count("1h") == 50

    def test_1m_timeframe_is_dominated_by_the_60m_return_horizon(self):
        # 60m/1m = 60 bars, larger than SMA50's fixed 50.
        assert feature_warmup_bar_count("1m") == 60


class TestWarmupRange:
    def test_end_is_strictly_before_holdout_start(self):
        holdout_start = datetime(2024, 7, 1, tzinfo=timezone.utc)
        development_end = holdout_start - timedelta(microseconds=1)
        bounds = warmup_range(
            holdout_start=holdout_start, development_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            development_end=development_end, timeframe="5m",
        )
        assert bounds is not None
        start, end = bounds
        assert end < holdout_start
        assert start < end

    def test_start_is_clamped_to_development_start(self):
        # development_start is very close to holdout_start -- the naive
        # buffer would reach further back than development_start allows.
        development_start = datetime(2024, 6, 30, 23, 0, tzinfo=timezone.utc)
        holdout_start = datetime(2024, 7, 1, tzinfo=timezone.utc)
        development_end = holdout_start - timedelta(microseconds=1)
        bounds = warmup_range(
            holdout_start=holdout_start, development_start=development_start, development_end=development_end, timeframe="5m"
        )
        assert bounds is not None
        start, _end = bounds
        assert start == development_start

    def test_no_room_at_all_returns_none(self):
        # development_start is AT holdout_start's own instant (a
        # degenerate, but structurally reachable, edge case) -- no
        # warm-up range can exist.
        holdout_start = datetime(2024, 7, 1, tzinfo=timezone.utc)
        bounds = warmup_range(
            holdout_start=holdout_start, development_start=holdout_start,
            development_end=holdout_start - timedelta(microseconds=1), timeframe="5m",
        )
        assert bounds is None

    def test_generous_buffer_covers_more_than_the_bare_minimum_bar_count(self):
        holdout_start = datetime(2024, 7, 1, tzinfo=timezone.utc)
        development_end = holdout_start - timedelta(microseconds=1)
        bounds = warmup_range(
            holdout_start=holdout_start, development_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            development_end=development_end, timeframe="5m",
        )
        start, end = bounds
        span_minutes = (end - start).total_seconds() / 60
        # 50 bars * 5 minutes * 3x buffer = 750 minutes, minus the 1
        # microsecond `end` is offset by -- comfortably more than the
        # bare 50-bar (250-minute) minimum.
        assert span_minutes > 250

    def test_a_gap_between_development_end_and_holdout_start_is_never_read(self):
        """See this module's own docstring -- audit finding, fixed."""
        development_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        development_end = datetime(2024, 1, 10, tzinfo=timezone.utc)  # dev ends Jan 10
        holdout_start = datetime(2024, 2, 1, tzinfo=timezone.utc)  # holdout starts Feb 1 -- a 22-day gap

        bounds = warmup_range(
            holdout_start=holdout_start, development_start=development_start, development_end=development_end, timeframe="5m"
        )
        assert bounds is not None
        start, end = bounds
        assert end <= development_end  # never reads into the undeclared gap
        assert start <= end
        assert start >= development_start  # and never before the partition's own development window either

    def test_when_development_end_is_adjacent_to_holdout_the_fix_is_a_no_op(self):
        """The common case (every example partition elsewhere in this
        codebase's tests/README): development_end is set to exactly
        `holdout_start - 1 microsecond` -- confirms the audit fix does
        not change behavior here."""
        holdout_start = datetime(2024, 7, 1, tzinfo=timezone.utc)
        development_end = holdout_start - timedelta(microseconds=1)
        bounds = warmup_range(
            holdout_start=holdout_start, development_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            development_end=development_end, timeframe="5m",
        )
        assert bounds is not None
        _start, end = bounds
        assert end == development_end == holdout_start - timedelta(microseconds=1)
