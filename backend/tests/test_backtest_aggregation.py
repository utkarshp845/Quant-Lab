"""Direct unit tests for app/backtesting/aggregation.py::aggregate_results()
-- isolated from app/backtesting/engine.py (already covered end to end
by tests/test_backtest_engine.py::TestAggregateResults). Builds
BacktestSignal/BacktestWindowOutcome fixtures by hand so every input
number is explicit, rather than deriving them from bars.
"""

import pytest

from app.backtesting.aggregation import aggregate_results
from app.models.backtesting import BacktestSignal, BacktestWindowOutcome


def _signal(*outcomes: BacktestWindowOutcome, signal_timestamp="2026-01-05T14:00:00Z") -> BacktestSignal:
    return BacktestSignal(
        backtest_id="bt-1",
        experiment_id="exp-1",
        symbol="TSLA",
        timeframe="5m",
        signal_timestamp=signal_timestamp,
        entry_timestamp="2026-01-05T14:05:00Z",
        entry_price=100.0,
        feature_values={"price.return_5m": -0.02},
        outcomes=list(outcomes),
    )


def _outcome(window_bars: int, forward_return: float, mfe: float, mae: float) -> BacktestWindowOutcome:
    return BacktestWindowOutcome(window_bars=window_bars, outcome_timestamp="2026-01-05T14:30:00Z", forward_return=forward_return, mfe=mfe, mae=mae)


class TestEmptyInput:
    def test_no_signals_yields_a_result_per_window_all_none(self):
        results = aggregate_results([], windows=[5, 15])

        assert [w.window_bars for w in results.windows] == [5, 15]
        for window_results in results.windows:
            assert window_results.signal_count == 0
            assert window_results.win_count == 0
            assert window_results.win_rate is None
            assert window_results.mean_return is None
            assert window_results.median_return is None
            assert window_results.std_dev_return is None
            assert window_results.best_return is None
            assert window_results.worst_return is None
            assert window_results.mean_mfe is None
            assert window_results.mean_mae is None


class TestSingleWindowMultipleSignals:
    def test_win_rate_counts_strictly_positive_returns_as_wins(self):
        signals = [
            _signal(_outcome(5, forward_return=0.02, mfe=0.03, mae=-0.01)),  # win
            _signal(_outcome(5, forward_return=-0.01, mfe=0.01, mae=-0.02)),  # loss
            _signal(_outcome(5, forward_return=0.0, mfe=0.005, mae=-0.005)),  # exactly zero -- NOT a win
        ]

        results = aggregate_results(signals, windows=[5])
        window = results.windows[0]

        assert window.signal_count == 3
        assert window.win_count == 1
        assert window.win_rate == 1 / 3

    def test_mean_median_best_worst_mfe_mae(self):
        signals = [
            _signal(_outcome(5, forward_return=0.02, mfe=0.04, mae=-0.01)),
            _signal(_outcome(5, forward_return=-0.01, mfe=0.01, mae=-0.03)),
            _signal(_outcome(5, forward_return=0.03, mfe=0.05, mae=-0.005)),
        ]

        results = aggregate_results(signals, windows=[5])
        window = results.windows[0]

        assert window.mean_return == pytest.approx((0.02 - 0.01 + 0.03) / 3)
        assert window.median_return == pytest.approx(0.02)
        assert window.best_return == pytest.approx(0.03)
        assert window.worst_return == pytest.approx(-0.01)
        assert window.mean_mfe == pytest.approx((0.04 + 0.01 + 0.05) / 3)
        assert window.mean_mae == pytest.approx((-0.01 - 0.03 - 0.005) / 3)

    def test_std_dev_is_none_for_a_single_signal(self):
        results = aggregate_results([_signal(_outcome(5, forward_return=0.02, mfe=0.03, mae=-0.01))], windows=[5])
        window = results.windows[0]

        assert window.signal_count == 1
        assert window.std_dev_return is None
        assert window.mean_return == 0.02
        assert window.best_return == window.worst_return == 0.02

    def test_std_dev_matches_stdlib_for_two_or_more_signals(self):
        import statistics

        returns = [0.02, -0.01, 0.03, -0.02]
        signals = [_signal(_outcome(5, forward_return=r, mfe=abs(r), mae=-abs(r))) for r in returns]

        results = aggregate_results(signals, windows=[5])

        assert results.windows[0].std_dev_return == statistics.stdev(returns)


class TestMultipleWindowsAreIndependent:
    def test_each_windows_statistics_only_reflect_its_own_outcomes(self):
        signals = [
            _signal(
                _outcome(5, forward_return=0.02, mfe=0.03, mae=-0.01),
                _outcome(15, forward_return=0.05, mfe=0.06, mae=-0.01),
            ),
            _signal(
                _outcome(5, forward_return=-0.01, mfe=0.01, mae=-0.02),
                # No 15-bar outcome for this signal (fell outside the dataset).
            ),
        ]

        results = aggregate_results(signals, windows=[5, 15])
        window_5, window_15 = results.windows

        assert window_5.signal_count == 2
        assert window_5.mean_return == pytest.approx((0.02 - 0.01) / 2)

        assert window_15.signal_count == 1
        assert window_15.mean_return == pytest.approx(0.05)
        assert window_15.std_dev_return is None  # only one 15-bar outcome exists
