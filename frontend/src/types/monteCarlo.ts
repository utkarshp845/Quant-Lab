// Types mirroring backend/app/models/monte_carlo.py.

import type { BearPutSpreadRequest } from "./bearPutSpread";

export interface MonteCarloRequest extends BearPutSpreadRequest {
  num_simulations: number;
  seed?: number | null;
}

export interface SamplePath {
  index: number;
  simulated_price: number;
  pl_per_contract: number;
}

export interface HistogramBin {
  price_low: number;
  price_high: number;
  frequency: number;
  count: number;
  pl_per_contract: number;
  is_profit: boolean;
}

export interface MonteCarloResult {
  num_simulations: number;
  probability_of_profit: number;
  probability_of_max_loss: number;
  probability_of_max_profit: number;
  expected_value_per_contract: number;
  expected_return_pct: number;
  median_pl_per_contract: number;
  percentile_5_pl_per_contract: number;
  percentile_25_pl_per_contract: number;
  percentile_75_pl_per_contract: number;
  percentile_95_pl_per_contract: number;
  expected_gain_per_contract: number;
  expected_loss_per_contract: number;
  closed_form_expected_value_per_contract: number;
  sample_paths: SamplePath[];
  histogram: HistogramBin[];
  formula_expected_return: string;
}
