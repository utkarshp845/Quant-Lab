"""Pure computation of an experiment's pipeline-status (app/models/
pipeline_status.py) -- no I/O here, matching app/research/conditions.py's
/metrics.py's own "engine" discipline. app/api/research_pipeline.py is
the only caller, and does all the fetching (Experiment, Backtests, OOS
evaluations/periods/reviews, decisions, conclusions) before calling
build_pipeline_status() with already-loaded objects.

The 14-stage list itself (PIPELINE_STAGE_IDS) lives in
app/models/pipeline_status.py; this module's only job is deciding each
stage's status/warnings/clickability from real, already-persisted data
-- never inventing a stage or silently skipping one.
"""

from app.models.backtesting import Backtest, BacktestStatus
from app.models.oos_evaluation import OOSEvaluationResult, OOSEvaluationStatus
from app.models.pipeline_status import PipelineStage, PipelineStageStatus, PipelineStatusResponse
from app.models.research import Experiment, ExperimentLifecycleState, ExperimentStatus
from app.models.research_notebook import Conclusion, ResearchDecision

_SMALL_SAMPLE_THRESHOLD = 30


def build_pipeline_status(
    experiment: Experiment,
    *,
    bars_count: int,
    features_count: int,
    decisions: list[ResearchDecision],
    backtests: list[Backtest],
    oos_evaluations: list[OOSEvaluationResult],
    oos_period_count: int,
    conclusions: list[Conclusion],
) -> PipelineStatusResponse:
    stages: list[PipelineStage] = []

    # -- DATA --------------------------------------------------------
    if bars_count > 0:
        data_status, data_warnings = PipelineStageStatus.COMPLETE, []
    else:
        data_status = PipelineStageStatus.WARNING
        data_warnings = [
            f"No historical bars found for {experiment.symbol}/{experiment.timeframe}/{experiment.provider} "
            f"in {experiment.start_date} .. {experiment.end_date} -- fetch data before running this experiment."
        ]
    stages.append(
        PipelineStage(
            id="data",
            label="Data",
            purpose="Raw historical OHLCV bars for this symbol/timeframe/provider/date range.",
            status=data_status,
            inputs=[experiment.symbol, experiment.timeframe, experiment.provider],
            outputs=[f"{bars_count} bar(s)"],
            warnings=data_warnings,
            clickable=True,
        )
    )

    # -- FEATURES ------------------------------------------------------
    if features_count > 0:
        features_status, features_warnings = PipelineStageStatus.COMPLETE, []
    elif bars_count > 0:
        features_status = PipelineStageStatus.WARNING
        features_warnings = ["Bars exist but features have not been computed for this range yet."]
    else:
        features_status, features_warnings = PipelineStageStatus.NOT_STARTED, []
    stages.append(
        PipelineStage(
            id="features",
            label="Features",
            purpose="Feature Engine values computed from those bars (this experiment's conditions read only these).",
            status=features_status,
            inputs=[f"{bars_count} bar(s)"],
            outputs=[f"{features_count} feature record(s)", f"contract {experiment.feature_contract_version}"],
            warnings=features_warnings,
            clickable=True,
        )
    )

    # -- OBSERVE -------------------------------------------------------
    has_observation = experiment.originating_observation_id is not None
    stages.append(
        PipelineStage(
            id="observe",
            label="Observe",
            purpose='"What actually happened" -- a structured observation, independent of any hypothesis.',
            status=PipelineStageStatus.COMPLETE if has_observation else PipelineStageStatus.NOT_STARTED,
            inputs=[experiment.symbol],
            outputs=[experiment.originating_observation_id] if has_observation else [],
            warnings=[] if has_observation else ["No originating observation linked -- optional, but recommended."],
            clickable=True,
        )
    )

    # -- HYPOTHESIZE -----------------------------------------------------
    has_structured_hypothesis = any(
        [experiment.expected_direction, experiment.expected_behavior, experiment.rationale, experiment.invalidation_criteria]
    )
    hyp_warnings = (
        []
        if has_structured_hypothesis
        else ["Only free-text hypothesis recorded -- no structured direction/rationale/invalidation criteria."]
    )
    stages.append(
        PipelineStage(
            id="hypothesize",
            label="Hypothesize",
            purpose='"What might repeat?" -- direction, expected behavior, rationale, invalidation criteria.',
            status=PipelineStageStatus.COMPLETE,
            inputs=["observation (optional)"],
            outputs=[experiment.hypothesis],
            warnings=hyp_warnings,
            clickable=True,
        )
    )

    # -- DESIGN ----------------------------------------------------------
    design_status = PipelineStageStatus.COMPLETE if decisions else PipelineStageStatus.NOT_STARTED
    stages.append(
        PipelineStage(
            id="design",
            label="Design",
            purpose="Candidate definitions considered, and why one was chosen -- before outcome data existed.",
            status=design_status,
            inputs=[experiment.design_group_id] if experiment.design_group_id else [],
            outputs=[f"{len(decisions)} decision(s) logged"],
            warnings=[],
            clickable=True,
        )
    )

    # -- DEFINE ------------------------------------------------------------
    stages.append(
        PipelineStage(
            id="define",
            label="Define",
            purpose="The exact, deterministic conditions + outcome the machine executes.",
            status=PipelineStageStatus.COMPLETE,
            inputs=["hypothesis"],
            outputs=[f"{len(experiment.conditions)} condition(s)", experiment.outcome.metric],
            warnings=[],
            clickable=True,
        )
    )

    # -- LOCK ----------------------------------------------------------------
    is_locked = experiment.lifecycle_state != ExperimentLifecycleState.DRAFT
    stages.append(
        PipelineStage(
            id="lock",
            label="Lock",
            purpose="Freeze the definition -- after this, it cannot silently change.",
            status=PipelineStageStatus.COMPLETE if is_locked else PipelineStageStatus.NOT_STARTED,
            inputs=["conditions", "outcome"],
            outputs=[experiment.lifecycle_state.value],
            warnings=[] if is_locked else ["Not frozen yet -- backtesting/OOS results against a DRAFT are provisional."],
            clickable=True,
        )
    )

    # -- DETECT / MEASURE ------------------------------------------------------
    total_events = experiment.results.total_events if experiment.results else None
    if experiment.status == ExperimentStatus.RUNNING:
        detect_status, detect_warnings = PipelineStageStatus.IN_PROGRESS, []
    elif experiment.status == ExperimentStatus.FAILED:
        detect_status, detect_warnings = PipelineStageStatus.WARNING, [experiment.error_message or "Run failed."]
    elif experiment.status == ExperimentStatus.COMPLETED and total_events is not None:
        if total_events == 0:
            detect_status, detect_warnings = PipelineStageStatus.WARNING, ["No qualifying events found in this range."]
        elif total_events < _SMALL_SAMPLE_THRESHOLD:
            detect_status = PipelineStageStatus.WARNING
            detect_warnings = [f"Only {total_events} qualifying event(s) -- sample may be underpowered."]
        else:
            detect_status, detect_warnings = PipelineStageStatus.COMPLETE, []
    else:
        detect_status, detect_warnings = PipelineStageStatus.NOT_STARTED, []
    stages.append(
        PipelineStage(
            id="detect",
            label="Detect",
            purpose="Run conditions against the dataset; find every qualifying event.",
            status=detect_status,
            inputs=[f"{len(experiment.conditions)} condition(s)", f"{features_count} feature record(s)"],
            outputs=[f"{total_events} event(s)"] if total_events is not None else [],
            warnings=detect_warnings,
            clickable=True,
        )
    )
    stages.append(
        PipelineStage(
            id="measure",
            label="Measure",
            purpose="Outcome statistics per qualifying event -- forward return, distribution, not just win/loss.",
            status=detect_status if total_events else PipelineStageStatus.NOT_STARTED,
            inputs=[f"{total_events} event(s)"] if total_events else [],
            outputs=(
                [f"success_rate={experiment.results.success_rate}"]
                if experiment.results and experiment.results.success_rate is not None
                else []
            ),
            warnings=[],
            clickable=detect_status == PipelineStageStatus.COMPLETE,
        )
    )

    # -- COMPARE / VALIDATE ----------------------------------------------------
    completed_backtests = [b for b in backtests if b.status == BacktestStatus.COMPLETED]
    has_backtest = len(completed_backtests) > 0
    compare_validate_status = PipelineStageStatus.COMPLETE if has_backtest else PipelineStageStatus.NOT_STARTED
    compare_validate_warnings = [] if has_backtest else ["Run a backtest first -- baseline comparison needs its signals."]
    stages.append(
        PipelineStage(
            id="compare",
            label="Compare",
            purpose="Setup vs. an unconditional baseline over the same data.",
            status=compare_validate_status,
            inputs=[f"{len(completed_backtests)} completed backtest(s)"],
            outputs=[],
            warnings=compare_validate_warnings,
            clickable=True,
        )
    )
    stages.append(
        PipelineStage(
            id="validate",
            label="Validate",
            purpose="How much evidence is there this is meaningful rather than noise? (statistical, not economic/trading significance)",
            status=compare_validate_status,
            inputs=[f"{len(completed_backtests)} completed backtest(s)"],
            outputs=[],
            warnings=compare_validate_warnings,
            clickable=True,
        )
    )

    # -- CONCLUDE -----------------------------------------------------------
    stages.append(
        PipelineStage(
            id="conclude",
            label="Conclude",
            purpose="An explicit verdict, referencing the hypothesis/sample/baseline/validation/limitations it rests on.",
            status=PipelineStageStatus.COMPLETE if conclusions else PipelineStageStatus.NOT_STARTED,
            inputs=[f"{len(completed_backtests)} completed backtest(s)"],
            outputs=[conclusions[0].state.value] if conclusions else [],
            warnings=[] if conclusions or not has_backtest else ["Backtest results exist but no conclusion has been recorded."],
            clickable=True,
        )
    )

    # -- BACKTEST -------------------------------------------------------------
    running_backtests = [b for b in backtests if b.status == BacktestStatus.RUNNING]
    if has_backtest:
        backtest_status = PipelineStageStatus.COMPLETE
    elif running_backtests:
        backtest_status = PipelineStageStatus.IN_PROGRESS
    else:
        backtest_status = PipelineStageStatus.NOT_STARTED
    stages.append(
        PipelineStage(
            id="backtest",
            label="Backtest",
            purpose="Signal-level historical outcome measurement (Backtesting v1) -- not position/capital/P&L simulation.",
            status=backtest_status,
            inputs=["locked conditions" if is_locked else "conditions (unlocked -- provisional)"],
            outputs=[f"{len(backtests)} backtest run(s)"],
            warnings=[] if is_locked or not backtests else ["Backtest run against a DRAFT (unlocked) experiment."],
            clickable=True,
        )
    )

    # -- OOS --------------------------------------------------------------------
    completed_oos = [e for e in oos_evaluations if e.status == OOSEvaluationStatus.COMPLETED]
    if experiment.lifecycle_state == ExperimentLifecycleState.OOS_EVALUATED and completed_oos:
        oos_status = PipelineStageStatus.COMPLETE
    elif not is_locked:
        oos_status = PipelineStageStatus.BLOCKED
    else:
        oos_status = PipelineStageStatus.NOT_STARTED
    oos_warnings = []
    if oos_status == PipelineStageStatus.BLOCKED:
        oos_warnings = ["Freeze this experiment before it can be OOS-evaluated."]
    elif oos_status == PipelineStageStatus.COMPLETE and len(completed_oos) == 1 and oos_period_count <= 1:
        oos_warnings = ["Only one OOS period evaluated -- consider accumulating more evidence before drawing conclusions."]
    stages.append(
        PipelineStage(
            id="oos",
            label="OOS",
            purpose="Out-of-sample evaluation against holdout data never touched during research.",
            status=oos_status,
            inputs=[experiment.oos_partition_id] if experiment.oos_partition_id else [],
            outputs=[f"{len(completed_oos)} completed evaluation(s)", f"{oos_period_count} period(s)"],
            warnings=oos_warnings,
            clickable=is_locked,
        )
    )

    current_stage = _current_stage(stages)
    next_action = _next_action(stages)
    return PipelineStatusResponse(
        experiment_id=experiment.id, current_stage=current_stage, next_action=next_action, stages=stages
    )


