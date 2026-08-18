"""Condition/outcome operator evaluation for Research v1 (app/research/).

Two distinct jobs, kept in one file because both answer the same
underlying question ("does this number satisfy an operator against a
target?") for the app's two different operator vocabularies:

  - evaluate() -- Outcome's five comparisons (ConditionOperator,
    app/models/research.py), applied to a computed forward_return
    value. Unchanged by v0.1.24 (see that module's docstring).
  - evaluate_feature_conditions() (v0.1.24) -- a list of ANDed
    FeatureConditions, each evaluated against an already-computed
    FeatureRecord via app/features/vocabulary.py::get_feature_value().
    This is the "Do NOT recalculate features inside Research" boundary
    in code: this function never touches a HistoricalBar, only values
    already sitting on a FeatureRecord.
"""

from app.features.vocabulary import get_feature_value
from app.models.features import FeatureRecord
from app.models.research import FeatureCondition

# "=" (FeatureConditionOperator's single-equals) is a deliberate alias
# for "==" (ConditionOperator's), not a second, differently-behaving
# comparison -- both mean the same Python `==`. Keeping both keys in
# one dict (rather than translating "=" -> "==" at each call site) is
# what lets evaluate() serve both operator vocabularies without one
# needing to know about the other's spelling.
_OPERATORS = {
    "<": lambda value, target: value < target,
    "<=": lambda value, target: value <= target,
    "==": lambda value, target: value == target,
    "=": lambda value, target: value == target,
    ">=": lambda value, target: value >= target,
    ">": lambda value, target: value > target,
}


def evaluate(value: float, operator: str, threshold: float) -> bool:
    """Whether `value` satisfies `operator` `threshold`, e.g.
    evaluate(-0.012, "<=", -0.01) -> True. Raises ValueError for an
    operator outside what this app supports -- see Outcome's
    `operator` field (app/models/research.py), which is typed as
    ConditionOperator so an unsupported string can never reach here
    from a validated model in the first place; this check exists for
    direct callers of this function (tests, any future caller)."""
    try:
        comparator = _OPERATORS[operator]
    except KeyError:
        raise ValueError(f"Unsupported operator {operator!r}. Allowed: {sorted(_OPERATORS)}") from None
    return comparator(value, threshold)


def evaluate_feature_conditions(
    conditions: list[FeatureCondition], feature_record: FeatureRecord
) -> dict[str, float | bool] | None:
    """Evaluates every condition in `conditions` (AND-combined) against
    one FeatureRecord. Returns feature_id -> the actual observed value
    for EVERY condition, if ALL are true -- or None the moment any
    single condition is false, OR the referenced feature's value is
    itself None at this record (insufficient history, a missing bar, a
    symbol not configured for market context -- see get_feature_value()'s
    own docstring). A None feature value can never satisfy any
    operator, the same "an undefined observation is not an eligible
    signal" rule app/research/engine.py's old bar-based condition
    evaluation already followed -- extended here to feature values
    instead of raw trailing returns.

    Never returns a PARTIAL dict: either every condition fired (a full
    dict, one entry per condition) or none of this record's condition
    values are returned at all -- a caller must never mistake "some
    conditions fired" for "the AND fired".
    """
    values: dict[str, float | bool] = {}
    for condition in conditions:
        value = get_feature_value(feature_record, condition.feature_id)
        if value is None:
            return None
        if not _evaluate_feature_condition(value, condition):
            return None
        values[condition.feature_id] = value
    return values


def _evaluate_feature_condition(value: float | int | bool, condition: FeatureCondition) -> bool:
    if condition.operator.value == "between":
        return condition.value <= value <= condition.value_max
    return evaluate(value, condition.operator.value, condition.value)
