"""Tests for app/features/timeframes.py -- the timeframe <-> minutes <->
bar-count conversion and the timestamp-contiguity check every trailing-
window feature relies on for missing-bar handling. No database, no
HTTP -- synthetic HistoricalBar lists only.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.market_data import HistoricalBar
from app.features.timeframes import bars_for_window, expected_timestamp_before, is_contiguous_window, timeframe_minutes


def _bar(ts, close=100.0) -> HistoricalBar:
    return HistoricalBar(
        symbol="TSLA", timestamp=ts, open=close, high=close, low=close, close=close, volume=1_000,
        provider="csv", timeframe="5m",
    )


def _bars(count: int, *, step_minutes=5, start=None) -> list[HistoricalBar]:
    start = start or datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    return [_bar(start + timedelta(minutes=step_minutes * i)) for i in range(count)]


class TestTimeframeMinutes:
    def test_known_timeframes_resolve_to_minutes(self):
        assert timeframe_minutes("1m") == 1
        assert timeframe_minutes("5m") == 5
        assert timeframe_minutes("15m") == 15
        assert timeframe_minutes("1h") == 60
        assert timeframe_minutes("1d") == 1440

    def test_unknown_timeframe_raises(self):
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            timeframe_minutes("3m")


class TestBarsForWindow:
    def test_exact_multiple_converts_cleanly(self):
        assert bars_for_window(30, "5m") == 6
        assert bars_for_window(60, "5m") == 12
        assert bars_for_window(60, "1h") == 1

    def test_non_multiple_returns_none_rather_than_raising(self):
        """Unlike research/metrics.py's bars_for_window (which raises),
        this one returns None: a misaligned window makes ONE feature
        undefined for the whole run, not a reason to abort every other
        feature too."""
        assert bars_for_window(30, "1h") is None  # 30 minutes is half of a 1h bar
        assert bars_for_window(5, "1h") is None

    def test_zero_or_negative_minutes_raises(self):
        with pytest.raises(ValueError, match="positive number of minutes"):
            bars_for_window(0, "5m")
        with pytest.raises(ValueError, match="positive number of minutes"):
            bars_for_window(-5, "5m")

    def test_unknown_timeframe_raises(self):
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            bars_for_window(30, "3m")


class TestIsContiguousWindow:
    def test_true_for_a_perfectly_contiguous_run(self):
        bars = _bars(5)
        assert is_contiguous_window(bars, 0, 4, 5) is True

    def test_false_when_a_bar_is_missing_in_the_middle(self):
        bars = _bars(5)
        del bars[2]  # remove the middle bar -- the remaining bars now have a 10-minute gap
        assert is_contiguous_window(bars, 0, 3, 5) is False

    def test_false_when_start_index_is_negative(self):
        bars = _bars(5)
        assert is_contiguous_window(bars, -1, 3, 5) is False

    def test_false_when_end_index_is_out_of_range(self):
        bars = _bars(5)
        assert is_contiguous_window(bars, 0, 10, 5) is False

    def test_true_for_a_single_bar_window(self):
        bars = _bars(1)
        assert is_contiguous_window(bars, 0, 0, 5) is True

    def test_false_when_the_step_is_the_wrong_size(self):
        """Bars 15 minutes apart are not contiguous at a 5-minute step."""
        bars = _bars(3, step_minutes=15)
        assert is_contiguous_window(bars, 0, 2, 5) is False


class TestExpectedTimestampBefore:
    def test_subtracts_exactly_the_given_minutes(self):
        ts = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        assert expected_timestamp_before(ts, 30) == datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
