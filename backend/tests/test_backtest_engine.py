"""Tests for app/backtesting/engine.py::run_backtest() -- the pure
conditions -> signal -> next-bar-open entry -> per-window outcome ->
aggregate pipeline, against synthetic, deterministic HistoricalBar data.
No database, no HTTP, no provider involved anywhere in this file.

FeatureRecords are built via the REAL Feature Engine
(app.features.engine.compute_features(), the same function
app/api/features.py calls) and conditions are evaluated via the REAL
app.research.conditions.evaluate_feature_conditions() -- this engine
contains no feature math and no condition-matching logic of its own
(this feature's own "do not duplicate existing feature calculations or
research-condition logic" requirement), so these tests exercise the
real reused boundary rather than assuming it.

Fixture: 5-minute bars with distinct open/high/low/close (unlike
test_research_engine.py's flat-OHLC fixture) so forward_return/MFE/MAE
are all independently hand-computable. Two isolated, hand-computable
signals:

  SIGNAL A (index 2): a sharp drop, entered at index 3's open, followed
  by a further rise -> both configured windows (1 and 2 bars forward
  from entry) are profitable -> WIN at both.

  SIGNAL B (index 8): an identical-shaped sharp drop, entered at index
  9's open, followed by a decline -> both windows are losses -> LOSS at
  both.

See _BARS below for the exact OHLC values and TestHandVerifiedMath for
the worked arithmetic. `_WINDOWS = [1, 2]` (bar counts, not the spec's
full (5, 15, 30, 60) default -- see app/backtesting/engine.py's module
docstring for why windows are bar counts at all) keeps every number in
this file hand-checkable; tests/test_backtest_api.py exercises the
spec's actual default window set end to end instead.
"""

import statistics as pystatistics
from datetime import datetime, timedelta, timezone

import pytest

from app.backtesting.engine import run_backtest
from app.features.engine import compute_features
from app.models.features import FEATURE_CONTRACT_VERSION, FeatureRecord
from app.models.market_data import HistoricalBar
from app.models.research import FeatureCondition, FeatureConditionOperator

_CONDITIONS = [FeatureCondition(feature_id="price.return_5m", operator=FeatureConditionOperator.LTE, value=-0.01)]
_WINDOWS = [1, 2]

# (open, high, low, close) per 5-minute bar, index 0..11.
_OHLC: list[tuple[float, float, float, float]] = [
    (100.0, 100.0, 100.0, 100.0),  # 0 -- no trailing bar yet, return_5m is None
    (100.0, 100.0, 100.0, 100.0),  # 1 -- return_5m = 0%, not a signal
    (100.0, 100.0, 97.0, 98.0),  # 2  <- SIGNAL A: return_5m = (98-100)/100 = -2.00%
    (97.0, 99.0, 96.0, 97.5),  # 3  <- SIGNAL A's entry bar: entry_price = open = 97.0
    (97.5, 101.0, 97.0, 100.0),  # 4 <- SIGNAL A window=1 outcome bar
    (100.0, 102.0, 99.0, 101.0),  # 5 <- SIGNAL A window=2 outcome bar
    (100.0, 100.0, 100.0, 100.0),  # 6 <- return_5m = (100-101)/101 = -0.99%, does NOT trigger (> -1%)
    (100.0, 100.0, 100.0, 100.0),  # 7 -- return_5m = 0%, not a signal
    (100.0, 100.0, 97.0, 98.0),  # 8  <- SIGNAL B: return_5m = (98-100)/100 = -2.00%
    (97.0, 99.0, 96.0, 97.5),  # 9  <- SIGNAL B's entry bar: entry_price = open = 97.0
    (97.0, 98.0, 95.0, 96.8),  # 10 <- SIGNAL B window=1 outcome bar; return_5m here = -0.72%, does NOT re-trigger
    (96.8, 97.0, 90.0, 93.0),  # 11 <- SIGNAL B window=2 outcome bar; LAST bar, its own return_5m (-3.93%) WOULD
    #                                  trigger the condition, but has no next bar to enter at -- see
    #                                  TestNoLookAheadPrevention::test_a_signal_on_the_last_bar_produces_no_event
]

