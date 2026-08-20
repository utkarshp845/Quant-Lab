"""Research Notebook v1: the provenance/methodology layer the redesign's
audit found missing entirely -- Observation, candidate-selection Decision
log, and Conclusion. None of these duplicate Research v1 (app/research/),
Feature Engine v1, Backtesting v1, or Statistical Validation -- they are
metadata ABOUT a research process that already runs on those engines
unmodified, recorded so the process itself (not just its numeric results)
stays inspectable.

Three entities, three tables (app/storage/db.py), one repository
(app/storage/research_notebook_repository.py), one router
(app/api/research_notebook.py):

  Observation  -- "what actually happened" (a structured, falsifiable
                  description of market behavior, referencing real
                  symbols/dates/bars/features), independent of any
                  hypothesis it might later inspire.
  Decision     -- one entry in a design group's append-only provenance
                  log: what was decided, why, what information was
                  available at the time, and -- critically -- whether
                  outcome data was available when the decision was made.
                  This is the mechanism that makes hindsight bias/
                  unconscious overfitting inspectable rather than merely
                  discouraged.
  Conclusion   -- a research verdict (SUPPORTED/WEAKENED/INCONCLUSIVE/
                  REJECTED/NEEDS_MORE_DATA) that cannot be recorded
                  without a caller filling in what it's based on -- the
                  four reference fields are required, non-blank text,
                  not optional decoration.

Experiment versioning (`design_group_id`/`candidate_label`/
`parent_experiment_id`/`version_label`) and the structured-hypothesis
fields (`expected_direction`/`expected_behavior`/`rationale`/
`invalidation_criteria`/`originating_observation_id`) live as ADDITIVE,
nullable fields directly on Experiment/ExperimentCreateRequest
(app/models/research.py) instead of a parallel table -- an "Experiment"
already IS what the redesign calls a candidate/version; giving it a
second, shadow representation here would be exactly the kind of
duplication the architectural rules forbid. ExperimentVersionNode/
ExperimentVersionsResponse/ExperimentFieldDiff below describe how those
existing fields are assembled into a tree + diff view, not a new
research-defining entity.

Every id here is a random uuid4 (like Experiment.id/Backtest.id), and
every table this module owns is written through exactly once per row --
Decision and Conclusion are explicitly append-only (see their own
docstrings for why); Observation has no update endpoint either (a
correction is a new Observation, not a silently-rewritten one -- the
same "no edit endpoint" discipline this app applies to Experiment
itself).
"""

import uuid
from datetime import date, datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from app.models.research import FeatureCondition


class ObservationCreateRequest(BaseModel):
    """Body of POST /research/observations. `description` is the
    falsifiable "what happened" statement -- deliberately free text (an
    Observation is a human noticing something in the data, not itself a
    deterministic rule; DEFINE is where a later hypothesis becomes
    deterministic conditions). `referenced_bar_timestamps`/
    `referenced_feature_ids` are how an Observation stays traceable back
    to real market data (spec: "Allow observations to reference actual
    symbols, dates, timestamps, bars, features") -- both optional since
    an observation can legitimately point at a date range without
    pinning individual bars yet."""

    symbol: str
    description: str
    observed_start: datetime
    observed_end: datetime
    referenced_bar_timestamps: list[datetime] = Field(default_factory=list)
    referenced_feature_ids: list[str] = Field(default_factory=list)

    @field_validator("description")
    @classmethod
    def _description_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank -- an Observation with nothing observed is not one.")
        return value

    @field_validator("observed_end")
    @classmethod
    def _end_not_before_start(cls, value: datetime, info) -> datetime:
        start = info.data.get("observed_start")
        if start is not None and value < start:
            raise ValueError("observed_end must not be before observed_start.")
        return value


