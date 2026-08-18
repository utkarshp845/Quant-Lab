// Types mirroring backend/app/models/features.py (Feature Engine v1).
// One sub-interface per contract section (PRICE/VOLUME/VOLATILITY/
// MARKET CONTEXT/PRICE POSITION), matching the backend's own
// one-sub-model-per-section split -- see that file's docstring for why.
// Every leaf value is `number | null`: null means "cannot honestly
// compute this" (insufficient history, a missing bar, a zero
// denominator, or -- market_context only -- this symbol isn't
// configured for it), never a fabricated 0.

export interface PriceFeatures {
  return_5m: number | null;
  return_15m: number | null;
  return_30m: number | null;
  return_60m: number | null;
}

export interface VolumeFeatures {
  volume: number; // the bar's own volume -- always known, never null
  relative_volume: number | null;
  volume_acceleration: number | null;
}

export interface VolatilityFeatures {
  realized_volatility: number | null;
  atr: number | null;
  volatility_ratio: number | null;
  volatility_percentile: number | null;
}

export interface MarketContextFeatures {
  spy_return_5m: number | null;
  spy_return_15m: number | null;
  spy_return_30m: number | null;
  spy_return_60m: number | null;
  qqq_return_5m: number | null;
  qqq_return_15m: number | null;
  qqq_return_30m: number | null;
  qqq_return_60m: number | null;
  relative_strength_spy_5m: number | null;
  relative_strength_spy_15m: number | null;
  relative_strength_spy_30m: number | null;
  relative_strength_spy_60m: number | null;
  relative_strength_qqq_5m: number | null;
  relative_strength_qqq_15m: number | null;
  relative_strength_qqq_30m: number | null;
  relative_strength_qqq_60m: number | null;
}

export interface PricePositionFeatures {
  vwap_distance: number | null;
  ma20_distance: number | null;
  ma50_distance: number | null;
  intraday_range_position: number | null;
}

export interface FeatureRecord {
  symbol: string;
  timestamp: string; // ISO datetime string -- the underlying bar's own timestamp
  timeframe: string;
  provider: string;
  calculated_at: string; // ISO datetime string -- when this row was computed, independent of `timestamp`
  feature_contract_version: string;
  price: PriceFeatures;
  volume: VolumeFeatures;
  volatility: VolatilityFeatures;
  // The whole sub-object, or null -- null means "this symbol is not
  // configured for market context at all" (a structural reason),
  // distinct from any leaf field inside a present object being null
  // (a data reason). See FeatureRecord's own docstring in the backend.
  market_context: MarketContextFeatures | null;
  price_position: PricePositionFeatures;
}

export interface FeatureComputeRequest {
  symbol: string;
  start_date: string; // YYYY-MM-DD
  end_date: string;
  timeframe: string;
  provider: string;
  include_market_context?: boolean; // defaults true server-side
}

export interface FeatureComputeResponse {
  symbol: string;
  timeframe: string;
  provider: string;
  start: string;
  end: string;
  bar_count: number;
  feature_count: number;
  market_context_applied: boolean;
  features: FeatureRecord[];
}

export interface FeatureRecordsResponse {
  symbol: string;
  provider: string;
  timeframe: string;
  start: string;
  end: string;
  feature_count: number;
  features: FeatureRecord[];
}

// Types mirroring backend/app/features/vocabulary.py (v0.1.24) -- the
// canonical feature vocabulary GET /api/features/vocabulary returns.
// This is what src/components/research/ConditionBuilder.tsx populates
// its feature dropdown from -- see that file's own docstring for why
// nothing in this frontend hardcodes the feature list either.

export type FeatureValueType = "numeric" | "boolean";

export type FeatureCategory = "price" | "volume" | "volatility" | "market_context" | "price_position";

export interface FeatureDefinition {
  feature_id: string; // "{category}.{field}", e.g. "price.return_5m"
  name: string;
  category: FeatureCategory;
  value_type: FeatureValueType;
  description: string;
  supported_operators: string[]; // e.g. ["<", "<=", "=", ">=", ">", "between"] for a numeric feature
  version: string;
  // True for the 16 SPY/QQQ-derived market_context features -- None
  // for any symbol not configured for market context at all (see
  // FeatureRecord.market_context above), not "insufficient history".
  market_context_only: boolean;
}

// Every leaf metric name across all 5 categories, used by the
// (currently backend-unsupported) segmentation UI to label which
// feature a user picked to bucket by -- see
// src/components/research/SegmentationPanel.tsx for why this list
// exists even though segmentation itself isn't wired to a real
// computation yet. NOT the source ConditionBuilder uses (that's
// FeatureDefinition/GET .../vocabulary above) -- kept here, unchanged,
// purely for SegmentationPanel's own still-disabled dropdown.
export const FEATURE_METRIC_LABELS: Record<string, string> = {
  "price.return_5m": "Return (5m)",
  "price.return_15m": "Return (15m)",
  "price.return_30m": "Return (30m)",
  "price.return_60m": "Return (60m)",
  "volume.volume": "Volume",
  "volume.relative_volume": "Relative Volume (RVOL)",
  "volume.volume_acceleration": "Volume Acceleration",
  "volatility.realized_volatility": "Realized Volatility",
  "volatility.atr": "ATR (14)",
  "volatility.volatility_ratio": "Volatility Ratio",
  "volatility.volatility_percentile": "Volatility Percentile",
  "market_context.relative_strength_spy_5m": "Relative Strength vs SPY (5m)",
  "market_context.relative_strength_spy_60m": "Relative Strength vs SPY (60m)",
  "market_context.relative_strength_qqq_5m": "Relative Strength vs QQQ (5m)",
  "market_context.relative_strength_qqq_60m": "Relative Strength vs QQQ (60m)",
  "price_position.vwap_distance": "Distance from VWAP",
  "price_position.ma20_distance": "Distance from 20-bar MA",
  "price_position.ma50_distance": "Distance from 50-bar MA",
  "price_position.intraday_range_position": "Intraday Range Position",
};
