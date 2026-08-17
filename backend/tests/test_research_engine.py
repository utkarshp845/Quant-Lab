"""Tests for app/research/engine.py::run_experiment() -- the pure
condition -> signal -> outcome -> aggregate pipeline, against synthetic,
deterministic HistoricalBar data (never live market data, per this
feature's spec section 9). No database, no HTTP, no provider involved
anywhere in this file.

Fixture shape, used throughout: 5-minute bars, a "5m_return <= -1%"
condition (window = 1 bar -- a single bar-to-bar step) and a
"forward_return, 15m horizon, <= -0.5%" outcome (window = 3 bars).
A 1-bar condition window is deliberate here (as opposed to the spec
example's 30-minute window, exercised end to end in
tests/test_research_api.py::TestExampleExperiment instead): it makes
every value in the fixture hand-computable from its two immediate
neighbors, with no risk of a declining/recovering run of bars
triggering the condition at more than one index by accident.

Two isolated, hand-computable signals are built into the series:

  SIGNAL A (index 2): a sharp drop, followed by continued gentle
  decline -> outcome satisfies the threshold -> SUCCESS.
  SIGNAL B (index 8): a sharp drop, followed by a gentle recovery ->
  outcome does not satisfy the threshold -> FAILURE.

Every other bar-to-bar step stays inside the threshold on purpose, so
run_experiment() finds exactly these two signals -- see _CLOSES below
for the exact values and TestConditionAndOutcomeMath for the
hand-verified arithmetic.
"""

import statistics as pystatistics
from datetime import datetime, timedelta, timezone

import pytest

from app.models.market_data import HistoricalBar
from app.models.research import Condition, ConditionOperator, Outcome
from app.research.engine import run_experiment

_CONDITION = Condition(metric="5m_return", operator=ConditionOperator.LTE, threshold=-0.01)
_OUTCOME = Outcome(metric="forward_return", horizon_minutes=15, operator=ConditionOperator.LTE, threshold=-0.005)

# One value per 5-minute bar, index 0..13. See the module docstring for
# how this was constructed: two flat bars (so the 1-bar condition
# window has no data before index 1 -- the very first eligible
# observation is index 1, itself not a signal), a sharp drop at index 2
# (SIGNAL A) followed by a gentle continued decline, a reset back to a
# flat baseline, then a second sharp drop at index 8 (SIGNAL B)
# followed by a gentle recovery.
_CLOSES = [
    100.00,  # 0
    100.00,  # 1
    98.00,  # 2  <- SIGNAL A: (98.00 - 100.00) / 100.00 = -2.00%
    97.90,  # 3
    97.80,  # 4
    97.00,  # 5  <- SIGNAL A's outcome bar (index 2 + 3): (97.00 - 98.00) / 98.00 = -1.0204%
    100.00,  # 6 <- reset back to baseline
    100.00,  # 7
    98.00,  # 8  <- SIGNAL B: (98.00 - 100.00) / 100.00 = -2.00%
    98.50,  # 9
    99.00,  # 10
    99.80,  # 11 <- SIGNAL B's outcome bar (index 8 + 3): (99.80 - 98.00) / 98.00 = +1.8367%
    100.20,  # 12
    100.40,  # 13
]

_SIGNAL_A_INDEX = 2
_SIGNAL_A_OUTCOME_INDEX = 5
_SIGNAL_B_INDEX = 8
_SIGNAL_B_OUTCOME_INDEX = 11


def _bars(closes: list[float] = _CLOSES, *, timeframe="5m") -> list[HistoricalBar]:
    base = datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc)
    return [
        HistoricalBar(
            symbol="TSLA",
            timestamp=base + timedelta(minutes=5 * i),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1_000,
            provider="csv",
            timeframe=timeframe,
        )
        for i, close in enumerate(closes)
    ]


def _run(bars=None, condition=_CONDITION, outcome=_OUTCOME, timeframe="5m"):
    return run_experiment(
        experiment_id="exp-1",
        symbol="TSLA",
        timeframe=timeframe,
        condition=condition,
        outcome=outcome,
        bars=bars if bars is not None else _bars(),
    )


