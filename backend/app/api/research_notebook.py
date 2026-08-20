"""API routes for Research Notebook v1 (app/models/research_notebook.py,
app/storage/research_notebook_repository.py, app/research/versions.py):

    POST /research/observations                                  create an Observation
    GET  /research/observations                                  list, optionally ?symbol=
    GET  /research/observations/{id}                              one Observation
    POST /research/decisions                                      log one decision (design_group_id in the body)
    GET  /research/design-groups/{design_group_id}/decisions       a design group's full decision history
    POST /research/experiments/{id}/conclusions                    record a Conclusion
    GET  /research/experiments/{id}/conclusions                    every Conclusion ever recorded (newest first)
    GET  /research/experiments/{id}/versions                       the version tree + diff from parent

Every write route here is create-only -- Observation/Decision/Conclusion
all lack an update or delete route, matching their own models' "no edit
endpoint for a definition/append-only record" docstrings.
"""

from fastapi import APIRouter, HTTPException

from app.api.historical_data import ALLOWED_SYMBOLS, ALLOWED_TIMEFRAMES
from app.features.vocabulary import get_feature_definition
from app.models.features import FEATURE_CONTRACT_VERSION
from app.models.research_notebook import (
    Conclusion,
    ConclusionCreateRequest,
    ConditionPreviewRequest,
    ConditionPreviewResponse,
    ExperimentVersionSummary,
    ExperimentVersionsResponse,
    Observation,
    ObservationCreateRequest,
    ResearchDecision,
    ResearchDecisionCreateRequest,
)
from app.research.design_preview import count_matching_signals
from app.research.versions import collect_version_tree, diff_experiments, find_root
from app.storage import feature_repository, research_notebook_repository, research_repository

router = APIRouter()


@router.post("/research/observations", response_model=Observation)
def create_observation(request: ObservationCreateRequest) -> Observation:
    observation = Observation.new(request)
    research_notebook_repository.save_observation(observation)
    return observation


@router.get("/research/observations", response_model=list[Observation])
def list_observations(symbol: str | None = None) -> list[Observation]:
    return research_notebook_repository.list_observations(symbol=symbol)


@router.get("/research/observations/{observation_id}", response_model=Observation)
def get_observation(observation_id: str) -> Observation:
    observation = research_notebook_repository.get_observation(observation_id)
    if observation is None:
        raise HTTPException(status_code=404, detail=f"No observation with id {observation_id!r}")
    return observation


@router.post("/research/decisions", response_model=ResearchDecision)
def create_decision(request: ResearchDecisionCreateRequest) -> ResearchDecision:
    """Logs one decision-log entry. If `resulting_experiment_id` is
    given, it must reference a real experiment -- a decision log
    entry pointing at a nonexistent experiment would be a broken
    provenance link, not a useful record."""
    if request.resulting_experiment_id is not None:
        if research_repository.get_experiment(request.resulting_experiment_id) is None:
            raise HTTPException(
                status_code=404, detail=f"No experiment with id {request.resulting_experiment_id!r}"
            )
    decision = ResearchDecision.new(request)
    research_notebook_repository.save_decision(decision)
    return decision


@router.get("/research/design-groups/{design_group_id}/decisions", response_model=list[ResearchDecision])
def list_decisions(design_group_id: str) -> list[ResearchDecision]:
    return research_notebook_repository.list_decisions(design_group_id)


@router.post("/research/experiments/{experiment_id}/conclusions", response_model=Conclusion)
def create_conclusion(experiment_id: str, request: ConclusionCreateRequest) -> Conclusion:
    if research_repository.get_experiment(experiment_id) is None:
        raise HTTPException(status_code=404, detail=f"No experiment with id {experiment_id!r}")
    conclusion = Conclusion.new(experiment_id, request)
    research_notebook_repository.save_conclusion(conclusion)
    return conclusion


@router.get("/research/experiments/{experiment_id}/conclusions", response_model=list[Conclusion])
def list_conclusions(experiment_id: str) -> list[Conclusion]:
    if research_repository.get_experiment(experiment_id) is None:
        raise HTTPException(status_code=404, detail=f"No experiment with id {experiment_id!r}")
    return research_notebook_repository.list_conclusions(experiment_id)


@router.post("/research/conditions/preview", response_model=ConditionPreviewResponse)
def preview_conditions(request: ConditionPreviewRequest) -> ConditionPreviewResponse:
    """The Design stage's "sample size, before outcome data exists"
    check (spec section 8) -- counts how many already-computed
    FeatureRecords satisfy `request.conditions`, WITHOUT ever reading a
    HistoricalBar or computing an outcome (see app/research/
    design_preview.py's own docstring for why that's structural, not a
    UI convention). Same symbol/timeframe/feature_id/operator
    validation as POST /research/experiments (app/api/research.py),
    reimplemented here rather than importing that route's own private
    helpers -- see app/oos_evidence/evaluation.py for the identical,
    established precedent in this codebase for why."""
    symbol = request.symbol.upper()
    if symbol not in ALLOWED_SYMBOLS:
        raise HTTPException(status_code=400, detail=f"Symbol {symbol!r} is not supported yet. Allowed: {sorted(ALLOWED_SYMBOLS)}")
    if request.timeframe not in ALLOWED_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe {request.timeframe!r}. Allowed: {sorted(ALLOWED_TIMEFRAMES)}")
    if request.end_date < request.start_date:
        raise HTTPException(status_code=400, detail="end_date must not be before start_date")

    for condition in request.conditions:
        try:
            definition = get_feature_definition(condition.feature_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if condition.operator.value not in definition.supported_operators:
            raise HTTPException(
                status_code=400,
                detail=f"Operator {condition.operator.value!r} is not supported for feature {condition.feature_id!r}.",
            )

    feature_records = feature_repository.get_features(
        symbol=symbol, timeframe=request.timeframe, provider=request.provider,
        start=request.start_date, end=request.end_date,
    )
    matching = count_matching_signals(request.conditions, feature_records, FEATURE_CONTRACT_VERSION)
    return ConditionPreviewResponse(total_feature_records=len(feature_records), matching_signal_count=matching)


@router.get("/research/experiments/{experiment_id}/versions", response_model=ExperimentVersionsResponse)
def get_versions(experiment_id: str) -> ExperimentVersionsResponse:
    target = research_repository.get_experiment(experiment_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"No experiment with id {experiment_id!r}")

    all_experiments = research_repository.list_experiments()
    by_id = {e.id: e for e in all_experiments}
    root_id = find_root(experiment_id, by_id)
    versions = collect_version_tree(root_id, all_experiments)

    diff_from_parent = None
    if target.parent_experiment_id and target.parent_experiment_id in by_id:
        diff_from_parent = diff_experiments(by_id[target.parent_experiment_id], target)

    return ExperimentVersionsResponse(
        experiment_id=experiment_id,
        root_id=root_id,
        versions=[
            ExperimentVersionSummary(
                id=v.id,
                name=v.name,
                version_label=v.version_label,
                candidate_label=v.candidate_label,
                design_group_id=v.design_group_id,
                parent_experiment_id=v.parent_experiment_id,
                lifecycle_state=v.lifecycle_state.value,
                created_at=v.created_at,
            )
            for v in versions
        ],
        diff_from_parent=diff_from_parent,
    )
