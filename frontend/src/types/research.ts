// Types mirroring backend/app/models/research.py (Research v1).
//
// v0.1.24 (Feature <-> Research integration): Condition (a free-standing
// metric/operator/threshold triple, "{N}m_return" only) became
// FeatureCondition -- feature_id/operator/value(/value_max) -- where
// `feature_id` references src/types/features.ts::FeatureDefinition
// (fetched from GET /api/features/vocabulary) instead of this app
// hardcoding a metric vocabulary. An Experiment now holds
// `conditions: FeatureCondition[]` (AND-combined -- no OR, no nesting)
// instead of a single `condition`, plus `feature_contract_version`
// (requirement 6's reproducibility guarantee -- see the backend
// Experiment model's own docstring). Outcome is UNCHANGED -- see
// ConditionOperator below, still Outcome's own operator vocabulary.

export type ExperimentStatus = "draft" | "running" | "completed" | "failed";

// Outcome's operator vocabulary -- unchanged by v0.1.24. NOT what
// FeatureCondition uses (see FeatureConditionOperator below) --
// deliberately a separate, parallel vocabulary; see the backend
// FeatureConditionOperator's own docstring for why.
export type ConditionOperator = "<" | "<=" | "==" | ">=" | ">";
export const CONDITION_OPERATORS: ConditionOperator[] = ["<", "<=", "==", ">=", ">"];

export interface Outcome {
  metric: "forward_return"; // the only value v1's backend accepts
  horizon_minutes: number;
  operator: ConditionOperator;
  threshold: number;
}

// FeatureCondition's operator vocabulary (requirement 4): "=" (single
// equals), not "==" -- see src/components/research/ConditionBuilder.tsx
// for how the UI restricts this per-feature (a numeric feature offers
// all six; a boolean feature offers only "=").
export type FeatureConditionOperator = "<" | "<=" | "=" | ">=" | ">" | "between";
export const FEATURE_CONDITION_OPERATORS: FeatureConditionOperator[] = ["<", "<=", "=", ">=", ">", "between"];

export interface FeatureCondition {
  feature_id: string; // references FeatureDefinition.feature_id (src/types/features.ts)
  operator: FeatureConditionOperator;
  value: number | boolean; // the comparison target -- for "between", the LOWER bound
  value_max?: number | null; // ONLY meaningful (and required) for operator "between" -- the UPPER bound
}

export interface ExperimentResults {
  total_events: number;
  successful_events: number;
  failed_events: number;
  success_rate: number | null;
  average_outcome: number | null;
  median_outcome: number | null;
  min_outcome: number | null;
  max_outcome: number | null;
  std_dev_outcome: number | null;
}

export interface ExperimentEvent {
  experiment_id: string;
  symbol: string;
  signal_timestamp: string;
  signal_price: number;
  // feature_id -> the actual observed value for EVERY condition that
  // fired (v0.1.24, replacing the old single `condition_value: number`)
  // -- an experiment can have more than one ANDed condition now.
  condition_values: Record<string, number | boolean>;
  outcome_timestamp: string;
  outcome_price: number;
  outcome_value: number;
  success: boolean;
}

export interface ExperimentEventsResponse {
  experiment_id: string;
  event_count: number;
  events: ExperimentEvent[];
}

// Experiment Freeze & Provenance v1 (backend v0.1.30) -- a SECOND,
// independent lifecycle axis from ExperimentStatus above: whether the
// experiment's own HYPOTHESIS DEFINITION has been frozen for OOS
// evaluation. DRAFT -> FROZEN -> OOS_EVALUATED -> ARCHIVED (FROZEN ->
// ARCHIVED also allowed). See backend/app/models/research.py::
// ExperimentLifecycleState and backend/app/research/lifecycle.py.
export type ExperimentLifecycleState = "draft" | "frozen" | "oos_evaluated" | "archived";

export interface ExperimentCreateRequest {
  name: string;
  hypothesis: string;
  symbol: string;
  start_date: string; // YYYY-MM-DD
  end_date: string;
  timeframe: string;
  provider: string;
  conditions: FeatureCondition[]; // at least one, AND-combined
  outcome: Outcome;
  // Research Notebook v1 -- all optional/additive (backend/app/models/
  // research_notebook.py's own docstring on ExperimentCreateRequest).
  // Structured hypothesis fields (spec section 7):
  expected_direction?: string | null;
  expected_behavior?: string | null;
  rationale?: string | null;
  invalidation_criteria?: string | null;
  originating_observation_id?: string | null;
  // Versioning links (spec sections 8/10) -- a candidate considered
  // alongside others shares design_group_id/candidate_label; a new
  // version of a locked experiment sets parent_experiment_id/version_label.
  design_group_id?: string | null;
  candidate_label?: string | null;
  parent_experiment_id?: string | null;
  version_label?: string | null;
}

export interface Experiment {
  id: string;
  name: string;
  hypothesis: string;
  symbol: string;
  start_date: string;
  end_date: string;
  timeframe: string;
  provider: string;
  conditions: FeatureCondition[];
  outcome: Outcome;
  feature_contract_version: string;
  status: ExperimentStatus;
  created_at: string;
  completed_at: string | null;
  results: ExperimentResults | null;
  error_message: string | null;
  lifecycle_state: ExperimentLifecycleState;
  oos_partition_id: string | null;
  hypothesis_hash: string | null;
  frozen_at: string | null;
  archived_at: string | null;
  expected_direction: string | null;
  expected_behavior: string | null;
  rationale: string | null;
  invalidation_criteria: string | null;
  originating_observation_id: string | null;
  design_group_id: string | null;
  candidate_label: string | null;
  parent_experiment_id: string | null;
  version_label: string | null;
}
