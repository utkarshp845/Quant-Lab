// Types mirroring backend/app/models/oos_partition.py (OOS / Holdout
// Partition Framework v1). A partition declares a symbol/timeframe/
// provider's already-stored bars split into a development window and
// a later, non-overlapping holdout window. No bars are duplicated --
// only date-range references. Immutable after creation.

export interface OOSPartitionCreateRequest {
  symbol: string;
  timeframe: string;
  provider: string;
  development_start: string;
  development_end: string;
  holdout_start: string;
  holdout_end: string;
  label?: string | null;
}

export interface OOSPartition {
  id: string;
  symbol: string;
  timeframe: string;
  provider: string;
  development_start: string;
  development_end: string;
  holdout_start: string;
  holdout_end: string;
  label: string | null;
  created_at: string;
}
