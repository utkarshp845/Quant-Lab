"""Pure "how many bars would this candidate's conditions match?" count
(spec section 8: candidate selection may use sample size -- "before
looking at outcome results"). Reuses app/research/conditions.py::
evaluate_feature_conditions() UNMODIFIED, and touches ONLY FeatureRecords
-- never a HistoricalBar, never app.research.metrics.forward_return(),
never an Outcome. That is a structural guarantee, not a UI convention:
this function has no way to compute or even see outcome/success data,
so it cannot leak it into a Design-stage comparison even by accident.
"""

from app.models.features import FeatureRecord
from app.models.research import FeatureCondition
from app.research.conditions import evaluate_feature_conditions


def count_matching_signals(
    conditions: list[FeatureCondition],
    feature_records: list[FeatureRecord],
    feature_contract_version: str,
) -> int:
    """The number of `feature_records` (filtered to `feature_contract_version`,
    the same reproducibility guarantee Research v1's real engine applies)
    whose values satisfy every ANDed condition -- i.e. how many
    qualifying signals this candidate definition would produce, with NO
    outcome ever computed."""
    count = 0
    for record in feature_records:
        if record.feature_contract_version != feature_contract_version:
            continue
        if evaluate_feature_conditions(conditions, record) is not None:
            count += 1
    return count
