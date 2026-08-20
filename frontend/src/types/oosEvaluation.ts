// Types mirroring backend/app/models/oos_evaluation.py (OOS Evaluation
// v1). Given a FROZEN experiment linked to an OOS partition, evaluates
// its frozen hypothesis against ONLY the partition's holdout data.
// Append-only -- re-running creates a brand-new evaluation row.

import type { BacktestResults, BacktestWindowOutcome } from "./backtesting";

export type OOSEvaluationStatus = "completed" | "failed";

export interface OOSSignal {
  evaluation_id: string;
  symbol: string;
  timeframe: string;
  signal_timestamp: string;
  entry_timestamp: string;
  entry_price: number;
  feature_values: Record<string, number | boolean>;
  outcomes: BacktestWindowOutcome[];
}

export interface OOSEvaluationResult {
  id: string;
  experiment_id: string;
  hypothesis_hash: string;
  frozen_snapshot_id: string;
  oos_partition_id: string;
  symbol: string;
  timeframe: string;
  provider: string;
  holdout_start: string;
  holdout_end: string;
  feature_contract_version: string;
  outcome_horizon_minutes: number;
  outcome_window_bars: number | null;
  signal_count: number;
  results: BacktestResults | null;
  status: OOSEvaluationStatus;
  error_message: string | null;
  frozen_at: string;
  evaluated_at: string;
}