_SIGNAL_A_INDEX = 2
_SIGNAL_A_ENTRY_INDEX = 3
_SIGNAL_B_INDEX = 8
_SIGNAL_B_ENTRY_INDEX = 9


def _bars(ohlc: list[tuple[float, float, float, float]] = _OHLC, *, timeframe="5m") -> list[HistoricalBar]:
    base = datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc)
    return [
        HistoricalBar(
            symbol="TSLA",
            timestamp=base + timedelta(minutes=5 * i),
            open=o,
            high=h,
            low=l,
            close=c,
            volume=1_000,
            provider="csv",
            timeframe=timeframe,
        )
        for i, (o, h, l, c) in enumerate(ohlc)
    ]


def _features(bars: list[HistoricalBar], *, timeframe="5m", feature_contract_version: str = FEATURE_CONTRACT_VERSION) -> list[FeatureRecord]:
    records = compute_features(symbol="TSLA", timeframe=timeframe, provider="csv", bars=bars, calculated_at=datetime.now(timezone.utc))
    if feature_contract_version != FEATURE_CONTRACT_VERSION:
        records = [r.model_copy(update={"feature_contract_version": feature_contract_version}) for r in records]
    return records


def _run(bars=None, conditions=_CONDITIONS, windows=_WINDOWS, timeframe="5m", feature_records=None, feature_contract_version=FEATURE_CONTRACT_VERSION):
    bars = bars if bars is not None else _bars()
    feature_records = feature_records if feature_records is not None else _features(bars, timeframe=timeframe)
    return run_backtest(
        backtest_id="bt-1",
        experiment_id="exp-1",
        symbol="TSLA",
        timeframe=timeframe,
        conditions=conditions,
        windows=windows,
        bars=bars,
        feature_records=feature_records,
        feature_contract_version=feature_contract_version,
    )


class TestHandVerifiedMath:
    """If any of these fail, the fixture itself no longer means what the
    module docstring says it does."""

    def test_signal_a_condition_value(self):
        assert (_OHLC[_SIGNAL_A_INDEX][3] - _OHLC[1][3]) / _OHLC[1][3] == pytest.approx(-0.02)

    def test_signal_a_window_1_outcome(self):
        entry = _OHLC[_SIGNAL_A_ENTRY_INDEX][0]  # open
        outcome_close = _OHLC[4][3]
        assert (outcome_close - entry) / entry == pytest.approx(0.030927835, abs=1e-6)

    def test_signal_a_window_2_outcome(self):
        entry = _OHLC[_SIGNAL_A_ENTRY_INDEX][0]
        outcome_close = _OHLC[5][3]
        assert (outcome_close - entry) / entry == pytest.approx(0.041237113, abs=1e-6)

    def test_signal_b_window_1_outcome(self):
        entry = _OHLC[_SIGNAL_B_ENTRY_INDEX][0]
        outcome_close = _OHLC[10][3]
        assert (outcome_close - entry) / entry == pytest.approx(-0.002061856, abs=1e-6)

    def test_signal_b_window_2_outcome(self):
        entry = _OHLC[_SIGNAL_B_ENTRY_INDEX][0]
        outcome_close = _OHLC[11][3]
        assert (outcome_close - entry) / entry == pytest.approx(-0.041237113, abs=1e-6)


