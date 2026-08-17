"""Tests for app/features/volatility.py -- realized_volatility, atr,
volatility_ratio, volatility_percentile. No database, no HTTP --
synthetic HistoricalBar lists only.
"""

import math
import statistics as pystatistics
from datetime import datetime, timedelta, timezone

import pytest

from app.models.market_data import HistoricalBar
from app.features.session import TRADING_DAYS_PER_YEAR
from app.features.volatility import (
    ATR_WINDOW_BARS,
    REALIZED_VOLATILITY_WINDOW_BARS,
    VOLATILITY_HISTORY_SESSIONS,
    atr_at,
    realized_volatility_at,
    volatility_percentile_at,
    volatility_ratio_at,
)


def _daily_bar(ts, close, high=None, low=None) -> HistoricalBar:
    high = high if high is not None else close
    low = low if low is not None else close
    return HistoricalBar(
        symbol="TSLA", timestamp=ts, open=close, high=high, low=low, close=close, volume=1_000,
        provider="csv", timeframe="1d",
    )


def _daily_bars(closes: list[float]) -> list[HistoricalBar]:
    start = datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc)  # matches this app's own 1d-bar fixture convention
    return [_daily_bar(start + timedelta(days=i), c) for i, c in enumerate(closes)]


class TestRealizedVolatilityAt:
    def test_exact_value_for_an_alternating_log_return_series(self):
        log_returns = [0.01, -0.01] * (REALIZED_VOLATILITY_WINDOW_BARS // 2)
        closes = [100.0]
        for r in log_returns:
            closes.append(closes[-1] * math.exp(r))
        bars = _daily_bars(closes)

        result = realized_volatility_at(bars, len(bars) - 1, "1d")

        expected = pystatistics.stdev(log_returns) * math.sqrt(TRADING_DAYS_PER_YEAR)
        assert result == pytest.approx(expected)

    def test_zero_for_a_perfectly_constant_growth_rate(self):
        closes = [100.0 * (1.01**i) for i in range(REALIZED_VOLATILITY_WINDOW_BARS + 1)]
        bars = _daily_bars(closes)

        result = realized_volatility_at(bars, len(bars) - 1, "1d")

        assert result == pytest.approx(0.0, abs=1e-9)

    def test_none_with_insufficient_history(self):
        closes = [100.0] * REALIZED_VOLATILITY_WINDOW_BARS  # one short of the required 21 closes
        bars = _daily_bars(closes)
        assert realized_volatility_at(bars, len(bars) - 1, "1d") is None

    def test_none_when_a_bar_is_missing_inside_the_window(self):
        closes = [100.0 + i for i in range(REALIZED_VOLATILITY_WINDOW_BARS + 1)]
        bars = _daily_bars(closes)
        del bars[10]  # break contiguity
        assert realized_volatility_at(bars, len(bars) - 1, "1d") is None

    def test_none_when_a_close_in_the_window_is_non_positive(self):
        closes = [100.0] * REALIZED_VOLATILITY_WINDOW_BARS + [0.0]
        bars = _daily_bars(closes)
        assert realized_volatility_at(bars, len(bars) - 1, "1d") is None

    def test_never_uses_a_bar_past_the_given_index(self):
        closes = [100.0 + (i % 3) for i in range(REALIZED_VOLATILITY_WINDOW_BARS + 2)]
        bars = _daily_bars(closes)
        index = REALIZED_VOLATILITY_WINDOW_BARS  # leaves exactly one bar after it

        before = realized_volatility_at(bars, index, "1d")
        bars[index + 1] = _daily_bar(bars[index + 1].timestamp, 999_999.0)
        after = realized_volatility_at(bars, index, "1d")

        assert before == after


class TestAtrAt:
    def test_exact_value_with_a_constant_true_range(self):
        start = datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc)
        bars = [
            HistoricalBar(
                symbol="TSLA", timestamp=start + timedelta(days=i), open=100.0, high=101.0, low=99.0, close=100.0,
                volume=1_000, provider="csv", timeframe="1d",
            )
            for i in range(ATR_WINDOW_BARS + 1)
        ]

        result = atr_at(bars, ATR_WINDOW_BARS, "1d")

        assert result == pytest.approx(2.0)  # high-low=2 dominates every TR term here

    def test_a_gap_bar_is_picked_up_via_the_prior_close_terms(self):
        start = datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc)
        bars = [
            HistoricalBar(
                symbol="TSLA", timestamp=start + timedelta(days=i), open=100.0, high=101.0, low=99.0, close=100.0,
                volume=1_000, provider="csv", timeframe="1d",
            )
            for i in range(ATR_WINDOW_BARS)
        ]
        # Final bar gaps up hard: TR = max(high-low, |high-prev_close|, |low-prev_close|)
        #                            = max(110-108, |110-100|, |108-100|) = max(2, 10, 8) = 10
        bars.append(
            HistoricalBar(
                symbol="TSLA", timestamp=start + timedelta(days=ATR_WINDOW_BARS), open=109.0, high=110.0, low=108.0,
                close=109.0, volume=1_000, provider="csv", timeframe="1d",
            )
        )

        result = atr_at(bars, ATR_WINDOW_BARS, "1d")

        # 13 bars of TR=2.0, 1 bar of TR=10.0 -- simple mean.
        expected = (13 * 2.0 + 10.0) / ATR_WINDOW_BARS
        assert result == pytest.approx(expected)

    def test_none_with_insufficient_history(self):
        start = datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc)
        bars = [
            HistoricalBar(
                symbol="TSLA", timestamp=start + timedelta(days=i), open=100.0, high=101.0, low=99.0, close=100.0,
                volume=1_000, provider="csv", timeframe="1d",
            )
            for i in range(ATR_WINDOW_BARS)  # one short of the required 15 bars
        ]
        assert atr_at(bars, ATR_WINDOW_BARS - 1, "1d") is None

    def test_none_when_a_bar_is_missing_inside_the_window(self):
        start = datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc)
        bars = [
            HistoricalBar(
                symbol="TSLA", timestamp=start + timedelta(days=i), open=100.0, high=101.0, low=99.0, close=100.0,
                volume=1_000, provider="csv", timeframe="1d",
            )
            for i in range(ATR_WINDOW_BARS + 1)
        ]
        del bars[5]
        assert atr_at(bars, len(bars) - 1, "1d") is None


