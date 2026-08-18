"""Tests for app/research/metrics.py -- the pure metric math Research
v1's engine (app/research/engine.py) is built on: forward-window
outcome returns and the bar-count conversion the outcome (and, at
experiment-creation time, app/api/research.py's own validation) relies
on. Condition-side trailing-return math moved to
app/features/price.py::trailing_return() in v0.1.24 -- see that
module's own tests (tests/test_feature_price.py) for its coverage; this
file no longer tests it (removed along with metrics.trailing_return()/
parse_trailing_return_metric() themselves -- see metrics.py's module
docstring). No database, no HTTP -- synthetic HistoricalBar lists only.
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
