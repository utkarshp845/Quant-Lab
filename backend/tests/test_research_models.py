"""Tests for app/models/research.py -- FeatureCondition/Outcome field
validation and Experiment.new()'s assignment of server-owned fields.
Scope checks that need app/features/vocabulary.py (whether a feature_id
is real, whether an operator suits that feature's type) or
ALLOWED_SYMBOLS/ALLOWED_TIMEFRAMES live at the API route
(app/api/research.py) and are covered by tests/test_research_api.py
instead -- this file only tests what the models themselves enforce
without any app.features import.
"""

import pytest
from pydantic import ValidationError

from app.models.features import FEATURE_CONTRACT_VERSION
from app.models.research import (
    ConditionOperator,
    Experiment,
    ExperimentCreateRequest,
    ExperimentStatus,
    FeatureCondition,
    FeatureConditionOperator,
    Outcome,
)


def _feature_condition(
    feature_id="price_position.vwap_distance", operator=FeatureConditionOperator.GT, value=0.0, value_max=None
) -> FeatureCondition:
    return FeatureCondition(feature_id=feature_id, operator=operator, value=value, value_max=value_max)


def _outcome(metric="forward_return", horizon_minutes=60, operator=ConditionOperator.LTE, threshold=-0.005) -> Outcome:
    return Outcome(metric=metric, horizon_minutes=horizon_minutes, operator=operator, threshold=threshold)


def _create_request(**overrides) -> ExperimentCreateRequest:
    defaults = dict(
        name="TSLA Early Selling Continuation",
        hypothesis="When TSLA declines >= 1% during the first 30 minutes, it declines another >= 0.5% in the next 60.",
        symbol="TSLA",
        start_date="2026-01-01",
        end_date="2026-06-01",
        timeframe="5m",
        provider="csv",
        conditions=overrides.pop("conditions", [_feature_condition()]),
        outcome=_outcome(),
    )
    defaults.update(overrides)
    return ExperimentCreateRequest(**defaults)


class TestFeatureConditionValidation:
    def test_every_supported_operator_is_accepted_for_a_single_value_condition(self):
        for operator in (
            FeatureConditionOperator.LT,
            FeatureConditionOperator.LTE,
            FeatureConditionOperator.EQ,
            FeatureConditionOperator.GTE,
            FeatureConditionOperator.GT,
        ):
            condition = _feature_condition(operator=operator, value=1.5)
            assert condition.operator == operator

    def test_an_unsupported_operator_string_is_rejected(self):
        with pytest.raises(ValidationError):
            FeatureCondition(feature_id="price.return_30m", operator="!=", value=-0.01)

    def test_between_requires_a_value_max(self):
        with pytest.raises(ValidationError, match="requires value_max"):
            FeatureCondition(feature_id="volatility.volatility_percentile", operator="between", value=0.5)

    def test_between_with_a_value_max_is_accepted(self):
        condition = FeatureCondition(
            feature_id="volatility.volatility_percentile", operator="between", value=0.5, value_max=0.7
        )
        assert condition.value == 0.5
        assert condition.value_max == 0.7

    def test_between_rejects_a_value_max_not_greater_than_value(self):
        with pytest.raises(ValidationError, match="must be greater than"):
            FeatureCondition(feature_id="volatility.volatility_percentile", operator="between", value=0.7, value_max=0.5)

    def test_between_rejects_a_boolean_value(self):
        with pytest.raises(ValidationError, match="numeric value, not a boolean"):
            FeatureCondition(feature_id="price.return_5m", operator="between", value=True, value_max=1.0)

    def test_non_between_operators_reject_a_value_max(self):
        with pytest.raises(ValidationError, match="only valid with operator 'between'"):
            FeatureCondition(feature_id="price.return_5m", operator=">", value=0.0, value_max=1.0)

    def test_a_boolean_value_is_accepted_for_equality(self):
        condition = FeatureCondition(feature_id="price.return_5m", operator="=", value=True)
        assert condition.value is True

    def test_a_numeric_json_value_is_not_coerced_to_a_boolean(self):
        condition = FeatureCondition.model_validate_json(
            '{"feature_id": "price.return_5m", "operator": "=", "value": 0}'
        )
        assert condition.value == 0.0
        assert condition.value is not False  # a real 0.0, not a coerced bool


class TestOutcomeValidation:
    def test_forward_return_is_accepted(self):
        assert _outcome(metric="forward_return").metric == "forward_return"

    def test_any_other_metric_is_rejected(self):
        with pytest.raises(ValidationError):
            _outcome(metric="30m_return")

    def test_horizon_must_be_positive(self):
        with pytest.raises(ValidationError):
            _outcome(horizon_minutes=0)
        with pytest.raises(ValidationError):
            _outcome(horizon_minutes=-60)

    def test_every_condition_operator_is_accepted(self):
        for operator in ConditionOperator:
            assert _outcome(operator=operator).operator == operator


class TestExperimentCreateRequestValidation:
    def test_at_least_one_condition_is_required(self):
        with pytest.raises(ValidationError, match="at least 1 item"):
            _create_request(conditions=[])

    def test_multiple_conditions_are_accepted(self):
        request = _create_request(
            conditions=[
                _feature_condition("price_position.vwap_distance", FeatureConditionOperator.GT, 0.0),
                _feature_condition("volatility.volatility_percentile", FeatureConditionOperator.BETWEEN, 0.5, 0.7),
                _feature_condition("volume.relative_volume", FeatureConditionOperator.GT, 1.5),
            ]
        )
        assert len(request.conditions) == 3


class TestExperimentCreation:
    def test_new_assigns_a_draft_status_and_a_fresh_id(self):
        request = _create_request()
        experiment = Experiment.new(request)

        assert experiment.status == ExperimentStatus.DRAFT
        assert experiment.id  # non-empty
        assert experiment.completed_at is None
        assert experiment.results is None
        assert experiment.error_message is None

    def test_two_experiments_from_the_same_request_get_different_ids(self):
        request = _create_request()
        first = Experiment.new(request)
        second = Experiment.new(request)

        assert first.id != second.id

    def test_symbol_is_upper_cased(self):
        request = _create_request(symbol="tsla")
        experiment = Experiment.new(request)
        assert experiment.symbol == "TSLA"

    def test_the_exact_conditions_and_outcome_are_preserved_verbatim(self):
        conditions = [_feature_condition("price.return_15m", FeatureConditionOperator.LT, -0.02)]
        outcome = _outcome(horizon_minutes=30, operator=ConditionOperator.GTE, threshold=0.01)
        request = _create_request(conditions=conditions, outcome=outcome)

        experiment = Experiment.new(request)

        assert experiment.conditions == conditions
        assert experiment.outcome == outcome

    def test_feature_contract_version_is_captured_from_the_current_contract(self):
        experiment = Experiment.new(_create_request())
        assert experiment.feature_contract_version == FEATURE_CONTRACT_VERSION
