"""Tests for app/features/volume.py -- volume, relative_volume,
volume_acceleration. No database, no HTTP -- synthetic HistoricalBar
lists only.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.market_data import HistoricalBar
from app.features.volume import RELATIVE_VOLUME_LOOKBACK_SESSIONS, compute_volume_features, relative_volume, volume_acceleration


def _bar(ts, volume, close=100.0) -> HistoricalBar:
    return HistoricalBar(
        symbol="TSLA", timestamp=ts, open=close, high=close, low=close, close=close, volume=volume,
        provider="csv", timeframe="5m",
    )


class TestVolumeAcceleration:
    def test_computes_current_over_previous(self):
        base = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        bars = [_bar(base, 1000), _bar(base + timedelta(minutes=5), 2000)]

        assert volume_acceleration(bars, 1, "5m") == pytest.approx(2.0)

    def test_none_at_the_first_bar(self):
        bars = [_bar(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc), 1000)]
        assert volume_acceleration(bars, 0, "5m") is None

    def test_none_when_the_previous_bar_is_missing(self):
        """rule 4: a gap immediately before this bar must not silently
        compare against a further-back, stale bar."""
        base = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        bars = [_bar(base, 1000), _bar(base + timedelta(minutes=5), 1500), _bar(base + timedelta(minutes=15), 2000)]
        # bars[2] is 10 minutes after bars[1], not the expected 5.
        assert volume_acceleration(bars, 2, "5m") is None

    def test_none_when_previous_volume_is_zero(self):
        """rule 6: division by zero."""
        base = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        bars = [_bar(base, 0), _bar(base + timedelta(minutes=5), 1500)]
        assert volume_acceleration(bars, 1, "5m") is None


class TestRelativeVolume:
    def test_none_with_no_prior_matching_bar(self):
        bars = [_bar(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc), 1000)]
        assert relative_volume(bars, 0) is None

    def test_uses_the_single_available_prior_session_at_the_same_time_of_day(self):
        day1_930 = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)  # 9:30am ET
        day2_930 = datetime(2026, 1, 6, 14, 30, tzinfo=timezone.utc)
        bars = [_bar(day1_930, 1000), _bar(day2_930, 3000)]

        assert relative_volume(bars, 1) == pytest.approx(3.0)  # 3000 / 1000

    def test_averages_over_multiple_prior_sessions_at_the_same_time_of_day(self):
        base_day = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        bars = [
            _bar(base_day, 1000),
            _bar(base_day + timedelta(days=1), 2000),
            _bar(base_day + timedelta(days=2), 4500),  # baseline = mean(1000, 2000) = 1500
        ]

        assert relative_volume(bars, 2) == pytest.approx(4500 / 1500)

    def test_a_different_time_of_day_is_not_a_match(self):
        day1_930 = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        day1_935 = datetime(2026, 1, 5, 14, 35, tzinfo=timezone.utc)
        day2_935 = datetime(2026, 1, 6, 14, 35, tzinfo=timezone.utc)
        bars = [_bar(day1_930, 1000), _bar(day1_935, 5000), _bar(day2_935, 6000)]

        # day2's 9:35 bar must only match day1's 9:35 bar (5000), not the 9:30 one.
        assert relative_volume(bars, 2) == pytest.approx(6000 / 5000)

    def test_lookback_is_capped_at_the_configured_number_of_sessions(self):
        base_day = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        # RELATIVE_VOLUME_LOOKBACK_SESSIONS sessions of volume=1000, then one outlier
        # far enough back that it must NOT be included in the baseline.
        bars = [_bar(base_day + timedelta(days=-100), 999_999)]  # ancient outlier, well outside the cap
        bars += [_bar(base_day + timedelta(days=i), 1000) for i in range(RELATIVE_VOLUME_LOOKBACK_SESSIONS)]
        bars.append(_bar(base_day + timedelta(days=RELATIVE_VOLUME_LOOKBACK_SESSIONS), 2000))

        result = relative_volume(bars, len(bars) - 1)

        assert result == pytest.approx(2.0)  # baseline stayed exactly 1000, the outlier was excluded

    def test_none_when_the_baseline_average_is_zero(self):
        """rule 6: division by zero."""
        base_day = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        bars = [_bar(base_day, 0), _bar(base_day + timedelta(days=1), 500)]
        assert relative_volume(bars, 1) is None

    def test_never_uses_a_bar_past_the_given_index(self):
        base_day = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        bars = [_bar(base_day, 1000), _bar(base_day + timedelta(days=1), 2000), _bar(base_day + timedelta(days=2), 3000)]

        value_before = relative_volume(bars, 1)
        bars[2] = _bar(bars[2].timestamp, 999_999)
        value_after = relative_volume(bars, 1)

        assert value_before == value_after


class TestComputeVolumeFeatures:
    def test_volume_is_always_present_never_none(self):
        bars = [_bar(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc), 1234)]
        features = compute_volume_features(bars, 0, "5m")
        assert features.volume == 1234

    def test_derived_fields_are_none_with_no_history(self):
        bars = [_bar(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc), 1234)]
        features = compute_volume_features(bars, 0, "5m")
        assert features.relative_volume is None
        assert features.volume_acceleration is None