class TestConditionAndOutcomeMath:
    """Hand-verified arithmetic for the two signals baked into _CLOSES
    -- if this fails, the fixture itself no longer means what the
    module docstring says it does."""

    def test_signal_a_condition_value(self):
        assert (_CLOSES[_SIGNAL_A_INDEX] - _CLOSES[1]) / _CLOSES[1] == pytest.approx(-0.02)

    def test_signal_a_outcome_value(self):
        base = _CLOSES[_SIGNAL_A_INDEX]
        outcome = _CLOSES[_SIGNAL_A_OUTCOME_INDEX]
        assert (outcome - base) / base == pytest.approx(-0.0102040816, abs=1e-6)

    def test_signal_b_condition_value(self):
        assert (_CLOSES[_SIGNAL_B_INDEX] - _CLOSES[7]) / _CLOSES[7] == pytest.approx(-0.02)

    def test_signal_b_outcome_value(self):
        base = _CLOSES[_SIGNAL_B_INDEX]
        outcome = _CLOSES[_SIGNAL_B_OUTCOME_INDEX]
        assert (outcome - base) / base == pytest.approx(0.0183673469, abs=1e-6)


class TestSignalEventCreation:
    def test_finds_exactly_the_two_built_in_signals(self):
        events, _ = _run()
        assert len(events) == 2

    def test_no_signal_before_the_condition_window_has_enough_trailing_bars(self):
        """Bar 0 cannot have a 1-bar trailing window (index - 1 < 0) --
        confirmed indirectly: the earliest possible signal is bar 1,
        and bar 1 -> bar 0 is a flat 0% return anyway, so the first
        real event is bar 2."""
        events, _ = _run()
        bars = _bars()
        assert events[0].signal_timestamp == bars[_SIGNAL_A_INDEX].timestamp

    def test_event_fields_match_the_signal_bar(self):
        events, _ = _run()
        bars = _bars()
        signal_a = events[0]

        assert signal_a.experiment_id == "exp-1"
        assert signal_a.symbol == "TSLA"
        assert signal_a.signal_timestamp == bars[_SIGNAL_A_INDEX].timestamp
        assert signal_a.signal_price == bars[_SIGNAL_A_INDEX].close
        assert signal_a.condition_value == pytest.approx(-0.02)


class TestOutcomeCalculation:
    def test_outcome_fields_match_the_forward_bar(self):
        events, _ = _run()
        bars = _bars()
        signal_a = events[0]

        assert signal_a.outcome_timestamp == bars[_SIGNAL_A_OUTCOME_INDEX].timestamp
        assert signal_a.outcome_price == bars[_SIGNAL_A_OUTCOME_INDEX].close
        assert signal_a.outcome_value == pytest.approx(-0.0102040816, abs=1e-6)

    def test_forward_window_calculation_uses_the_correct_bar_count(self):
        """15-minute horizon on 5-minute bars is 3 bars forward -- not
        2, not 4. Assert against off-by-one horizons to pin the exact
        offset used."""
        events, _ = _run()
        bars = _bars()
        signal_a = events[0]

        assert signal_a.outcome_timestamp != bars[_SIGNAL_A_INDEX + 2].timestamp
        assert signal_a.outcome_timestamp != bars[_SIGNAL_A_INDEX + 4].timestamp
        assert signal_a.outcome_timestamp == bars[_SIGNAL_A_INDEX + 3].timestamp

    def test_a_signal_too_close_to_the_end_of_the_dataset_produces_no_event(self):
        """The last 3 bars can never have a 3-bar-forward outcome
        within this dataset -- confirmed by truncating right after
        signal B's own trigger bar, before its outcome bar exists."""
        truncated = _bars(_CLOSES[: _SIGNAL_B_INDEX + 1])  # ends exactly at the signal B bar

        events, _ = _run(bars=truncated)

        # Signal A (fully contained) is still found; signal B (whose
        # outcome bar was truncated away) produces no event at all.
        assert len(events) == 1
        assert events[0].signal_timestamp == truncated[_SIGNAL_A_INDEX].timestamp


class TestSuccessFailureClassification:
    def test_continued_decline_is_a_success(self):
        events, _ = _run()
        signal_a = events[0]
        assert signal_a.success is True

    def test_reversal_is_a_failure(self):
        events, _ = _run()
        signal_b = events[1]
        assert signal_b.success is False


