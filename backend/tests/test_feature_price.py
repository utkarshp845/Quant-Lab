"""Tests for app/features/price.py -- return_5m/15m/30m/60m. No
database, no HTTP -- synthetic HistoricalBar lists only.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.market_data import HistoricalBar
from app.features.price import compute_price_features, trailing_return


def _bar(ts, close) -> HistoricalBar:
    return HistoricalBar(
        symbol="TSLA", timestamp=ts, open=close, high=close, low=close, close=close, volume=1_000,
        provider="csv", timeframe="5m",
    )


def _bars(closes: list[float], *, step_minutes=5) -> list[HistoricalBar]:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    return [_bar(start + timedelta(minutes=step_minutes * i), c) for i, c in enumerate(closes)]


class TestTrailingReturn:
    def test_computes_the_return_over_the_window(self):
        bars = _bars([100.0, 101.0, 102.0, 103.0, 104.0, 99.0])
        # 5 bars back from index 5 is index 0 (100.0).
        assert trailing_return(bars, 5, 25, "5m") == pytest.approx(-0.01)

    def test_none_when_insufficient_trailing_history(self):
        bars = _bars([100.0, 101.0, 102.0])
        assert trailing_return(bars, 1, 25, "5m") is None  # needs 5 bars back, only 1 available

    def test_none_at_the_very_first_bar(self):
        bars = _bars([100.0])
        assert trailing_return(bars, 0, 5, "5m") is None

    def test_none_when_a_bar_is_missing_inside_the_window(self):
        """rule 4: handle missing bars explicitly -- a gap must yield
        None, never a value silently computed against the wrong bar."""
        bars = _bars([100.0, 101.0, 102.0, 103.0, 104.0, 99.0, 98.0])
        del bars[2]  # remove one bar from inside the window -- the rest are no longer evenly spaced
        assert trailing_return(bars, 5, 25, "5m") is None

    def test_none_when_the_base_close_is_zero(self):
        """rule 6: handle division-by-zero safely."""
        bars = _bars([0.0, 101.0, 102.0, 103.0, 104.0, 99.0])
        assert trailing_return(bars, 5, 25, "5m") is None

    def test_none_when_the_horizon_does_not_align_with_the_timeframe(self):
        bars = _bars([100.0, 101.0], step_minutes=60)
        assert trailing_return(bars, 1, 30, "1h") is None  # 30 minutes is not a whole number of 1h bars

    def test_never_uses_a_bar_past_the_given_index(self):
        """The no-look-ahead guarantee: mutating bars AFTER `index`
        must not change the computed value at `index`."""
        bars = _bars([100.0, 101.0, 102.0, 200.0])
        value_before = trailing_return(bars, 2, 10, "5m")

        bars[3] = _bar(bars[3].timestamp, 999_999.0)

        value_after = trailing_return(bars, 2, 10, "5m")
        assert value_before == value_after


class TestComputePriceFeatures:
    def test_all_four_horizons_are_populated_with_enough_history(self):
        # 12 bars of 5m = 60 minutes -- enough for every horizon.
        closes = [100.0 + i * 0.5 for i in range(13)]
        bars = _bars(closes)

        features = compute_price_features(bars, 12, "5m")

        assert features.return_5m is not None
        assert features.return_15m is not None
        assert features.return_30m is not None
        assert features.return_60m is not None

    def test_partial_history_leaves_only_the_shorter_horizons_populated(self):
        # 4 bars of 5m = 20 minutes: enough for 5m/15m, not 30m/60m.
        closes = [100.0, 100.5, 101.0, 101.5]
        bars = _bars(closes)

        features = compute_price_features(bars, 3, "5m")

        assert features.return_5m is not None
        assert features.return_15m is not None
        assert features.return_30m is None
        assert features.return_60m is None

    def test_exact_hand_verified_values(self):
        closes = [100.0] * 5 + [98.0]  # index 5, 5m return vs index 4 (100.0)
        bars = _bars(closes)

        features = compute_price_features(bars, 5, "5m")

        assert features.return_5m == pytest.approx(-0.02)
