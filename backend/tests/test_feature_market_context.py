"""Tests for app/features/market_context.py -- SPY/QQQ returns and
relative strength, and the exact-timestamp alignment rule between the
underlying and SPY/QQQ. No database, no HTTP -- synthetic HistoricalBar
lists only.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.market_data import HistoricalBar
from app.features.market_context import build_timestamp_index, compute_market_context_features


def _bar(symbol, ts, close) -> HistoricalBar:
    return HistoricalBar(
        symbol=symbol, timestamp=ts, open=close, high=close, low=close, close=close, volume=1_000,
        provider="csv", timeframe="5m",
    )


def _series(symbol, closes: list[float], *, start=None) -> list[HistoricalBar]:
    start = start or datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    return [_bar(symbol, start + timedelta(minutes=5 * i), c) for i, c in enumerate(closes)]


def _compute(bars, index, spy_bars, qqq_bars):
    return compute_market_context_features(
        bars,
        index,
        "5m",
        spy_bars=spy_bars,
        spy_index_by_timestamp=build_timestamp_index(spy_bars),
        qqq_bars=qqq_bars,
        qqq_index_by_timestamp=build_timestamp_index(qqq_bars),
    )


class TestSpyQqqReturns:
    def test_spy_and_qqq_returns_are_computed_independently_of_the_asset(self):
        asset = _series("TSLA", [100.0] * 5 + [98.0])  # -2% over 5m
        spy = _series("SPY", [500.0] * 5 + [505.0])  # +1% over 5m
        qqq = _series("QQQ", [400.0] * 5 + [396.0])  # -1% over 5m

        features = _compute(asset, 5, spy, qqq)

        assert features.spy_return_5m == pytest.approx(0.01)
        assert features.qqq_return_5m == pytest.approx(-0.01)

    def test_relative_strength_is_asset_return_minus_index_return(self):
        asset = _series("TSLA", [100.0] * 5 + [98.0])  # -2%
        spy = _series("SPY", [500.0] * 5 + [505.0])  # +1%
        qqq = _series("QQQ", [400.0] * 5 + [400.0])  # 0%

        features = _compute(asset, 5, spy, qqq)

        assert features.relative_strength_spy_5m == pytest.approx(-0.02 - 0.01)
        assert features.relative_strength_qqq_5m == pytest.approx(-0.02 - 0.0)


class TestTimestampAlignment:
    """This feature's rule 5: SPY/QQQ bars are matched to the
    underlying by exact timestamp equality only."""

    def test_a_spy_bar_with_no_exact_timestamp_match_yields_none(self):
        asset = _series("TSLA", [100.0] * 5 + [98.0])
        # SPY is missing the bar at the underlying's signal timestamp
        # (a gap), even though it has bars before and after it.
        spy_closes = [500.0] * 5 + [505.0]
        spy = _series("SPY", spy_closes)
        del spy[5]  # remove SPY's bar at the exact signal timestamp
        qqq = _series("QQQ", [400.0] * 6)

        features = _compute(asset, 5, spy, qqq)

        assert features.spy_return_5m is None
        assert features.relative_strength_spy_5m is None
        # QQQ, which DOES have a bar at the exact timestamp, is unaffected.
        assert features.qqq_return_5m is not None

    def test_empty_spy_and_qqq_data_yields_an_all_none_but_present_submodel(self):
        asset = _series("TSLA", [100.0] * 5 + [98.0])

        features = _compute(asset, 5, [], [])

        assert features.spy_return_5m is None
        assert features.qqq_return_5m is None
        assert features.relative_strength_spy_60m is None
        assert features.relative_strength_qqq_60m is None

    def test_spy_and_qqq_series_can_have_independent_gaps_without_affecting_each_other(self):
        asset = _series("TSLA", [100.0] * 5 + [98.0])
        spy = _series("SPY", [500.0] * 5 + [505.0])
        qqq_closes = [400.0] * 5 + [396.0]
        qqq = _series("QQQ", qqq_closes)
        del qqq[5]

        features = _compute(asset, 5, spy, qqq)

        assert features.spy_return_5m is not None
        assert features.qqq_return_5m is None

    def test_never_uses_a_spy_bar_from_a_later_timestamp_than_the_signal(self):
        """No fuzzy/nearest-bar matching: a SPY bar exists ONLY after
        the signal timestamp (never at or before it) -- must not be
        used at all, not even as a fallback."""
        start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        asset = _series("TSLA", [100.0, 98.0], start=start)
        # SPY's only bar is 5 minutes AFTER the signal timestamp.
        spy = [_bar("SPY", start + timedelta(minutes=10), 500.0)]

        features = _compute(asset, 1, spy, [])

        assert features.spy_return_5m is None
