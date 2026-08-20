// Types mirroring backend/app/models/statistical_validation.py
// (Statistical Validation V1). A derived, on-demand report -- never
// persisted -- always recomputed from a Backtest's own already-
// persisted signals. Episode-level (non-overlapping) inference is
// ALWAYS the authoritative population; raw, clustered signals are
// shown alongside for comparison only (see RobustnessCheck).

export interface EpisodeGroupingRule {
  description: string;
  bar_interval_minutes: number;
}

export interface SampleSizes {
  window_bars: number;
  raw_signal_count: number;
  episode_count: number;
  baseline_count: number;
}

export interface MeanDifferenceCI {
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

export interface WinRateDifferenceCI {
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

export interface SessionBoundaryStats {
  window_bars: number;
  n_conditioned_observations: number;
  n_conditioned_crossing: number;
  n_baseline_observations: number;
  n_baseline_crossing: number;
}

export interface HorizonResult {
  window_bars: number;
  is_primary: boolean;
  sample_sizes: SampleSizes;
  mean_difference: MeanDifferenceCI;
  win_rate_difference: WinRateDifferenceCI;
  session_boundary: SessionBoundaryStats;
}

export interface PermutationTestResult {
  window_bars: number;
  observed_mean_difference: number;
  p_value_two_sided: number;
  n_permutations: number;
  n_conditioned: number;
  n_baseline: number;
  seed: number;
}

export interface EffectSizeResult {
  window_bars: number;
  cohens_d: number;
  pooled_stdev: number;
  interpretation: string;
}

export interface RobustnessCheck {
  window_bars: number;
  raw_mean_difference: number;
  raw_p_value: number;
  raw_n: number;
  episode_mean_difference: number;
  episode_p_value: number;
  episode_n: number;
}

export interface StatisticalValidationReport {
  experiment_id: string;
  backtest_id: string;
  primary_window_bars: number;
  generated_at: string;
  seed: number;
  n_bootstrap: number;
  n_permutations: number;
  episode_rule: EpisodeGroupingRule;
  horizons: HorizonResult[];
  primary_permutation_test: PermutationTestResult;
  primary_effect_size: EffectSizeResult;
  robustness_check: RobustnessCheck;
}
