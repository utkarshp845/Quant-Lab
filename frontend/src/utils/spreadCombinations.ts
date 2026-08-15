// The "first tiny scanner": examine every possible long-put + short-put
// combination WITHIN the already-uploaded CSV chain -- not a market-wide
// scan across symbols (see README's Phase 5 roadmap for that, still
// future scope). This is the same Mid Debit math already used by
// SpreadBuilderPreview and PayoffCalculator (calculations/bearPutSpread.ts),
// just run once per combination instead of once for a single hand-picked
// pair.
//
// A valid bear put spread combination requires: both legs are puts, both
// legs share the same expiration (you cannot spread across expirations),
// and the long strike is strictly greater than the short strike (see
// BearPutSpreadRequest's check_strike_ordering validator on the backend --
// the same rule, just applied here to decide which pairs are even worth
// computing instead of rejecting one bad pair after the fact).

import {
  breakevenPrice,
  debitPerContract,
  debitPerShare,
  maxLossPerContract,
  maxProfitPerContract,
  maxProfitPerShare,
  midPrice,
  spreadDelta,
  strikeWidth,
} from "../calculations/bearPutSpread";
import type { NormalizedOption } from "../types/csvImport";

export interface SpreadCombination {
  long: NormalizedOption;
  short: NormalizedOption;
  expiration: string;
  dte: number;
  debitPerContract: number;
  maxLossPerContract: number;
  maxProfitPerContract: number;
  breakeven: number;
  netDelta: number;
}

/**
 * Generates every valid long/short put combination across all
 * expirations present in `contracts` (typically pre-filtered to one
 * symbol by the caller), with every combination's metrics computed.
 */
export function generateSpreadCombinations(contracts: NormalizedOption[]): SpreadCombination[] {
  const puts = contracts.filter((c) => c.option_type === "put");

  // Combinations must stay within one expiration -- group first.
  const byExpiration = new Map<string, NormalizedOption[]>();
  for (const put of puts) {
    const group = byExpiration.get(put.expiration);
    if (group) {
      group.push(put);
    } else {
      byExpiration.set(put.expiration, [put]);
    }
  }

  const combinations: SpreadCombination[] = [];
  for (const group of byExpiration.values()) {
    for (const long of group) {
      for (const short of group) {
        if (long.strike <= short.strike) continue; // not a valid bear put spread

        const longMid = midPrice(long.bid, long.ask);
        const shortMid = midPrice(short.bid, short.ask);
        const debitShare = debitPerShare(longMid, shortMid);
        const width = strikeWidth(long.strike, short.strike);
        const maxProfitShare = maxProfitPerShare(width, debitShare);

        combinations.push({
          long,
          short,
          expiration: long.expiration,
          dte: long.dte,
          debitPerContract: debitPerContract(debitShare),
          maxLossPerContract: maxLossPerContract(debitShare),
          maxProfitPerContract: maxProfitPerContract(maxProfitShare),
          breakeven: breakevenPrice(long.strike, debitShare),
          netDelta: spreadDelta(long.delta, short.delta),
        });
      }
    }
  }
  return combinations;
}

export interface ScannerFilters {
  dteMin: number | null;
  dteMax: number | null;
  longDeltaMin: number | null;
  longDeltaMax: number | null;
  shortDeltaMin: number | null;
  shortDeltaMax: number | null;
  maxLossLimit: number | null;
}

export const EMPTY_FILTERS: ScannerFilters = {
  dteMin: null,
  dteMax: null,
  longDeltaMin: null,
  longDeltaMax: null,
  shortDeltaMin: null,
  shortDeltaMax: null,
  maxLossLimit: null,
};

/**
 * Applies the scanner's filter criteria. Every bound is optional (null
 * = unbounded) so an empty filter set matches everything. Delta bounds
 * are compared with min/max resolved automatically, since put deltas
 * are negative and it's easy to type "-.55 to -.70" (i.e. numerically
 * backwards) without meaning to.
 */
export function filterCombinations(
  combinations: SpreadCombination[],
  filters: ScannerFilters,
): SpreadCombination[] {
  return combinations.filter((c) => {
    if (filters.dteMin !== null && c.dte < filters.dteMin) return false;
    if (filters.dteMax !== null && c.dte > filters.dteMax) return false;
    if (!inRange(c.long.delta, filters.longDeltaMin, filters.longDeltaMax)) return false;
    if (!inRange(c.short.delta, filters.shortDeltaMin, filters.shortDeltaMax)) return false;
    if (filters.maxLossLimit !== null && c.maxLossPerContract > filters.maxLossLimit) return false;
    return true;
  });
}

function inRange(value: number, boundA: number | null, boundB: number | null): boolean {
  // Missing bounds are unbounded on that side; whichever of the two
  // provided bounds is numerically smaller becomes the floor -- so
  // typing a delta range as "-.55 to -.70" (natural to say, backwards
  // as raw numbers) still works.
  const a = boundA ?? -Infinity;
  const b = boundB ?? Infinity;
  const lo = Math.min(a, b);
  const hi = Math.max(a, b);
  return value >= lo && value <= hi;
}
