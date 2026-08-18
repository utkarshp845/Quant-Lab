"""Backtesting v1's core engine -- turns an already-created Research
Experiment's conditions, plus the ALREADY-QUERIED, normalized bar series
(app/storage/historical_bar_repository.get_bars()) and the
ALREADY-COMPUTED feature series (app/storage/feature_repository.get_features())
for its symbol/timeframe/date-range, into BacktestSignal rows and
aggregate BacktestResults.

Answers exactly one question: "when this research condition occurred
historically, what happened afterward?" -- nothing here sizes a
position, tracks capital, or simulates an order book; see the module
docstring on app/models/backtesting.py for the full scope statement and
what Backtesting v1 deliberately does not do.

WINDOWS ARE BAR COUNTS, NOT MINUTES -- a deliberate departure from
Research's Outcome.horizon_minutes. Outcome needed minutes because it
is defined once, e­arly, on the Experiment itself, independent of which
timeframe eventually runs against it; a Backtest, by contrast, is
created FROM an already-timeframe-bound Experiment, so "60 bars
forward" is already an unambiguous, timeframe-agnostic instruction with
no unit-conversion step required (contrast
app/research/metrics.py::bars_for_window(), which exists purely to
convert Outcome's minutes into a bar count in the first place). Fewer
moving parts, and no risk of a horizon that silently doesn't land on a
bar boundary -- that failure mode simply cannot occur here.

NO-LOOK-AHEAD, spelled out precisely (three separate rules, all
enforced in one forward pass):

  1. A signal at bar t may only be evaluated using bar t's own,
     ALREADY-COMPUTED FeatureRecord (itself computed from bar t's own
     trailing history only -- a guarantee Feature Engine v1 already
     provides and this engine never re-derives). Condition evaluation
     is REUSED verbatim from app/research/conditions.py::
     evaluate_feature_conditions() -- this module contains no feature
     math and no condition-matching logic of its own, per this
     feature's own "do not duplicate existing feature calculations or
     research-condition logic" requirement.
  2. The signal is never acted on at bar t's own close. Entry happens
     at bar t+1's OPEN -- the first price actually available after the
     bar whose close made the condition true is fully known. A
     condition firing on the dataset's last bar produces no signal at
     all (there is no t+1 to enter at within this dataset).
  3. Every forward-window outcome (forward_return/MFE/MAE) is computed
     ONLY from bars at or after the entry bar, up to and including the
     window's own outcome bar -- never a bar before entry, and never a
     bar beyond what the window's own horizon requires. A window whose
     outcome bar falls outside the queried dataset is simply absent
     from that signal's outcomes (see BacktestSignal's own docstring) --
     never estimated, interpolated, or borrowed from a shorter window.

Deliberately pure: bars + feature_records + conditions + windows in,
signals + results out -- no I/O, no repository calls, no HTTP concerns,
mirroring app/research/engine.py::run_experiment()'s own "trivially
unit-testable against synthetic in-memory data" design (see
tests/test_backtest_engine.py). app/api/backtesting.py is the only
caller that fetches bars/features/the referenced Experiment and
persists what this returns.
"""

from app.backtesting.aggregation import aggregate_results
from app.models.backtesting import BacktestSignal, BacktestWindowOutcome
from app.models.features import FeatureRecord
from app.models.market_data import HistoricalBar
from app.models.research import FeatureCondition
from app.research.conditions import evaluate_feature_conditions


