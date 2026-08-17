"""Tests for app/models/research.py -- Condition/Outcome field
validation and Experiment.new()'s assignment of server-owned fields.
Scope checks that need ALLOWED_SYMBOLS/ALLOWED_TIMEFRAMES or
metric-window math live at the API route (app/api/research.py) and are
covered by tests/test_research_api.py instead -- this file only tests
what the models themselves enforce.
"""

import pytest
from pydantic import ValidationError

from app.models.research import (
    Condition,
    ConditionOperator,
    Experiment,
    ExperimentCreateRequest,
    ExperimentStatus,
    Outcome,
)


def _condition(metric="30m_return", operator=ConditionOperator.LTE, threshold=-0.01) -> Condition:
    return Condition(metric=metric, operator=operator, threshold=threshold)


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
        condition=_condition(),
        outcome=_outcome(),
    )
    defaults.update(overrides)
    return ExperimentCreateRequest(**defaults)


class TestConditionValidation:
    def test_a_well_formed_trailing_return_metric_is_accepted(self):
        condition = _condition(metric="30m_return")
        assert condition.metric == "30m_return"

    @pytest.mark.parametrize("bad_metric", ["forward_return", "30_minute_return", "return", "", "30m-return", "m_return"])
    def test_malformed_metrics_are_rejected(self, bad_metric):
        with pytest.raises(ValidationError):
            _condition(metric=bad_metric)

    def test_every_supported_operator_is_accepted(self):
        for operator in ConditionOperator:
            assert _condition(operator=operator).operator == operator

    def test_an_unsupported_operator_string_is_rejected(self):
        with pytest.raises(ValidationError):
            Condition(metric="30m_return", operator="!=", threshold=-0.01)


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

    def test_the_exact_condition_and_outcome_are_preserved_verbatim(self):
        condition = _condition(metric="15m_return", operator=ConditionOperator.LT, threshold=-0.02)
        outcome = _outcome(horizon_minutes=30, operator=ConditionOperator.GTE, threshold=0.01)
        request = _create_request(condition=condition, outcome=outcome)

        experiment = Experiment.new(request)

        assert experiment.condition == condition
        assert experiment.outcome == outcome
