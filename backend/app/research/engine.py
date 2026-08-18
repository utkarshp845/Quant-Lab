"""Research v1's experiment engine (app/research/) -- turns an
Experiment's conditions/outcome definition into ExperimentEvent rows and
aggregate ExperimentResults, given the ALREADY-QUERIED, normalized bar
series (app/storage/historical_bar_repository.get_bars()) AND the
ALREADY-COMPUTED feature series (app/storage/feature_repository.get_features())
for its symbol/timeframe/date-range.

v0.1.24 (Feature <-> Research integration): condition evaluation moved
from "recompute a trailing return directly off bars" to "look up an
already-persisted FeatureRecord's value" -- see app/research/
conditions.py::evaluate_feature_conditions() and app/features/
vocabulary.py::get_feature_value(). `bars` is still required: the
OUTCOME side (forward_return) is unchanged and still reads bars
directly, and `signal_price` is still the signal bar's own close. This
engine never calls app/features/engine.py itself -- "Do NOT recalculate
features inside Research" (this integration's own spec) means exactly
that: features arrive here pre-computed, the same way bars always have.

Deliberately pure: bars + feature_records + conditions + outcome in,
events + results out -- no I/O, no repository calls, no HTTP concerns
of its own. That is what makes run_experiment() trivially unit-testable
against synthetic in-memory bars/FeatureRecords
(tests/test_research_engine.py), with no database, no provider, and no
network call anywhere in the loop. app/api/research.py is the only
caller that fetches bars/features (via historical_bar_repository /
feature_repository) and persists what this returns (via
app/storage/research_repository.py).
"""

from app.models.features import FeatureRecord
from app.models.market_data import HistoricalBar
from app.models.research import ExperimentEvent, ExperimentResults, FeatureCondition, Outcome
from app.research import metrics
from app.research.aggregation import aggregate_results
from app.research.conditions import evaluate as evaluate_operator
from app.research.conditions import evaluate_feature_conditions


def run_experiment(
    *,
    experiment_id: str,
    symbol: str,
    timeframe: str,
    conditions: list[FeatureCondition],
    outcome: Outcome,
    bars: list[HistoricalBar],
    feature_records: list[FeatureRecord],
    feature_contract_version: str,
) -> tuple[list[ExperimentEvent], ExperimentResults]:
    """Walks `bars` (must already be ordered oldest-first, exactly what
    historical_bar_repository.get_bars() returns) once, forward, and at
    each index:

      1. Looks up the FeatureRecord whose timestamp EXACTLY matches this
         bar's, among `feature_records` filtered to ones whose own
         `feature_contract_version` matches `feature_contract_version`
         (the experiment's stored version, requirement 6's
         reproducibility guarantee -- see Experiment's own docstring in
         app/models/research.py). No match (a bar with no computed
         features yet, or only features computed under a different
         contract version) means this observation is skipped entirely --
         the same "None/absent means not eligible" rule this engine
         already applied to a not-yet-computable trailing return before
         v0.1.24.
      2. Evaluates every condition (AND-combined,
         app/research/conditions.py::evaluate_feature_conditions())
         against that FeatureRecord. Skips the observation (no signal,
         no event) if any condition is false or any referenced
         feature's value is itself None there.
      3. When every condition is true, computes the forward outcome
         (app/research/metrics.py::forward_return(), reading `bars`
         directly) -- this step alone is deliberately look-ahead, since
         "the outcome" means what happened AFTER the signal.
      4. Skips creating an event if the forward window falls outside
         the queried dataset (the signal is too near the end of the
         requested date range to measure): an event's outcome fields
         are never optional or fabricated, so if it can't be measured,
         no event exists for that signal.
      5. Otherwise records one ExperimentEvent with the numeric outcome,
         every condition's observed value, and whether it satisfied the
         outcome condition.

    `bars` and `feature_records` are assumed pre-filtered to the
    requested symbol/timeframe/provider/date-range by the caller (see
    historical_bar_repository.get_bars() / feature_repository.get_features())
    -- this function does not re-filter by symbol or date itself, it
    only trusts what it's given.
    """
    outcome_window_bars = metrics.bars_for_window(outcome.horizon_minutes, timeframe)
    features_by_timestamp = {
        record.timestamp: record for record in feature_records if record.feature_contract_version == feature_contract_version
    }

    events: list[ExperimentEvent] = []
    for index, bar in enumerate(bars):
        feature_record = features_by_timestamp.get(bar.timestamp)
        if feature_record is None:
            continue  # no version-matching feature data at this bar -- not an eligible observation

        condition_values = evaluate_feature_conditions(conditions, feature_record)
        if condition_values is None:
            continue  # at least one condition false, or a referenced feature value is undefined here -- no signal

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
                condition_values=condition_values,
                outcome_timestamp=outcome_bar.timestamp,
                outcome_price=outcome_bar.close,
                outcome_value=outcome_value,
                success=success,
            )
        )

    results = aggregate_results(events)
    return events, results
