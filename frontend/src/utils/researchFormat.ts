import type { FeatureCondition } from "../types/research";
import { fmtNumber, fmtPercent } from "./format";

/** Null-safe wrapper around the existing fmtPercent/fmtNumber helpers
 * (src/utils/format.ts) for the feature/research values that are
 * `number | null` -- `null` always renders as an explicit dash, never
 * "0%"/"0.00", matching the backend's own "null means cannot honestly
 * compute this" convention (never a fabricated 0). */
export function fmtPercentOrDash(value: number | null, digits = 2): string {
  return value === null ? "—" : fmtPercent(value, digits);
}

export function fmtNumberOrDash(value: number | null, digits = 4): string {
  return value === null ? "—" : fmtNumber(value, digits);
}

export function fmtIntOrDash(value: number | null): string {
  return value === null ? "—" : Math.round(value).toLocaleString();
}

/** Renders one FeatureCondition as plain text, e.g.
 * "price_position.vwap_distance > 0" or
 * "volatility.volatility_percentile between 0.5 and 0.7" -- used
 * anywhere an experiment's conditions need a human-readable summary
 * (ExperimentResultsView, ExperimentCompare) without needing the full
 * feature vocabulary fetched just to look up a display name; the raw
 * feature_id (e.g. "price_position.vwap_distance") is itself a
 * readable, self-describing string. */
export function describeFeatureCondition(condition: FeatureCondition): string {
  if (condition.operator === "between") {
    return `${condition.feature_id} between ${condition.value} and ${condition.value_max}`;
  }
  return `${condition.feature_id} ${condition.operator} ${condition.value}`;
}

/** Every condition in an experiment, AND-joined -- the text form of
 * "Price vs VWAP > 0 AND RSI 14 between 50 and 70 AND Volume Ratio >
 * 1.5" this integration's spec describes. */
export function describeConditions(conditions: FeatureCondition[]): string {
  return conditions.map(describeFeatureCondition).join(" AND ");
}
