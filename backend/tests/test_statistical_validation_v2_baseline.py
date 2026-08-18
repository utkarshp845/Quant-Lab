"""Tests for app/statistical_validation/v2/baseline.py -- Method A's
non-overlapping-window subsampling, in isolation.
"""

from datetime import datetime, timedelta, timezone

from app.models.backtesting import BacktestSignal, BacktestWindowOutcome
from app.statistical_validation.v2.baseline import non_overlapping_baseline

_FIVE_MIN = timedelta(minutes=5)
_BASE = datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc)


def _baseline_signal(entry_offset_minutes: int, *, windows: list[int] = (5,)) -> BacktestSignal:
    entry_ts = _BASE + timedelta(minutes=entry_offset_minutes)
    outcomes = [
        BacktestWindowOutcome(window_bars=w, outcome_timestamp=entry_ts + timedelta(minutes=5 * w), forward_return=0.001 * w, mfe=0.01, mae=-0.01)
        for w in windows
    ]
    return BacktestSignal(
        backtest_id="baseline-control", experiment_id="baseline-control", symbol="TSLA", timeframe="5m",
        signal_timestamp=entry_ts - _FIVE_MIN, entry_timestamp=entry_ts, entry_price=100.0,
        feature_values={"volume.volume": 1000}, outcomes=outcomes,
    )


class TestNonOverlappingSelection:
    def test_dense_every_bar_baseline_selects_every_window_bars_th_entry(self):
        """Entries at 0,5,10,...,45 minutes (every bar, 5m spacing,
        10 entries total). window_bars=5 -> a 25-minute non-overlap
        requirement: select 0 (next_allowed=25), select 25
        (next_allowed=50), then 30/35/40/45 are all < 50 -> skipped."""
        signals = [_baseline_signal(m) for m in range(0, 50, 5)]  # entries at 0,5,...,45

        selected = non_overlapping_baseline(signals, window_bars=5, bar_interval=_FIVE_MIN)

        selected_offsets = [(s.entry_timestamp - _BASE).total_seconds() / 60 for s in selected]
        assert selected_offsets == [0, 25]

    def test_no_two_selected_windows_overlap(self):
        signals = [_baseline_signal(m) for m in range(0, 100, 5)]
        window_bars = 5

        selected = non_overlapping_baseline(signals, window_bars=window_bars, bar_interval=_FIVE_MIN)

        for earlier, later in zip(selected, selected[1:]):
            gap = later.entry_timestamp - earlier.entry_timestamp
            assert gap >= window_bars * _FIVE_MIN

    def test_a_gap_in_the_underlying_data_is_handled_without_assuming_dense_spacing(self):
        """Entries at 0, 5, 40 minutes -- a real gap between 5 and 40.
        window_bars=5 (25-minute non-overlap requirement): select 0
        (next_allowed=25), skip 5 (< 25), select 40 (>= 25)."""
        signals = [_baseline_signal(0), _baseline_signal(5), _baseline_signal(40)]

        selected = non_overlapping_baseline(signals, window_bars=5, bar_interval=_FIVE_MIN)

        offsets = [(s.entry_timestamp - _BASE).total_seconds() / 60 for s in selected]
        assert offsets == [0, 40]

    def test_signals_without_the_requested_window_are_excluded(self):
        """A signal near the end of the dataset might lack a
        window_bars=60 outcome even though it has a window_bars=5 one
        -- must be excluded from a window_bars=60 selection entirely,
        not treated as eligible with an undefined return."""
        with_5_only = _baseline_signal(0, windows=[5])
        with_5_and_60 = _baseline_signal(100, windows=[5, 60])

        selected = non_overlapping_baseline([with_5_only, with_5_and_60], window_bars=60, bar_interval=_FIVE_MIN)

        assert len(selected) == 1
        assert selected[0] is with_5_and_60

    def test_empty_input_returns_empty_output(self):
        assert non_overlapping_baseline([], window_bars=5, bar_interval=_FIVE_MIN) == []

    def test_out_of_order_input_is_handled_correctly(self):
        signals = [_baseline_signal(45), _baseline_signal(0), _baseline_signal(25), _baseline_signal(10)]

        selected = non_overlapping_baseline(signals, window_bars=5, bar_interval=_FIVE_MIN)

        offsets = [(s.entry_timestamp - _BASE).total_seconds() / 60 for s in selected]
        assert offsets == sorted(offsets)  # chronological output regardless of input order
        assert offsets[0] == 0
