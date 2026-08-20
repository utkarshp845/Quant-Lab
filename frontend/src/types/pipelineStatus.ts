// Types mirroring backend/app/models/pipeline_status.py -- the answer
// to "what stage is this experiment at, right now?", the primary
// in-experiment navigation model (see components/research/ResearchPipeline.tsx).

export const PIPELINE_STAGE_IDS = [
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
] as const;

export type PipelineStageId = (typeof PIPELINE_STAGE_IDS)[number];

export type PipelineStageStatus = "not_started" | "in_progress" | "complete" | "warning" | "blocked";

export interface PipelineStage {
  id: string;
  label: string;
  purpose: string;
  status: PipelineStageStatus;
  inputs: string[];
  outputs: string[];
  warnings: string[];
  clickable: boolean;
}

export interface PipelineStatusResponse {
  experiment_id: string;
  current_stage: string;
  next_action: string;
  stages: PipelineStage[];
}