def run_backtest(
    *,
    backtest_id: str,
    experiment_id: str,
    symbol: str,
    timeframe: str,
    conditions: list[FeatureCondition],
    windows: list[int],
    bars: list[HistoricalBar],
    feature_records: list[FeatureRecord],
    feature_contract_version: str,
):
    """Walks `bars` (must already be ordered oldest-first, exactly what
    historical_bar_repository.get_bars() returns) once, forward, and at
    each index t:

      1. Looks up the FeatureRecord whose timestamp EXACTLY matches
         bar t's, among `feature_records` filtered to ones whose own
         `feature_contract_version` matches `feature_contract_version`
         (this Backtest's stored version -- captured from the
         Experiment at creation time, see app/models/backtesting.py::
         Backtest's own docstring). No match means this bar is not an
         eligible observation at all -- skipped, exactly like
         app/research/engine.py already treats a bar with no
         version-matching feature data.
      2. Evaluates `conditions` (AND-combined) against that
         FeatureRecord via app/research/conditions.py::
         evaluate_feature_conditions() -- the SAME function Research
         uses, not a reimplementation. No signal if any condition is
         false or any referenced feature's value is itself undefined
         there.
      3. When every condition is true, the entry bar is bars[t + 1] --
         skipped entirely (no signal produced) if that index does not
         exist in `bars`, or if its open is exactly 0 (an entry price
         of 0 has no honest percentage-return math, the same guard
         app/research/metrics.py::forward_return() already applies to
         a zero signal price).
      4. For each configured window (bars forward from the ENTRY bar,
         inclusive of both ends -- see _window_outcome() below), computes
         a BacktestWindowOutcome if the window's own outcome bar exists
         in `bars`; a window whose outcome bar would fall past the end
         of the dataset is simply omitted from this signal's outcomes,
         never fabricated.
      5. If NOT ONE window produced a measurable outcome, no
         BacktestSignal is created at all for bar t -- a signal with an
         entirely empty outcomes list would carry no inspectable
         information and cannot contribute to any aggregate.

    Returns (signals, results) -- results has one BacktestWindowResults
    per entry in `windows`, in that same order, even for a window that
    ended up with zero measurable signals across the whole dataset (see
    app/backtesting/aggregation.py).
    """
    features_by_timestamp = {
        record.timestamp: record for record in feature_records if record.feature_contract_version == feature_contract_version
    }

    signals: list[BacktestSignal] = []
    for index, bar in enumerate(bars):
        feature_record = features_by_timestamp.get(bar.timestamp)
        if feature_record is None:
            continue  # no version-matching feature data at this bar -- not an eligible observation

        feature_values = evaluate_feature_conditions(conditions, feature_record)
        if feature_values is None:
            continue  # at least one condition false, or a referenced feature value is undefined here -- no signal

        entry_index = index + 1
        if entry_index >= len(bars):
            continue  # bar t is the last bar in the dataset -- no next-bar-open to enter at, no look-ahead substitute allowed
        entry_bar = bars[entry_index]
        entry_price = entry_bar.open
        if entry_price == 0:
            continue  # an entry price of 0 has no honest percentage-return math

        outcomes: list[BacktestWindowOutcome] = []
        for window_bars in windows:
            outcome = _window_outcome(bars, entry_index=entry_index, entry_price=entry_price, window_bars=window_bars)
            if outcome is not None:
                outcomes.append(outcome)

        if not outcomes:
            continue  # every configured window fell outside the dataset from this entry point -- nothing measurable to persist

        signals.append(
            BacktestSignal(
                backtest_id=backtest_id,
                experiment_id=experiment_id,
                symbol=symbol,
                timeframe=timeframe,
                signal_timestamp=bar.timestamp,
                entry_timestamp=entry_bar.timestamp,
                entry_price=entry_price,
                feature_values=feature_values,
                outcomes=outcomes,
            )
        )

    results = aggregate_results(signals, windows)
    return signals, results


def _window_outcome(
    bars: list[HistoricalBar], *, entry_index: int, entry_price: float, window_bars: int
) -> BacktestWindowOutcome | None:
    """One window's outcome from `entry_index` forward `window_bars`
    bars -- None if the outcome bar (entry_index + window_bars) does
    not exist in `bars`. The window spans bars[entry_index] through
    bars[outcome_index] INCLUSIVE (window_bars + 1 bars total): entry
    itself is already the first bar whose high/low genuinely happen
    AFTER the entry decision (we bought at its open; what that same bar
    later does is real, un-look-ahead information), so excluding it
    would silently discard real excursion that already happened at or
    after entry.
    """
    outcome_index = entry_index + window_bars
    if outcome_index >= len(bars):
        return None

    window = bars[entry_index : outcome_index + 1]
    outcome_bar = bars[outcome_index]

    return BacktestWindowOutcome(
        window_bars=window_bars,
        outcome_timestamp=outcome_bar.timestamp,
        forward_return=(outcome_bar.close - entry_price) / entry_price,
        mfe=max((b.high - entry_price) / entry_price for b in window),
        mae=min((b.low - entry_price) / entry_price for b in window),
    )