def _current_stage(stages: list[PipelineStage]) -> str:
    """The id of the first stage that is NOT complete, or the last
    stage if every one is complete -- "where am I right now"."""
    for stage in stages:
        if stage.status != PipelineStageStatus.COMPLETE:
            return stage.id
    return stages[-1].id


def _next_action(stages: list[PipelineStage]) -> str:
    by_id = {s.id: s for s in stages}
    if by_id["data"].status == PipelineStageStatus.WARNING:
        return "Fetch historical data for this symbol/date range."
    if by_id["features"].status == PipelineStageStatus.WARNING:
        return "Compute features for this range."
    if by_id["detect"].status == PipelineStageStatus.NOT_STARTED:
        return "Run this experiment to detect qualifying events."
    if by_id["detect"].status == PipelineStageStatus.WARNING and "No qualifying" in " ".join(by_id["detect"].warnings):
        return "No qualifying events -- reconsider your conditions or date range."
    if by_id["lock"].status == PipelineStageStatus.NOT_STARTED:
        return "Review the results, then freeze this experiment before backtesting or OOS evaluation."
    if by_id["backtest"].status == PipelineStageStatus.NOT_STARTED:
        return "Create a backtest to measure signal-level outcomes."
    if by_id["conclude"].status == PipelineStageStatus.NOT_STARTED and by_id["backtest"].status == PipelineStageStatus.COMPLETE:
        return "Record a conclusion, referencing your sample/baseline/statistical validation/limitations."
    if by_id["oos"].status == PipelineStageStatus.NOT_STARTED:
        return "Run an out-of-sample evaluation against holdout data."
    return "Consider accumulating more OOS evidence, or start a new experiment."
