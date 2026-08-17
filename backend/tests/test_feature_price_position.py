"""Tests for app/features/price_position.py -- vwap_distance,
ma20_distance, ma50_distance, intraday_range_position. No database, no
HTTP -- synthetic HistoricalBar lists only.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.market_data import HistoricalBar
from app.features.price_position import (
    SMA20_WINDOW_BARS,
    SMA50_WINDOW_BARS,
    compute_price_position_features,
    intraday_range_position,
    moving_average_distance,
    vwap,
    vwap_distance,
)


def _bar(ts, close, high=None, low=None, volume=1_000) -> HistoricalBar:
    high = high if high is not None else close
    low = low if low is not None else close
    return HistoricalBar(
        symbol="TSLA", timestamp=ts, open=close, high=high, low=low, close=close, volume=volume,
        provider="csv", timeframe="5m",
    )


class TestVwap:
    def test_exact_value_over_two_bars_in_the_same_session(self):
        start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        bars = [
            _bar(start, close=100.0, high=101.0, low=99.0, volume=100),  # typical = 100.0
            _bar(start + timedelta(minutes=5), close=110.0, high=111.0, low=109.0, volume=300),  # typical = 110.0
        ]
        # VWAP = (100*100 + 110*300) / (100+300) = 43000/400 = 107.5
        assert vwap(bars, 1) == pytest.approx(107.5)

    def test_does_not_blend_bars_from_an_earlier_session(self):
        day1 = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        day2 = datetime(2026, 1, 6, 14, 30, tzinfo=timezone.utc)
        bars = [
            _bar(day1, close=1000.0, high=1000.0, low=1000.0, volume=999),  # day 1 -- must not leak into day 2's VWAP
            _bar(day2, close=50.0, high=51.0, low=49.0, volume=10),
        ]
        assert vwap(bars, 1) == pytest.approx(50.0)

    def test_none_when_cumulative_session_volume_is_zero(self):
        bars = [_bar(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc), close=100.0, volume=0)]
        assert vwap(bars, 0) is None

    def test_vwap_distance_matches_close_minus_vwap_over_vwap(self):
        start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        bars = [
            _bar(start, close=100.0, volume=100),
            _bar(start + timedelta(minutes=5), close=120.0, volume=100),
        ]
        session_vwap = vwap(bars, 1)
        assert vwap_distance(bars, 1) == pytest.approx((bars[1].close - session_vwap) / session_vwap)

    def test_never_uses_a_bar_past_the_given_index(self):
        start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        bars = [
            _bar(start, close=100.0, volume=100),
            _bar(start + timedelta(minutes=5), close=110.0, volume=100),
            _bar(start + timedelta(minutes=10), close=120.0, volume=100),
        ]
        before = vwap(bars, 1)
        bars[2] = _bar(bars[2].timestamp, close=999_999.0, volume=999_999)
        after = vwap(bars, 1)
        assert before == after


class TestMovingAverageDistance:
    def test_exact_value_for_sma20(self):
        start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        closes = [100.0 + i for i in range(SMA20_WINDOW_BARS)]  # 100..119
        bars = [_bar(start + timedelta(minutes=5 * i), c) for i, c in enumerate(closes)]

        sma = sum(closes) / len(closes)
        expected = (closes[-1] - sma) / sma

        assert moving_average_distance(bars, len(bars) - 1, "5m", SMA20_WINDOW_BARS) == pytest.approx(expected)

    def test_none_with_insufficient_history(self):
        start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        bars = [_bar(start + timedelta(minutes=5 * i), 100.0 + i) for i in range(SMA50_WINDOW_BARS - 1)]
        assert moving_average_distance(bars, len(bars) - 1, "5m", SMA50_WINDOW_BARS) is None

    def test_none_when_a_bar_is_missing_inside_the_window(self):
        start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        bars = [_bar(start + timedelta(minutes=5 * i), 100.0 + i) for i in range(SMA20_WINDOW_BARS)]
        del bars[10]
        assert moving_average_distance(bars, len(bars) - 1, "5m", SMA20_WINDOW_BARS) is None

    def test_none_when_the_average_is_zero(self):
        start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        closes = [10.0] * (SMA20_WINDOW_BARS // 2) + [-10.0] * (SMA20_WINDOW_BARS // 2)  # sums to 0
        bars = [_bar(start + timedelta(minutes=5 * i), c) for i, c in enumerate(closes)]
        assert moving_average_distance(bars, len(bars) - 1, "5m", SMA20_WINDOW_BARS) is None


class TestIntradayRangePosition:
    def test_exact_value_within_the_current_session(self):
        start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        bars = [
            _bar(start, close=100.0, high=105.0, low=95.0),
            _bar(start + timedelta(minutes=5), close=103.0, high=110.0, low=100.0),
        ]
        # session_high=110, session_low=95, close=103 -> (103-95)/(110-95) = 8/15
        assert intraday_range_position(bars, 1) == pytest.approx(8 / 15)

    def test_does_not_blend_an_earlier_sessions_range(self):
        day1 = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        day2 = datetime(2026, 1, 6, 14, 30, tzinfo=timezone.utc)
        bars = [
            _bar(day1, close=1.0, high=1000.0, low=-1000.0),  # day 1 -- extreme range, must not leak into day 2
            _bar(day2, close=55.0, high=60.0, low=50.0),
        ]
        assert intraday_range_position(bars, 1) == pytest.approx((55.0 - 50.0) / (60.0 - 50.0))

    def test_none_when_session_high_equals_session_low(self):
        bars = [_bar(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc), close=100.0, high=100.0, low=100.0)]
        assert intraday_range_position(bars, 0) is None

    def test_never_uses_a_bar_past_the_given_index(self):
        start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        bars = [
            _bar(start, close=100.0, high=105.0, low=95.0),
            _bar(start + timedelta(minutes=5), close=103.0, high=110.0, low=100.0),
        ]
        before = intraday_range_position(bars, 1)
        bars.append(_bar(start + timedelta(minutes=10), close=1.0, high=1_000_000.0, low=-1_000_000.0))
        after = intraday_range_position(bars, 1)
        assert before == after


class TestComputePricePositionFeatures:
    def test_all_populated_with_enough_history(self):
        start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        closes = [100.0 + i * 0.1 for i in range(SMA50_WINDOW_BARS)]
        bars = [_bar(start + timedelta(minutes=5 * i), c, high=c + 0.5, low=c - 0.5, volume=100) for i, c in enumerate(closes)]

        features = compute_price_position_features(bars, len(bars) - 1, "5m")

        assert features.vwap_distance is not None
        assert features.ma20_distance is not None
        assert features.ma50_distance is not None
        assert features.intraday_range_position is not None
