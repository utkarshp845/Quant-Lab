"""Research v1's experiment engine (app/research/) -- turns an
Experiment's condition/outcome definition into ExperimentEvent rows and
aggregate ExperimentResults, given the ALREADY-QUERIED, normalized bar
series for its symbol/timeframe/date-range (see
app/storage/historical_bar_repository.get_bars()).

Deliberately pure: bars + condition + outcome in, events + results out
-- no I/O, no repository calls, no HTTP concerns of its own. That is
what makes run_experiment() trivially unit-testable against synthetic
in-memory bars (tests/test_research_engine.py), with no database, no
provider, and no network call anywhere in the loop. app/api/research.py
is the only caller that fetches bars (via historical_bar_repository)
and persists what this returns (via app/storage/research_repository.py).
"""

from app.models.market_data import HistoricalBar
from app.models.research import Condition, ExperimentEvent, ExperimentResults, Outcome
from app.research import metrics
from app.research.aggregation import aggregate_results
from app.research.conditions import evaluate as evaluate_operator


def run_experiment(
    *,
    experiment_id: str,
    symbol: str,
    timeframe: str,
    condition: Condition,
    outcome: Outcome,
    bars: list[HistoricalBar],
) -> tuple[list[ExperimentEvent], ExperimentResults]:
    """Walks `bars` (must already be ordered oldest-first, exactly what
    historical_bar_repository.get_bars() returns) once, forward, and at
    each index:

      1. Computes the condition metric using ONLY bars up to and
         including that index (app/research/metrics.py::trailing_return)
         -- this is the entire no-look-ahead guarantee: nothing later
         in the loop ever looks backward past `index` at a bar that
         wasn't computed with the same restriction.
      2. Skips the observation (no signal, no event) if the metric
         can't be computed yet (not enough trailing bars) or the
         condition is false there.
      3. When the condition is true, computes the forward outcome
         (app/research/metrics.py::forward_return) -- this step alone
         is deliberately look-ahead, since "the outcome" means what
         happened AFTER the signal.
      4. Skips creating an event if the forward window falls outside
         the queried dataset (the signal is too near the end of the
         requested date range to measure): an event's outcome fields
         are never optional or fabricated, so if it can't be measured,
         no event exists for that signal.
      5. Otherwise records one ExperimentEvent with the numeric outcome
         and whether it satisfied the outcome condition.

    Bars are assumed pre-filtered to the requested symbol/timeframe/
    date-range by the caller (see historical_bar_repository.get_bars())
    -- this function does not re-filter by symbol or date itself, it
    only trusts what it's given, consistent with the storage layer
    already being the one place that queries the dataset boundary.
    """
    condition_window_bars = metrics.bars_for_window(metrics.parse_trailing_return_metric(condition.metric), timeframe)
    outcome_window_bars = metrics.bars_for_window(outcome.horizon_minutes, timeframe)

    events: list[ExperimentEvent] = []
    for index, bar in enumerate(bars):
        condition_value = metrics.trailing_return(bars, index, condition_window_bars)
        if condition_value is None:
            continue  # not enough trailing history yet -- not an eligible observation
        if not evaluate_operator(condition_value, condition.operator.value, condition.threshold):
            continue  # condition false at this observation -- no signal

        forward = metrics.forward_return(bars, index, outcome_window_bars)
        if forward is None:
            continue  # signal too close to the end of the dataset to measure its outcome
        outcome_value, outcome_bar = forward

        success = evaluate_operator(outcome_value, outcome.operator.value, outcome.threshold)

        events.append(
            ExperimentEvent(
                experiment_id=experiment_id,
                symbol=symbol,
                signal_timestamp=bar.timestamp,
                signal_price=bar.close,
                condition_value=condition_value,
                outcome_timestamp=outcome_bar.timestamp,
                outcome_price=outcome_bar.close,
                outcome_value=outcome_value,
                success=success,
            )
        )

    results = aggregate_results(events)
    return events, results