class Observation(BaseModel):
    """The persisted Observation (app/storage/
    research_notebook_repository.py). Immutable after creation -- every
    field is set once, at Observation.new() time, matching this app's
    "no edit endpoint" convention everywhere a definition (rather than a
    run's status) is recorded."""

    id: str
    symbol: str
    description: str
    observed_start: datetime
    observed_end: datetime
    referenced_bar_timestamps: list[datetime]
    referenced_feature_ids: list[str]
    created_at: datetime

    @classmethod
    def new(cls, request: ObservationCreateRequest) -> "Observation":
        return cls(
            id=str(uuid.uuid4()),
            symbol=request.symbol.upper(),
            description=request.description,
            observed_start=request.observed_start,
            observed_end=request.observed_end,
            referenced_bar_timestamps=request.referenced_bar_timestamps,
            referenced_feature_ids=request.referenced_feature_ids,
            created_at=datetime.now(timezone.utc),
        )


class ResearchDecisionCreateRequest(BaseModel):
    """Body of POST /research/design-groups/{design_group_id}/decisions
    -- one entry in a design group's provenance log (spec section 9's
    worked example: "Selected Candidate C" / reason / selection
    criteria / information available at decision time / whether outcome
    data was available / status). `outcome_data_available` is the field
    this whole entity exists for: a decision made about which candidate
    definition to lock BEFORE any of them has been run has
    `outcome_data_available=False` by construction -- this module does
    not compute that value, a caller states it, because only the caller
    (the UI, at the moment a human makes the choice) actually knows
    whether it looked at results first. This is a discipline aid, not a
    technical enforcement mechanism -- see this feature's own docstring."""

    design_group_id: str
    decision: str
    reason: str
    selection_criteria: list[str] = Field(default_factory=list)
    information_available: list[str] = Field(default_factory=list)
    outcome_data_available: bool
    resulting_experiment_id: str | None = None

    @field_validator("decision", "reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank -- a decision log entry with no decision/reason recorded is not one.")
        return value


class ResearchDecision(BaseModel):
    """One persisted, immutable decision-log entry. APPEND-ONLY --
    there is no update/delete function anywhere in app/storage/
    research_notebook_repository.py, the same convention
    oos_evaluations/oos_statistical_reviews already use for "a record of
    what was decided/computed, once, that must never be rewritten
    after the fact." A design group accumulates one entry per real
    decision made about it (candidate proposed, candidate selected,
    a later amendment) -- GET .../decisions returns the full ordered
    history, never just the latest."""

    id: str
    design_group_id: str
    decision: str
    reason: str
    selection_criteria: list[str]
    information_available: list[str]
    outcome_data_available: bool
    resulting_experiment_id: str | None
    created_at: datetime

    @classmethod
    def new(cls, request: ResearchDecisionCreateRequest) -> "ResearchDecision":
        return cls(
            id=str(uuid.uuid4()),
            design_group_id=request.design_group_id,
            decision=request.decision,
            reason=request.reason,
            selection_criteria=request.selection_criteria,
            information_available=request.information_available,
            outcome_data_available=request.outcome_data_available,
            resulting_experiment_id=request.resulting_experiment_id,
            created_at=datetime.now(timezone.utc),
        )


class ConclusionState(str, Enum):
    """The five states spec section 18 lists -- deliberately not a
    binary PASS/FAIL (spec: "Do not force a binary PASS/FAIL")."""

    SUPPORTED = "supported"
    WEAKENED = "weakened"
    INCONCLUSIVE = "inconclusive"
    REJECTED = "rejected"
    NEEDS_MORE_DATA = "needs_more_data"


class ConclusionCreateRequest(BaseModel):
    """Body of POST /research/experiments/{id}/conclusions. Every
    `references_*` field plus `limitations` is REQUIRED, non-blank text
    -- spec section 18: "Require conclusions to reference: hypothesis,
    sample, baseline, outcomes, statistical validation, limitations."
    This module cannot verify a reference is actually accurate (it has
    no way to know whether the text genuinely describes this
    experiment's own sample/baseline/validation), but it can and does
    refuse to persist a conclusion that skipped any of them -- the same
    "hard to accidentally violate" spirit as ResearchDecision's
    `outcome_data_available` field."""

    state: ConclusionState
    statement: str
    references_hypothesis: str
    references_sample: str
    references_baseline: str
    references_outcomes: str
    references_statistical_validation: str
    limitations: str

    @field_validator(
        "statement",
        "references_hypothesis",
        "references_sample",
        "references_baseline",
        "references_outcomes",
        "references_statistical_validation",
        "limitations",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "must not be blank -- a Conclusion must reference the hypothesis, sample, baseline, outcomes, "
                "statistical validation, and limitations it is actually based on."
            )
        return value


