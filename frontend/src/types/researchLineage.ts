// Types mirroring backend/app/models/research_lineage.py -- the "why
// did this event qualify?" view (spec section 12). signal_bar/
// outcome_bar are RAW market data; feature_record is DERIVED -- kept
// as clearly separate fields, never merged, so a reader never mistakes
// a derived value for a raw one.

import type { FeatureRecord } from "./features";
import type { HistoricalBar } from "./marketData";

export interface LineageConditionEvaluation {
  feature_id: string;
  feature_name: string;
  feature_description: string;
  operator: string;
  value: number | boolean;
  value_max: number | null;
  observed_value: number | boolean;
}

export interface EventLineage {
  experiment_id: string;
  symbol: string;
  timeframe: string;
  signal_timestamp: string;
  signal_bar: HistoricalBar | null;
  feature_record: FeatureRecord | null;
  condition_evaluations: LineageConditionEvaluation[];
  outcome_timestamp: string;
  outcome_bar: HistoricalBar | null;
  outcome_value: number;
  success: boolean;
}