class TestSignalGeneration:
    def test_finds_exactly_the_two_built_in_signals(self):
        signals, _ = _run()
        assert len(signals) == 2

    def test_signal_fields_reference_bar_t_but_entry_fields_reference_bar_t_plus_1(self):
        signals, _ = _run()
        bars = _bars()
        signal_a = signals[0]

        assert signal_a.backtest_id == "bt-1"
        assert signal_a.experiment_id == "exp-1"
        assert signal_a.symbol == "TSLA"
        assert signal_a.timeframe == "5m"
        assert signal_a.signal_timestamp == bars[_SIGNAL_A_INDEX].timestamp
        assert signal_a.entry_timestamp == bars[_SIGNAL_A_ENTRY_INDEX].timestamp
        assert signal_a.feature_values == {"price.return_5m": pytest.approx(-0.02)}

    def test_entry_price_is_the_next_bars_open_not_the_signal_bars_close(self):
        """The core no-look-ahead mechanism: entry_price must be
        bars[t+1].open (97.0), never bars[t].close (98.0) -- the two
        are deliberately different values in this fixture so a bug
        that entered at the signal bar's own close cannot pass by
        coincidence."""
        signals, _ = _run()
        bars = _bars()

        assert signals[0].entry_price == bars[_SIGNAL_A_ENTRY_INDEX].open
        assert signals[0].entry_price == pytest.approx(97.0)
        assert signals[0].entry_price != bars[_SIGNAL_A_INDEX].close

    def test_signals_appear_in_chronological_order(self):
        signals, _ = _run()
        assert [s.signal_timestamp for s in signals] == sorted(s.signal_timestamp for s in signals)
        assert signals[0].signal_timestamp < signals[1].signal_timestamp


class TestWindowOutcomes:
    def test_signal_a_has_one_outcome_per_configured_window(self):
        signals, _ = _run()
        signal_a = signals[0]
        assert [o.window_bars for o in signal_a.outcomes] == [1, 2]

    def test_signal_a_window_1_forward_return_mfe_mae(self):
        signals, _ = _run()
        bars = _bars()
        outcome = signals[0].outcomes[0]

        assert outcome.window_bars == 1
        assert outcome.outcome_timestamp == bars[4].timestamp
        assert outcome.forward_return == pytest.approx(0.030927835, abs=1e-6)
        assert outcome.mfe == pytest.approx(0.041237113, abs=1e-6)  # bar 4's high (101) vs entry (97)
        assert outcome.mae == pytest.approx(-0.010309278, abs=1e-6)  # bar 3's low (96) vs entry (97)

    def test_signal_a_window_2_forward_return_mfe_mae(self):
        signals, _ = _run()
        bars = _bars()
        outcome = signals[0].outcomes[1]

        assert outcome.window_bars == 2
        assert outcome.outcome_timestamp == bars[5].timestamp
        assert outcome.forward_return == pytest.approx(0.041237113, abs=1e-6)
        assert outcome.mfe == pytest.approx(0.051546392, abs=1e-6)  # bar 5's high (102) vs entry (97)
        assert outcome.mae == pytest.approx(-0.010309278, abs=1e-6)  # still bar 3's low

    def test_signal_b_window_1_forward_return_mfe_mae(self):
        signals, _ = _run()
        bars = _bars()
        outcome = signals[1].outcomes[0]

        assert outcome.outcome_timestamp == bars[10].timestamp
        assert outcome.forward_return == pytest.approx(-0.002061856, abs=1e-6)
        assert outcome.mfe == pytest.approx(0.020618557, abs=1e-6)  # bar 9's high (99) vs entry (97)
        assert outcome.mae == pytest.approx(-0.020618557, abs=1e-6)  # bar 10's low (95) vs entry (97)

    def test_signal_b_window_2_forward_return_mfe_mae(self):
        signals, _ = _run()
        bars = _bars()
        outcome = signals[1].outcomes[1]

        assert outcome.outcome_timestamp == bars[11].timestamp
        assert outcome.forward_return == pytest.approx(-0.041237113, abs=1e-6)
        assert outcome.mfe == pytest.approx(0.020618557, abs=1e-6)
        assert outcome.mae == pytest.approx(-0.072164948, abs=1e-6)  # bar 11's low (90) vs entry (97)

    def test_a_window_too_close_to_the_end_of_the_dataset_is_simply_absent(self):
        """Truncating right after signal B's own entry bar (no forward
        bars at all) leaves signal B with zero measurable windows -> no
        BacktestSignal for it at all, while signal A (fully contained)
        is untouched."""
        truncated = _bars(_OHLC[: _SIGNAL_B_ENTRY_INDEX + 1])  # ends exactly at signal B's entry bar

        signals, _ = _run(bars=truncated)

        assert len(signals) == 1
        assert signals[0].signal_timestamp == truncated[_SIGNAL_A_INDEX].timestamp

    def test_a_signal_with_only_some_windows_measurable_keeps_the_measurable_ones(self):
        """Truncating right after signal B's window=1 outcome bar (index
        10) leaves window=1 measurable but window=2 (needs index 11)
        not -- signal B must still be created, with exactly one
        outcome."""
        truncated = _bars(_OHLC[:11])  # indices 0..10, window=2's outcome bar (11) does not exist

        signals, _ = _run(bars=truncated)

        assert len(signals) == 2
        signal_b = signals[1]
        assert [o.window_bars for o in signal_b.outcomes] == [1]


