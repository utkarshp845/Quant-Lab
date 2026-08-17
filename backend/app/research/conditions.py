"""Shared operator evaluation for Research v1 (app/research/).

Both Condition and Outcome (app/models/research.py) are a
metric/operator/threshold triple, and "does this number satisfy <=,
==, ...?" is the identical question for both -- answered here once,
rather than duplicated at each call site.
"""

_OPERATORS = {
    "<": lambda value, threshold: value < threshold,
    "<=": lambda value, threshold: value <= threshold,
    "==": lambda value, threshold: value == threshold,
    ">=": lambda value, threshold: value >= threshold,
    ">": lambda value, threshold: value > threshold,
}


def evaluate(value: float, operator: str, threshold: float) -> bool:
    """Whether `value` satisfies `operator` `threshold`, e.g.
    evaluate(-0.012, "<=", -0.01) -> True. Raises ValueError for an
    operator outside the five this app supports -- see Condition's and
    Outcome's `operator` fields (app/models/research.py), which are
    typed as ConditionOperator so an unsupported string can never reach
    here from a validated model in the first place; this check exists
    for direct callers of this function (tests, any future caller)."""
    try:
        comparator = _OPERATORS[operator]
    except KeyError:
        raise ValueError(f"Unsupported operator {operator!r}. Allowed: {sorted(_OPERATORS)}") from None
    return comparator(value, threshold)
