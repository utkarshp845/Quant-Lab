// Types mirroring backend/app/models/oos_statistical_review.py (OOS
// Statistical Review V1) -- a formal, READ-ONLY statistical review of
// a frozen experiment's own accumulated OOS evidence. Reuses
// Statistical Validation V2's own result shapes for the OOS-scoped
// equivalent (types/statisticalValidationV2.ts).

import type {
  DependenceAwareTestResultV2,
  EffectSizeResultV2,
  MeanDifferenceResultV2,
  PowerAnalysisResultV2,
  RobustnessComparisonV2,
  WinRateDifferenceResultV2,
} from "./statisticalValidationV2";

// This verdict is a statement about evidence for the forward-return
// hypothesis ONLY -- never a trading recommendation, never a claim
// about profitability. p >= 0.05 alone is INCONCLUSIVE, not
// NOT_SUPPORTED.
export type OOSStatisticalVerdict = "supported" | "not_supported" | "inconclusive" | "insufficient_data";

export interface ExcludedEvaluation {
  evaluation_id: string;
  oos_partition_id: string;
  reason: string;
}

export interface OOSPeriodBoundary {
  evaluation_id: string;
  oos_partition_id: string;
  oos_start: string;
  oos_end: string;
}

export interface OOSEpisodeSampleSizes {
  evaluation_count: number;
  raw_signal_count: number;
  episode_count: number;
  baseline_raw_observations: number;
  method_a_effective_baseline_n: number;
}

export interface OOSPeriodConsistencyResult {
  evaluation_id: string;
  oos_partition_id: string;
  oos_start: string;
  oos_end: string;
  raw_signal_count: number;
  episode_count: number;
  mean_return: number | null;
  median_return: number | null;
  win_rate: number | null;
  std_dev_return: number | null;
}

export interface OOSStatisticalReview {
  id: string;
  experiment_id: string;
  frozen_snapshot_id: string;
  hypothesis_hash: string;
  review_config_version: string;
  created_at: string;

  included_evaluation_ids: string[];
  excluded_evaluations: ExcludedEvaluation[];
  oos_periods: OOSPeriodBoundary[];
  outcome_metric: string;
  outcome_operator: string;
  outcome_threshold: number;
  outcome_horizon_minutes: number;
  primary_window_bars: number;
  symbol: string;
  timeframe: string;
  provider: string;
  feature_contract_version: string;

  seed: number;
  n_resamples: number;
  ci_level: number;
  block_length_multiplier: number;
  power_target: number;
  min_episodes_for_formal_test: number;

  sample_sizes: OOSEpisodeSampleSizes;

  method_a_mean_difference: MeanDifferenceResultV2 | null;
  method_a_win_rate_difference: WinRateDifferenceResultV2 | null;
  method_a_test: DependenceAwareTestResultV2 | null;
  method_b_mean_difference: MeanDifferenceResultV2 | null;
  method_b_win_rate_difference: WinRateDifferenceResultV2 | null;
  method_b_test: DependenceAwareTestResultV2 | null;
  effect_size: EffectSizeResultV2 | null;
  power_analysis: PowerAnalysisResultV2 | null;
  robustness: RobustnessComparisonV2 | null;

  exploratory_horizons_note: string;

  per_period_results: OOSPeriodConsistencyResult[];

  verdict: OOSStatisticalVerdict;
  verdict_reasoning: string;
}
