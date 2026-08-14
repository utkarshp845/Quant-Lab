// Types mirroring the backend's pydantic models (backend/app/models).
// Keeping these in one place makes the API contract explicit and lets
// the whole UI be written against known shapes instead of `any`.

export interface UnderlyingInput {
  symbol: string;
  price: number;
  dte: number;
}

export interface OptionLegInput {
  strike: number;
  bid: number;
  ask: number;
  delta: number;
  /** Decimal fraction, e.g. 0.44 for 44%. The form displays/accepts a percentage and converts. */
  iv: number;
}

export interface BearPutSpreadRequest {
  underlying: UnderlyingInput;
  long_put: OptionLegInput;
  short_put: OptionLegInput;
}

export interface DebitBreakdown {
  long_put_ask: number;
  short_put_bid: number;
  debit_per_share: number;
  debit_per_contract: number;
  formula: string;
}

export interface RiskReward {
  strike_width: number;
  max_loss_per_contract: number;
  max_profit_per_share: number;
  max_profit_per_contract: number;
  breakeven: number;
  formula_strike_width: string;
  formula_max_loss: string;
  formula_max_profit: string;
  formula_breakeven: string;
}

export interface DeltaAnalysis {
  long_delta: number;
  short_delta: number;
  spread_delta: number;
  formula: string;
}

export interface VolatilityAnalysis {
  average_iv: number;
  expected_move: number;
  lower_1sd: number;
  upper_1sd: number;
  formula_average_iv: string;
  formula_expected_move: string;
}

export interface ProbabilityAnalysis {
  z_score: number;
  probability_below_breakeven: number;
  formula_z: string;
  formula_probability: string;
}

export interface PayoffScenario {
  expiration_price: number;
  long_put_value: number;
  short_put_value: number;
  spread_value: number;
  pl_per_share: number;
  pl_per_contract: number;
  is_profit?: boolean | null;
  label?: string | null;
}

export interface DistributionBucket {
  /** null means this bucket is the open-ended lower tail (extends to -infinity). */
  price_low: number | null;
  /** null means this bucket is the open-ended upper tail (extends to +infinity). */
  price_high: number | null;
  representative_price: number;
  probability: number;
  pl_per_share: number;
  pl_per_contract: number;
  is_profit: boolean;
  label: string;
}

export interface ProbabilityDistribution {
  buckets: DistributionBucket[];
  expected_value_per_share: number;
  expected_value_per_contract: number;
  total_probability: number;
  mean: number;
  std_dev: number;
  formula_bucket_probability: string;
  formula_expected_value: string;
}

export interface TradeSummary {
  symbol: string;
  underlying_price: number;
  dte: number;
  long_put_strike: number;
  short_put_strike: number;
  debit_per_contract: number;
  max_loss_per_contract: number;
  max_profit_per_contract: number;
  breakeven: number;
  spread_delta: number;
  average_iv: number;
  expected_move: number;
  probability_below_breakeven: number;
  expected_value_per_contract: number;
}

export interface BearPutSpreadResponse {
  debit: DebitBreakdown;
  risk_reward: RiskReward;
  delta: DeltaAnalysis;
  volatility: VolatilityAnalysis;
  probability: ProbabilityAnalysis;
  distribution: ProbabilityDistribution;
  payoff_table: PayoffScenario[];
  payoff_chart_points: PayoffScenario[];
  summary: TradeSummary;
}

export interface ValidationErrors {
  message: string;
  fieldErrors: string[];
}
