// Types mirroring backend/app/models/research.py (Research v1).
//
// Scope, carried over verbatim from the backend model's own docstring:
// ONE condition, ONE outcome, per experiment -- no boolean composition,
// no multi-symbol universe. `Condition.metric` only accepts the shape
// "{N}m_return" (validated server-side by a regex); `Outcome.metric` is
// always exactly "forward_return". Neither is a free-form feature
// picker -- see src/components/research/ConditionBuilder.tsx for how
// the UI keeps that scope visible rather than pretending it supports
// arbitrary features.

export type ExperimentStatus = "draft" | "running" | "completed" | "failed";

export type ConditionOperator = "<" | "<=" | "==" | ">=" | ">";

export const CONDITION_OPERATORS: ConditionOperator[] = ["<", "<=", "==", ">=", ">"];

export interface Condition {
  metric: string; // "{N}m_return", e.g. "30m_return"
  operator: ConditionOperator;
  threshold: number;
}

export interface Outcome {
  metric: "forward_return"; // the only value v1's backend accepts
  horizon_minutes: number;
  operator: ConditionOperator;
  threshold: number;
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
  condition_value: number;
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

export interface ExperimentCreateRequest {
  name: string;
  hypothesis: string;
  symbol: string;
  start_date: string; // YYYY-MM-DD
  end_date: string;
  timeframe: string;
  provider: string;
  condition: Condition;
  outcome: Outcome;
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
  condition: Condition;
  outcome: Outcome;
  status: ExperimentStatus;
  created_at: string;
  completed_at: string | null;
  results: ExperimentResults | null;
  error_message: string | null;
}