class TestNoLookAheadPrevention:
    def test_a_signal_on_the_last_bar_produces_no_event(self):
        """Bar 11's own return_5m (-3.93%) satisfies the condition, but
        it is the LAST bar in the dataset -- there is no bar 12 to enter
        at. No event may ever be fabricated for it."""
        signals, _ = _run()
        bars = _bars()

        assert bars[11].timestamp not in {s.signal_timestamp for s in signals}

    def test_perturbing_bars_after_a_signals_outcome_window_does_not_change_that_signal(self):
        """Signal A's condition/entry/outcomes only ever depend on bars
        1-5. Replacing everything from index 6 onward with wildly
        different OHLC must not change signal A's computed fields at
        all."""
        perturbed = list(_OHLC)
        for i in range(6, len(perturbed)):
            perturbed[i] = (999.0, 1234.0, 111.0, 555.0)

        original_signals, _ = _run()
        perturbed_signals, _ = _run(bars=_bars(perturbed))

        original_a, perturbed_a = original_signals[0], perturbed_signals[0]
        assert original_a.feature_values == perturbed_a.feature_values
        assert original_a.entry_price == perturbed_a.entry_price
        assert original_a.outcomes == perturbed_a.outcomes

    def test_truncating_the_dataset_right_after_a_signals_outcome_reproduces_it_identically(self):
        """Truncating to end immediately after signal A's own window=2
        outcome bar (index 5) must reproduce signal A identically --
        proving nothing beyond that point was ever read to compute it."""
        full_signals, _ = _run()
        truncated_signals, _ = _run(bars=_bars(_OHLC[:6]))

        assert truncated_signals == [full_signals[0]]

    def test_evaluating_the_condition_never_reads_a_later_bars_feature_value(self):
        """A bar with no matching FeatureRecord at all is skipped, not
        treated as an eligible-but-undefined observation -- confirmed
        by dropping signal A's own FeatureRecord and observing only
        signal B survives."""
        bars = _bars()
        features = _features(bars)
        features_missing_signal_a = [f for f in features if f.timestamp != bars[_SIGNAL_A_INDEX].timestamp]

        signals, _ = _run(bars=bars, feature_records=features_missing_signal_a)

        assert len(signals) == 1
        assert signals[0].signal_timestamp == bars[_SIGNAL_B_INDEX].timestamp


class TestFeatureContractVersionReproducibility:
    def test_a_feature_record_under_a_different_contract_version_is_ignored(self):
        bars = _bars()
        mismatched = _features(bars, feature_contract_version="v2-not-yet-real")

        signals, results = _run(bars=bars, feature_records=mismatched, feature_contract_version=FEATURE_CONTRACT_VERSION)

        assert signals == []
        assert all(w.signal_count == 0 for w in results.windows)

    def test_matching_the_backtests_own_version_explicitly_still_works(self):
        bars = _bars()
        features = _features(bars, feature_contract_version="v2-not-yet-real")

        signals, _ = _run(bars=bars, feature_records=features, feature_contract_version="v2-not-yet-real")

        assert len(signals) == 2


