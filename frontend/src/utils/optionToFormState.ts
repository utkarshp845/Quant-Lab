import type { NormalizedOption } from "../types/csvImport";
import type { BearPutSpreadFormState } from "../types/form";

/**
 * Maps a selected long/short NormalizedOption pair onto the same
 * BearPutSpreadFormState the manual-entry form uses. This is the one
 * place that translates "two contracts from a CSV chain" into "form
 * fields" -- both CsvImportWorkflow's chain browser and SpreadScanner's
 * results table call this same function so there is exactly one
 * mapping to keep correct, not two copies that could drift apart.
 */
export function optionToFormState(long: NormalizedOption, short: NormalizedOption): BearPutSpreadFormState {
  return {
    underlying: {
      symbol: long.symbol,
      price: String(long.underlying_price),
      dte: String(long.dte),
    },
    longPut: {
      strike: String(long.strike),
      bid: String(long.bid),
      ask: String(long.ask),
      delta: String(long.delta),
      ivPercent: String(long.implied_volatility * 100),
    },
    shortPut: {
      strike: String(short.strike),
      bid: String(short.bid),
      ask: String(short.ask),
      delta: String(short.delta),
      ivPercent: String(short.implied_volatility * 100),
    },
  };
}