def _volatility_regime_bars(low_vol_sessions: int, high_vol_sessions: int) -> list[HistoricalBar]:
    """A deterministic (no RNG) daily-bar series: a long low-volatility
    regime (alternating +-0.1% steps) followed by a high-volatility
    regime (alternating +-5% steps) -- used to exercise
    volatility_ratio/percentile against a real, qualitatively-obvious
    "today is much more volatile than history" scenario without
    needing to hand-derive an exact expected float for either.
    """
    closes = [100.0]
    for i in range(low_vol_sessions):
        r = 0.001 if i % 2 == 0 else -0.001
        closes.append(closes[-1] * math.exp(r))
    for i in range(high_vol_sessions):
        r = 0.05 if i % 2 == 0 else -0.05
        closes.append(closes[-1] * math.exp(r))
    return _daily_bars(closes)


class TestVolatilityRatioAndPercentile:
    def test_none_with_fewer_than_the_required_distinct_history_sessions(self):
        bars = _volatility_regime_bars(low_vol_sessions=REALIZED_VOLATILITY_WINDOW_BARS + 5, high_vol_sessions=1)
        index = len(bars) - 1

        assert volatility_ratio_at(bars, index, "1d") is None
        assert volatility_percentile_at(bars, index, "1d") is None

    def test_a_volatility_spike_produces_a_ratio_above_one_and_a_high_percentile(self):
        bars = _volatility_regime_bars(
            low_vol_sessions=VOLATILITY_HISTORY_SESSIONS + REALIZED_VOLATILITY_WINDOW_BARS + 10,
            high_vol_sessions=REALIZED_VOLATILITY_WINDOW_BARS + 5,
        )
        index = len(bars) - 1

        ratio = volatility_ratio_at(bars, index, "1d")
        percentile = volatility_percentile_at(bars, index, "1d")

        assert ratio is not None and ratio > 1.0
        assert percentile is not None and percentile >= 0.9

    def test_a_calm_bar_within_a_calm_history_produces_a_ratio_near_one(self):
        bars = _volatility_regime_bars(
            low_vol_sessions=VOLATILITY_HISTORY_SESSIONS + REALIZED_VOLATILITY_WINDOW_BARS + 10, high_vol_sessions=0
        )
        index = len(bars) - 1

        ratio = volatility_ratio_at(bars, index, "1d")

        assert ratio is not None and ratio == pytest.approx(1.0, abs=0.05)

    def test_none_when_current_realized_volatility_is_itself_undefined(self):
        bars = _daily_bars([100.0] * 3)  # far too short for even realized_volatility itself
        assert volatility_ratio_at(bars, 2, "1d") is None
        assert volatility_percentile_at(bars, 2, "1d") is None

    def test_never_uses_a_bar_past_the_given_index(self):
        bars = _volatility_regime_bars(
            low_vol_sessions=VOLATILITY_HISTORY_SESSIONS + REALIZED_VOLATILITY_WINDOW_BARS + 10,
            high_vol_sessions=REALIZED_VOLATILITY_WINDOW_BARS + 5,
        )
        index = len(bars) - 3  # leave a couple of bars after it

        ratio_before = volatility_ratio_at(bars, index, "1d")
        percentile_before = volatility_percentile_at(bars, index, "1d")

        bars[index + 1] = _daily_bar(bars[index + 1].timestamp, 999_999.0)
        bars[index + 2] = _daily_bar(bars[index + 2].timestamp, 1.0)

        ratio_after = volatility_ratio_at(bars, index, "1d")
        percentile_after = volatility_percentile_at(bars, index, "1d")

        assert ratio_before == ratio_after
        assert percentile_before == percentile_after