class TestZeroSignalConditions:
    def test_a_condition_that_never_fires_produces_no_signals_and_explicit_nones(self):
        flat_bars = _bars([(100.0, 100.0, 100.0, 100.0)] * 10)

        signals, results = _run(bars=flat_bars)

        assert signals == []
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


class TestAggregateResults:
    def test_per_window_signal_count_and_win_rate(self):
        _, results = _run()
        window_1, window_2 = results.windows

        assert window_1.window_bars == 1
        assert window_1.signal_count == 2
        assert window_1.win_count == 1  # signal A wins, signal B loses
        assert window_1.win_rate == pytest.approx(0.5)

        assert window_2.window_bars == 2
        assert window_2.signal_count == 2
        assert window_2.win_count == 1
        assert window_2.win_rate == pytest.approx(0.5)

    def test_mean_median_stdev_best_worst_match_stdlib_over_the_two_returns(self):
        signals, results = _run()
        window_1_returns = [s.outcomes[0].forward_return for s in signals]

        window_1 = results.windows[0]
        assert window_1.mean_return == pytest.approx(pystatistics.mean(window_1_returns))
        assert window_1.median_return == pytest.approx(pystatistics.median(window_1_returns))
        assert window_1.std_dev_return == pytest.approx(pystatistics.stdev(window_1_returns))
        assert window_1.best_return == pytest.approx(max(window_1_returns))
        assert window_1.worst_return == pytest.approx(min(window_1_returns))

    def test_mean_mfe_mean_mae_match_the_two_signals_own_values(self):
        signals, results = _run()
        window_1 = results.windows[0]
        mfes = [s.outcomes[0].mfe for s in signals]
        maes = [s.outcomes[0].mae for s in signals]

        assert window_1.mean_mfe == pytest.approx(pystatistics.mean(mfes))
        assert window_1.mean_mae == pytest.approx(pystatistics.mean(maes))

    def test_a_single_signal_window_has_no_std_dev_but_every_other_statistic(self):
        """Truncate to leave only signal A measurable at all -- one
        signal, one outcome per window -- std_dev must be None
        (undefined for a single observation), never a crash or a
        fabricated 0.0."""
        truncated = _bars(_OHLC[:6])  # signal A + both its outcome bars, nothing else

        _, results = _run(bars=truncated)

        for window_results in results.windows:
            assert window_results.signal_count == 1
            assert window_results.std_dev_return is None
            assert window_results.mean_return is not None
            assert window_results.best_return == window_results.worst_return == window_results.mean_return

    def test_windows_with_no_measurable_outcomes_still_appear_in_results_explicitly(self):
        """A window far larger than this fixture's dataset can ever
        satisfy still gets its own BacktestWindowResults entry, with
        every statistic None -- never silently dropped from the list."""
        _, results = _run(windows=[1, 500])

        assert [w.window_bars for w in results.windows] == [1, 500]
        huge_window = results.windows[1]
        assert huge_window.signal_count == 0
        assert huge_window.win_rate is None


class TestReproducibility:
    def test_running_the_same_inputs_twice_produces_identical_output(self):
        first_signals, first_results = _run()
        second_signals, second_results = _run()

        assert first_signals == second_signals
        assert first_results == second_results

    def test_repeated_calls_are_side_effect_free(self):
        bars = _bars()
        feature_records = _features(bars)
        bars_snapshot = list(bars)
        features_snapshot = list(feature_records)

        for _ in range(3):
            run_backtest(
                backtest_id="bt-1",
                experiment_id="exp-1",
                symbol="TSLA",
                timeframe="5m",
                conditions=_CONDITIONS,
                windows=_WINDOWS,
                bars=bars,
                feature_records=feature_records,
                feature_contract_version=FEATURE_CONTRACT_VERSION,
            )

        assert bars == bars_snapshot
        assert feature_records == features_snapshot
