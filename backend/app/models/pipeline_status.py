"""Output shapes for the pipeline-status aggregation (app/research/
pipeline_status.py, app/api/research_pipeline.py) -- the redesign's
answer to "what stage is this experiment at, right now?" No single
source of truth for that exists anywhere else in this codebase; it has
to be derived by reading Experiment + its Backtests + its OOS state
together. Purely a READ aggregation -- nothing here is persisted; it is
recomputed on every request from already-persisted rows, the same
"derived, on-demand, never snapshotted" precedent
app/statistical_validation/engine.py's own module docstring already
set for StatisticalValidationReport.
"""

from enum import Enum

from pydantic import BaseModel

# The pipeline's fixed stage order (spec section 3). "paper_trade"/"live"
# are deliberately NOT included here -- this backend has no paper-
# trading or live-execution concept at all (spec section 20: "if it
# does not exist, do not fake it"); the frontend renders that as a
# static, honestly-labeled placeholder tile after this list, not as a
# computed stage.
PIPELINE_STAGE_IDS: list[str] = [
    "data",
    "features",
    "observe",
    "hypothesize",
    "design",
    "define",
    "lock",
    "detect",
    "measure",
    "compare",
    "validate",
    "conclude",
    "backtest",
    "oos",
]


class PipelineStageStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    WARNING = "warning"
    BLOCKED = "blocked"


class PipelineStage(BaseModel):
    """One stage's current state -- purpose/status/inputs/outputs/
    warnings/clickability, per spec section 3's own list of what each
    stage must show."""

    id: str
    label: str
    purpose: str
    status: PipelineStageStatus
    inputs: list[str]
    outputs: list[str]
    warnings: list[str]
    clickable: bool


class PipelineStatusResponse(BaseModel):
    """GET /research/experiments/{id}/pipeline-status. `current_stage`
    is the id of the LAST stage that is COMPLETE, or the first
    NOT_STARTED/WARNING/BLOCKED stage if none are -- "where am I right
    now" per spec section 1's own list of questions a user must always
    be able to answer. `next_action` is one sentence answering "what
    should I do next", spec section 5's own headline question."""

    experiment_id: str
    current_stage: str
    next_action: str
    stages: list[PipelineStage]
