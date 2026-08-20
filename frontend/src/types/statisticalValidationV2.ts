// Types mirroring backend/app/models/statistical_validation_v2.py
// (Statistical Validation V2) -- corrects V1's baseline dependence
// weakness with two independent methods (non-overlapping windows,
// moving block bootstrap), reused unmodified by OOS Statistical Review
// (types/oosStatisticalReview.ts) for the OOS-scoped equivalent.

export type BaselineMethodV2 = "non_overlapping_windows" | "moving_block_bootstrap";

export interface PopulationSummaryV2 {
  window_bars: number;
  raw_conditioned_signals: number;
  conditioned_episodes: number;
  baseline_raw_observations: number;
  method_a_effective_baseline_n: number;
  method_b_block_length: number;
  method_b_block_count: number;
}

export interface MeanDifferenceResultV2 {
  method: BaselineMethodV2;
  window_bars: number;
  conditioned_mean: number;
  baseline_mean: number;
  difference: number;
  ci_low: number;
  ci_high: number;
  ci_level: number;
  n_conditioned: number;
  n_baseline: number;
}

export interface WinRateDifferenceResultV2 {
  method: BaselineMethodV2;
  window_bars: number;
  conditioned_win_rate: number;
  baseline_win_rate: number;
  difference_pp: number;
  ci_low_pp: number;
  ci_high_pp: number;
  ci_level: number;
  n_conditioned: number;
  n_baseline: number;
}

export interface DependenceAwareTestResultV2 {
  method: BaselineMethodV2;
  window_bars: number;
  observed_mean_difference: number;
  p_value_two_sided: number;
  n_resamples: number;
  n_conditioned: number;
  n_baseline: number;
  seed: number;
}

export interface EffectSizeResultV2 {
  window_bars: number;
  cohens_d: number;
  pooled_stdev: number;
  interpretation: string;
  method: BaselineMethodV2;
}

export interface PowerAnalysisResultV2 {
  n_conditioned_episodes: number;
  n_baseline_effective: number;
  alpha: number;
  power: number;
  minimum_detectable_effect_size: number;
  observed_effect_size: number;
  observed_effect_below_detectable_threshold: boolean;
}

export interface DescriptiveHorizonResultV2 {
  window_bars: number;
  raw_signal_count: number;
  episode_count: number;
  conditioned_episode_mean: number;
  baseline_mean: number;
  difference: number;
  conditioned_win_rate: number;
  baseline_win_rate: number;
}

export interface RobustnessComparisonV2 {
  window_bars: number;
  method_a_mean_difference: MeanDifferenceResultV2;
  method_a_test: DependenceAwareTestResultV2;
  method_b_mean_difference: MeanDifferenceResultV2;
  method_b_test: DependenceAwareTestResultV2;
  conclusion_changes_materially: boolean;
}

export interface StatisticalValidationReportV2 {
  experiment_id: string;
  backtest_id: string;
  primary_window_bars: number;
  generated_at: string;
  seed: number;
  n_resamples: number;
  ci_level: number;
  block_length_multiplier: number;
  population: PopulationSummaryV2;
  method_a_mean_difference: MeanDifferenceResultV2;
  method_a_win_rate_difference: WinRateDifferenceResultV2;
  method_a_test: DependenceAwareTestResultV2;
  method_b_mean_difference: MeanDifferenceResultV2;
  method_b_win_rate_difference: WinRateDifferenceResultV2;
  method_b_test: DependenceAwareTestResultV2;
  effect_size: EffectSizeResultV2;
  power_analysis: PowerAnalysisResultV2;
  robustness: RobustnessComparisonV2;
  secondary_horizons: DescriptiveHorizonResultV2[];
}
