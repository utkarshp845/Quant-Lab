// Types mirroring backend/app/models/oos_evidence.py (OOS Evidence
// Accumulation V1). Lets an already-FROZEN experiment accumulate more
// than one independent OOS evaluation period over time. Deliberately
// keeps total_raw_signals (pooled, correlated) and
// total_independent_episodes (per-period, summed) separate -- never
// conflated -- and computes NO significance claim (that's OOS
// Statistical Review's job, types/oosStatisticalReview.ts).

import type { BacktestResults } from "./backtesting";
import type { OOSEvaluationStatus } from "./oosEvaluation";

export interface OOSPeriodLinkRequest {
  oos_partition_id: string;
}

export interface OOSPeriod {
  id: string;
  experiment_id: string;
  oos_partition_id: string;
  symbol: string;
  timeframe: string;
  provider: string;
  oos_start: string;
  oos_end: string;
  label: string | null;
  registered_at: string;
}

export interface OOSEvidencePeriodResult {
  evaluation_id: string;
  oos_partition_id: string;
  oos_start: string;
  oos_end: string;
  status: OOSEvaluationStatus;
  signal_count: number;
  episode_count: number;
  results: BacktestResults | null;
  evaluated_at: string;
}

export interface OOSEvidenceSummary {
  experiment_id: string;
  hypothesis_hash: string;
  oos_period_count: number;
  completed_evaluation_count: number;
  failed_evaluation_count: number;
  total_raw_signals: number;
  total_independent_episodes: number;
  mean_return: number | null;
  median_return: number | null;
  win_rate: number | null;
  std_dev_return: number | null;
  mean_mfe: number | null;
  mean_mae: number | null;
  earliest_oos_start: string | null;
  latest_oos_end: string | null;
  per_period_results: OOSEvidencePeriodResult[];
}
