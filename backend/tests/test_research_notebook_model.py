"""Model-level tests for Research Notebook v1 (app/models/research_notebook.py):
construction, blank-field rejection, and Experiment.new()'s additive
optional fields (app/models/research.py)."""

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.research import ExperimentCreateRequest, Experiment, FeatureCondition, Outcome
from app.models.research_notebook import (
    Conclusion,
    ConclusionCreateRequest,
    ConclusionState,
    Observation,
    ObservationCreateRequest,
    ResearchDecision,
    ResearchDecisionCreateRequest,
)


def _observation_request(**overrides) -> ObservationCreateRequest:
    defaults = dict(
        symbol="tsla",
        description="TSLA dropped sharply on high volume at the open.",
        observed_start=datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc),
        observed_end=datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return ObservationCreateRequest(**defaults)


def test_observation_new_uppercases_symbol_and_assigns_id():
    obs = Observation.new(_observation_request())
    assert obs.symbol == "TSLA"
    assert obs.id
    assert obs.created_at.tzinfo is not None


def test_observation_blank_description_rejected():
    with pytest.raises(ValidationError):
        _observation_request(description="   ")


def test_observation_end_before_start_rejected():
    with pytest.raises(ValidationError):
        _observation_request(
            observed_start=datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc),
            observed_end=datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc),
        )


def test_observation_references_default_empty():
    obs = Observation.new(_observation_request())
    assert obs.referenced_bar_timestamps == []
    assert obs.referenced_feature_ids == []


def _decision_request(**overrides) -> ResearchDecisionCreateRequest:
    defaults = dict(
        design_group_id="dg-1",
        decision="Selected Candidate C",
        reason="Largest viable sample among conceptually valid definitions.",
        selection_criteria=["sample_size", "conceptual_validity"],
        information_available=["sample_size"],
        outcome_data_available=False,
    )
    defaults.update(overrides)
    return ResearchDecisionCreateRequest(**defaults)


def test_decision_new_assigns_id_and_timestamp():
    decision = ResearchDecision.new(_decision_request())
    assert decision.id
    assert decision.outcome_data_available is False
    assert decision.created_at.tzinfo is not None


@pytest.mark.parametrize("field", ["decision", "reason"])
def test_decision_blank_fields_rejected(field):
    with pytest.raises(ValidationError):
        _decision_request(**{field: "  "})


def _conclusion_request(**overrides) -> ConclusionCreateRequest:
    defaults = dict(
        state=ConclusionState.INCONCLUSIVE,
        statement="No clear edge over baseline at the primary horizon.",
        references_hypothesis="Downside-momentum-on-volume continuation hypothesis.",
        references_sample="63 independent episodes.",
        references_baseline="Unconditional TSLA 15m forward return, same range.",
        references_outcomes="Mean -0.03%, median -0.06%.",
        references_statistical_validation="p=0.25 (Method A), p=0.41 (Method B), both n.s.",
        limitations="Single symbol, single development window.",
    )
    defaults.update(overrides)
    return ConclusionCreateRequest(**defaults)


def test_conclusion_new_carries_experiment_id():
    conclusion = Conclusion.new("exp-123", _conclusion_request())
    assert conclusion.experiment_id == "exp-123"
    assert conclusion.state == ConclusionState.INCONCLUSIVE


@pytest.mark.parametrize(
    "field",
    [
        "statement",
        "references_hypothesis",
        "references_sample",
        "references_baseline",
        "references_outcomes",
        "references_statistical_validation",
        "limitations",
    ],
)
def test_conclusion_requires_every_reference_field_non_blank(field):
    with pytest.raises(ValidationError):
        _conclusion_request(**{field: ""})


def _experiment_request(**overrides) -> ExperimentCreateRequest:
    defaults = dict(
        name="Test experiment",
        hypothesis="Free text hypothesis.",
        symbol="TSLA",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        timeframe="5m",
        provider="csv",
        conditions=[FeatureCondition(feature_id="price.return_15m", operator="<=", value=-0.005)],
        outcome=Outcome(metric="forward_return", horizon_minutes=15, operator="<=", threshold=0.0),
    )
    defaults.update(overrides)
    return ExperimentCreateRequest(**defaults)


def test_experiment_new_defaults_notebook_fields_to_none():
    experiment = Experiment.new(_experiment_request())
    assert experiment.expected_direction is None
    assert experiment.rationale is None
    assert experiment.design_group_id is None
    assert experiment.parent_experiment_id is None
    assert experiment.version_label is None


def test_experiment_new_carries_notebook_fields_through():
    request = _experiment_request(
        expected_direction="down",
        expected_behavior="continuation",
        rationale="Momentum persists on high relative volume.",
        invalidation_criteria="No effect if relative volume < 1.5x.",
        originating_observation_id="obs-1",
        design_group_id="dg-1",
        candidate_label="C",
        parent_experiment_id="exp-parent",
        version_label="2A",
    )
    experiment = Experiment.new(request)
    assert experiment.expected_direction == "down"
    assert experiment.rationale == "Momentum persists on high relative volume."
    assert experiment.design_group_id == "dg-1"
    assert experiment.candidate_label == "C"
    assert experiment.parent_experiment_id == "exp-parent"
    assert experiment.version_label == "2A"