class Conclusion(BaseModel):
    """One persisted, immutable conclusion. APPEND-ONLY (same reasoning
    as ResearchDecision above) -- an experiment can gain a NEW
    conclusion (more evidence arrived, a re-read changed the verdict),
    never an edited one; GET .../conclusions returns every one ever
    recorded, newest first, and the newest is "current" by convention,
    not by a separate `is_current` flag (there is exactly one
    unambiguous newest-by-created_at row)."""

    id: str
    experiment_id: str
    state: ConclusionState
    statement: str
    references_hypothesis: str
    references_sample: str
    references_baseline: str
    references_outcomes: str
    references_statistical_validation: str
    limitations: str
    created_at: datetime

    @classmethod
    def new(cls, experiment_id: str, request: ConclusionCreateRequest) -> "Conclusion":
        return cls(
            id=str(uuid.uuid4()),
            experiment_id=experiment_id,
            state=request.state,
            statement=request.statement,
            references_hypothesis=request.references_hypothesis,
            references_sample=request.references_sample,
            references_baseline=request.references_baseline,
            references_outcomes=request.references_outcomes,
            references_statistical_validation=request.references_statistical_validation,
            limitations=request.limitations,
            created_at=datetime.now(timezone.utc),
        )


class ExperimentFieldDiff(BaseModel):
    """One changed field between two experiments' research-defining
    values, built from app/research/lifecycle.py::
    canonicalize_hypothesis() (reused unmodified -- the exact field set
    that already defines a hypothesis's meaning for hash purposes is
    the exact field set worth diffing)."""

    field: str
    parent_value: str
    child_value: str


class ExperimentVersionSummary(BaseModel):
    """One node in a version tree -- deliberately a thin projection of
    Experiment (id/name/version_label/candidate_label/lifecycle_state/
    created_at), not the full object: GET .../versions is for seeing the
    shape of a version tree at a glance, not for re-fetching every
    experiment's full definition (GET .../experiments/{id} already does
    that)."""

    id: str
    name: str
    version_label: str | None
    candidate_label: str | None
    design_group_id: str | None
    parent_experiment_id: str | None
    lifecycle_state: str
    created_at: datetime


class ConditionPreviewRequest(BaseModel):
    """Body of POST /research/conditions/preview -- the Design stage's
    "sample size, before outcome data exists" check (spec section 8).
    Deliberately the SAME shape as the research-defining subset of
    ExperimentCreateRequest, minus `outcome` -- an outcome has no
    meaning here, since this route never computes one (see
    app/research/design_preview.py's own docstring for why that's a
    structural guarantee, not a convention)."""

    symbol: str
    start_date: date
    end_date: date
    timeframe: str
    provider: str
    conditions: list[FeatureCondition] = Field(min_length=1)


class ConditionPreviewResponse(BaseModel):
    total_feature_records: int
    matching_signal_count: int


class ExperimentVersionsResponse(BaseModel):
    """GET /research/experiments/{id}/versions: every experiment in the
    same version tree as `experiment_id` (found by walking
    `parent_experiment_id` up to the root, then collecting every
    descendant of that root), plus a field-level diff between
    `experiment_id` and its immediate parent, if it has one. `root_id`
    equals `experiment_id` itself when it has no parent and no
    children -- a version tree of one is still a valid, empty tree."""

    experiment_id: str
    root_id: str
    versions: list[ExperimentVersionSummary]
    diff_from_parent: list[ExperimentFieldDiff] | None