class TestAggregateStatistics:
    def test_totals_and_success_rate(self):
        _, results = _run()

        assert results.total_events == 2
        assert results.successful_events == 1
        assert results.failed_events == 1
        assert results.success_rate == pytest.approx(0.5)

    def test_average_median_min_max_match_the_two_outcome_values(self):
        events, results = _run()
        outcome_values = [e.outcome_value for e in events]

        assert results.average_outcome == pytest.approx(pystatistics.mean(outcome_values))
        assert results.median_outcome == pytest.approx(pystatistics.median(outcome_values))
        assert results.min_outcome == pytest.approx(min(outcome_values))
        assert results.max_outcome == pytest.approx(max(outcome_values))

    def test_std_dev_matches_the_stdlib_sample_stdev(self):
        events, results = _run()
        outcome_values = [e.outcome_value for e in events]

        assert results.std_dev_outcome == pytest.approx(pystatistics.stdev(outcome_values))

    def test_zero_events_yields_explicit_nones_not_misleading_zeros(self):
        """A dataset where the condition never fires -- every statistic
        must be None, not 0.0 or a ZeroDivisionError."""
        flat_bars = _bars([100.0] * 10)

        events, results = _run(bars=flat_bars)

        assert events == []
        assert results.total_events == 0
        assert results.successful_events == 0
        assert results.failed_events == 0
        assert results.success_rate is None
        assert results.average_outcome is None
        assert results.median_outcome is None
        assert results.min_outcome is None
        assert results.max_outcome is None
        assert results.std_dev_outcome is None

    def test_a_single_event_has_no_std_dev_but_does_have_every_other_statistic(self):
        """statistics.stdev is undefined for one observation -- must be
        None, never a StatisticsError or a fabricated 0.0."""
        single_signal_bars = _bars(_CLOSES[: _SIGNAL_A_OUTCOME_INDEX + 1])  # signal A + its outcome bar, nothing past

        events, results = _run(bars=single_signal_bars)

        assert results.total_events == 1
        assert results.std_dev_outcome is None
        assert results.average_outcome is not None
        assert results.min_outcome == results.max_outcome == results.average_outcome


class TestNoLookAheadBehavior:
    def test_perturbing_bars_after_a_signals_outcome_window_does_not_change_that_signal(self):
        """The strongest form of the no-look-ahead guarantee at the
        engine level: signal A's condition and outcome only ever
        depend on bars[1] (its trailing base) and bars[2]/bars[5] (its
        signal/outcome bars). Replacing everything from index 6
        onward with wildly different values must not change signal A's
        computed fields at all -- only what happens to signal B (whose
        data WAS changed) is allowed to differ.
        """
        perturbed_closes = list(_CLOSES)
        for i in range(6, len(perturbed_closes)):
            perturbed_closes[i] = 12345.0  # a wildly different, unrelated future value

        original_events, _ = _run()
        perturbed_events, _ = _run(bars=_bars(perturbed_closes))

        original_signal_a = original_events[0]
        perturbed_signal_a = perturbed_events[0]

        assert original_signal_a.condition_value == perturbed_signal_a.condition_value
        assert original_signal_a.signal_price == perturbed_signal_a.signal_price
        assert original_signal_a.outcome_value == perturbed_signal_a.outcome_value
        assert original_signal_a.outcome_price == perturbed_signal_a.outcome_price
        assert original_signal_a.success == perturbed_signal_a.success

    def test_evaluating_at_an_earlier_index_never_consults_a_later_bar(self):
        """Truncating the dataset to end immediately after signal A's
        own outcome bar must reproduce signal A identically -- proving
        nothing beyond that point was ever read to compute it."""
        full_events, _ = _run()
        truncated_events, _ = _run(bars=_bars(_CLOSES[: _SIGNAL_A_OUTCOME_INDEX + 1]))

        assert truncated_events == [full_events[0]]


class TestReproducibility:
    def test_running_the_same_inputs_twice_produces_identical_output(self):
        first_events, first_results = _run()
        second_events, second_results = _run()

        assert first_events == second_events
        assert first_results == second_results

    def test_repeated_calls_are_side_effect_free(self):
        """run_experiment() is pure -- calling it three times against
        the exact same arguments (including the same list object) must
        not mutate `bars` or accumulate state anywhere."""
        bars = _bars()
        bars_snapshot = list(bars)

        for _ in range(3):
            run_experiment(
                experiment_id="exp-1",
                symbol="TSLA",
                timeframe="5m",
                condition=_CONDITION,
                outcome=_OUTCOME,
                bars=bars,
            )

        assert bars == bars_snapshot
