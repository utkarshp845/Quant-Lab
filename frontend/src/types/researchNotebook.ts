// Types mirroring backend/app/models/research_notebook.py (Research
// Notebook v1): Observation ("what happened"), Decision (candidate-
// selection provenance log), Conclusion (a verdict that must reference
// its own evidence), and the experiment version tree.

import type { FeatureCondition } from "./research";

// The Design stage's "sample size, before outcome data exists" check
// (spec section 8) -- POST /research/conditions/preview counts
// matching signals from already-computed features WITHOUT ever
// touching bars or computing an outcome (backend/app/research/
// design_preview.py's own docstring: a structural guarantee, not a UI
// convention).
export interface ConditionPreviewRequest {
  symbol: string;
  start_date: string;
  end_date: string;
  timeframe: string;
  provider: string;
  conditions: FeatureCondition[];
}

export interface ConditionPreviewResponse {
  total_feature_records: number;
  matching_signal_count: number;
}

export interface ObservationCreateRequest {
  symbol: string;
  description: string;
  observed_start: string;
  observed_end: string;
  referenced_bar_timestamps?: string[];
  referenced_feature_ids?: string[];
}

export interface Observation {
  id: string;
  symbol: string;
  description: string;
  observed_start: string;
  observed_end: string;
  referenced_bar_timestamps: string[];
  referenced_feature_ids: string[];
  created_at: string;
}

export interface ResearchDecisionCreateRequest {
  design_group_id: string;
  decision: string;
  reason: string;
  selection_criteria?: string[];
  information_available?: string[];
  outcome_data_available: boolean;
  resulting_experiment_id?: string | null;
}

export interface ResearchDecision {
  id: string;
  design_group_id: string;
  decision: string;
  reason: string;
  selection_criteria: string[];
  information_available: string[];
  outcome_data_available: boolean;
  resulting_experiment_id: string | null;
  created_at: string;
}

// The five states spec section 18 lists -- deliberately not a binary
// pass/fail.
export type ConclusionState = "supported" | "weakened" | "inconclusive" | "rejected" | "needs_more_data";

export interface ConclusionCreateRequest {
  state: ConclusionState;
  statement: string;
  references_hypothesis: string;
  references_sample: string;
  references_baseline: string;
  references_outcomes: string;
  references_statistical_validation: string;
  limitations: string;
}

export interface Conclusion {
  id: string;
  experiment_id: string;
  state: ConclusionState;
  statement: string;
  references_hypothesis: string;
  references_sample: string;
  references_baseline: string;
  references_outcomes: string;
  references_statistical_validation: string;
  limitations: string;
  created_at: string;
}

export interface ExperimentFieldDiff {
  field: string;
  parent_value: string;
  child_value: string;
}

export interface ExperimentVersionSummary {
  id: string;
  name: string;
  version_label: string | null;
  candidate_label: string | null;
  design_group_id: string | null;
  parent_experiment_id: string | null;
  lifecycle_state: string;
  created_at: string;
}

export interface ExperimentVersionsResponse {
  experiment_id: string;
  root_id: string;
  versions: ExperimentVersionSummary[];
  diff_from_parent: ExperimentFieldDiff[] | null;
}
