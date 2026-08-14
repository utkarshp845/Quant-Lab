// Form state uses strings for every field so inputs can be edited
// freely (including transient invalid/empty states) before being
// parsed into the numeric BearPutSpreadRequest sent to the API.

export interface UnderlyingFormState {
  symbol: string;
  price: string;
  dte: string;
}

export interface OptionLegFormState {
  strike: string;
  bid: string;
  ask: string;
  delta: string;
  /** Entered and displayed as a percentage, e.g. "44" for 44%. */
  ivPercent: string;
}

export interface BearPutSpreadFormState {
  underlying: UnderlyingFormState;
  longPut: OptionLegFormState;
  shortPut: OptionLegFormState;
}

export const GRADUATION_EXAMPLE: BearPutSpreadFormState = {
  underlying: { symbol: "XYZ", price: "82.00", dte: "30" },
  longPut: { strike: "85", bid: "5.00", ask: "5.10", delta: "-0.58", ivPercent: "44" },
  shortPut: { strike: "77", bid: "0.71", ask: "0.85", delta: "-0.29", ivPercent: "42" },
};
