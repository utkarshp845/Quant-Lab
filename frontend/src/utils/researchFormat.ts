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
