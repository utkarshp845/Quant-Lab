"""Tests for app/research/metrics.py -- the pure metric math Research
v1's engine (app/research/engine.py) is built on: trailing-window
condition returns, forward-window outcome returns, and the bar-count
conversions both windows rely on. No database, no HTTP -- synthetic
HistoricalBar lists only.
"""

import pytest

from app.models.market_data import HistoricalBar
from app.research import metrics


def _bar(close: float, timestamp="2026-08-10T13:30:00Z") -> HistoricalBar:
    return HistoricalBar(
        symbol="TSLA",
        timestamp=timestamp,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000,
        provider="csv",
        timeframe="5m",
    )


class TestTimeframeMinutes:
    def test_known_timeframes_resolve_to_minutes(self):
        assert metrics.timeframe_minutes("1m") == 1
        assert metrics.timeframe_minutes("5m") == 5
        assert metrics.timeframe_minutes("15m") == 15
        assert metrics.timeframe_minutes("1h") == 60
        assert metrics.timeframe_minutes("1d") == 1440

    def test_unknown_timeframe_raises(self):
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            metrics.timeframe_minutes("3m")


class TestParseTrailingReturnMetric:
    def test_parses_the_minutes_component(self):
        assert metrics.parse_trailing_return_metric("30m_return") == 30
        assert metrics.parse_trailing_return_metric("5m_return") == 5

    def test_rejects_anything_not_shaped_like_nm_return(self):
        for bad in ["forward_return", "30_minute_return", "30m-return", "return_30m", ""]:
            with pytest.raises(ValueError, match="Unsupported condition metric"):
                metrics.parse_trailing_return_metric(bad)


class TestBarsForWindow:
    def test_exact_multiple_converts_cleanly(self):
        assert metrics.bars_for_window(30, "5m") == 6
        assert metrics.bars_for_window(60, "5m") == 12
        assert metrics.bars_for_window(60, "1h") == 1

    def test_non_multiple_raises_rather_than_rounding(self):
        with pytest.raises(ValueError, match="not a whole number"):
            metrics.bars_for_window(30, "1h")  # 30 minutes is half of a 1h bar

    def test_zero_or_negative_minutes_raises(self):
        with pytest.raises(ValueError, match="positive number of minutes"):
            metrics.bars_for_window(0, "5m")


class TestTrailingReturn:
    def test_computes_return_over_the_window(self):
        bars = [_bar(100.0), _bar(101.0), _bar(102.0), _bar(103.0), _bar(104.0), _bar(99.0)]
        # 5 bars back from index 5 is index 0 (100.0) -> (99.0 - 100.0) / 100.0
        assert metrics.trailing_return(bars, index=5, window_bars=5) == pytest.approx(-0.01)

    def test_none_when_not_enough_trailing_bars(self):
        bars = [_bar(100.0), _bar(101.0), _bar(102.0)]
        assert metrics.trailing_return(bars, index=1, window_bars=5) is None

    def test_none_at_the_very_first_bar_for_any_positive_window(self):
        bars = [_bar(100.0)]
        assert metrics.trailing_return(bars, index=0, window_bars=1) is None

    def test_never_uses_a_bar_past_the_given_index(self):
        """The look-ahead guarantee itself: mutating bars AFTER `index`
        must not change the computed value at `index`."""
        bars = [_bar(100.0), _bar(101.0), _bar(102.0), _bar(200.0)]
        value_before = metrics.trailing_return(bars, index=2, window_bars=2)

        bars[3] = _bar(999_999.0)  # a wildly different future bar

        value_after = metrics.trailing_return(bars, index=2, window_bars=2)
        assert value_before == value_after


class TestForwardReturn:
    def test_computes_return_to_the_forward_bar(self):
        bars = [_bar(100.0), _bar(101.0), _bar(102.0), _bar(99.0)]
        result = metrics.forward_return(bars, index=0, window_bars=3)
        assert result is not None
        value, outcome_bar = result
        assert value == pytest.approx(-0.01)
        assert outcome_bar.close == 99.0

    def test_none_when_forward_bar_is_outside_the_dataset(self):
        bars = [_bar(100.0), _bar(101.0)]
        assert metrics.forward_return(bars, index=1, window_bars=1) is None

    def test_none_when_forward_bar_would_be_the_last_index_exactly_out_of_range(self):
        bars = [_bar(100.0), _bar(101.0), _bar(102.0)]
        # index 2 + window 1 = index 3, which does not exist (len == 3)
        assert metrics.forward_return(bars, index=2, window_bars=1) is None
