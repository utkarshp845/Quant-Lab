// Types mirroring backend/app/models/experiment_freeze.py (Experiment
// Freeze & Provenance v1). Freezing commits an Experiment's hypothesis
// definition -- DRAFT -> FROZEN -> OOS_EVALUATED -> ARCHIVED (see
// ExperimentLifecycleState on the Experiment type itself, types/research.ts).

import type { FeatureCondition, Outcome } from "./research";
import type { OOSPartition } from "./oosPartitions";

export interface OOSPartitionLinkRequest {
  oos_partition_id: string;
}

export interface ExperimentFreezeSnapshot {
  experiment_id: string;
  hypothesis_hash: string;
  name: string;
  hypothesis: string;
  symbol: string;
  timeframe: string;
  provider: string;
  start_date: string;
  end_date: string;
  feature_contract_version: string;
  conditions: FeatureCondition[];
  outcome: Outcome;
  oos_partition_id: string | null;
  experiment_created_at: string;
  frozen_at: string;
}

export interface ExperimentProvenance {
  experiment_id: string;
  lifecycle_state: "draft" | "frozen" | "oos_evaluated" | "archived";
  hypothesis_hash: string;
  name: string;
  hypothesis: string;
  symbol: string;
  timeframe: string;
  provider: string;
  start_date: string;
  end_date: string;
  feature_contract_version: string;
  conditions: FeatureCondition[];
  outcome: Outcome;
  oos_partition: OOSPartition | null;
  experiment_created_at: string;
  frozen_at: string;
  archived_at: string | null;
}
