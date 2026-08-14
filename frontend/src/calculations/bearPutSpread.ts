// Pure TypeScript mirror of backend/app/calculations/bear_put_spread.py.
//
// Why duplicate the math on the frontend at all, if the backend is the
// source of truth? Two reasons:
//   1. It lets the UI recompute instantly as you type, without a
//      network round trip, which matters for a "learning instrument"
//      where you want to see numbers move as you nudge an input.
//   2. Having the *same* small formulas written twice, side by side,
//      in two languages, is itself a transparency device: you can
//      diff them by eye and confirm neither hides anything the other
//      doesn't.
//
// The backend's pytest suite (backend/tests/test_calculations.py) is
// what "counts" as the verified source of truth. This file intentionally
// stays just as small and readable as its Python counterpart.

export const CONTRACT_MULTIPLIER = 100;

/** Debit (per share) = Long Put Ask - Short Put Bid. */
export function debitPerShare(longPutAsk: number, shortPutBid: number): number {
  return longPutAsk - shortPutBid;
}

/** Debit (per contract) = Debit per share x 100 shares/contract. */
export function debitPerContract(debitShare: number): number {
  return debitShare * CONTRACT_MULTIPLIER;
}

/** Max Loss (per contract) = Debit x 100. */
export function maxLossPerContract(debitShare: number): number {
  return debitShare * CONTRACT_MULTIPLIER;
}

/** Strike Width = Long Put Strike - Short Put Strike. */
export function strikeWidth(longStrike: number, shortStrike: number): number {
  return longStrike - shortStrike;
}

/** Max Profit (per share) = Strike Width - Debit. */
export function maxProfitPerShare(strikeWidthValue: number, debitShare: number): number {
  return strikeWidthValue - debitShare;
}

/** Max Profit (per contract) = Max Profit per share x 100. */
export function maxProfitPerContract(maxProfitShare: number): number {
  return maxProfitShare * CONTRACT_MULTIPLIER;
}

/** Breakeven = Long Put Strike - Debit. */
export function breakevenPrice(longStrike: number, debitShare: number): number {
  return longStrike - debitShare;
}

/** Spread Delta = Long Put Delta - Short Put Delta. */
export function spreadDelta(longDelta: number, shortDelta: number): number {
  return longDelta - shortDelta;
}

/** Average IV = (Long IV + Short IV) / 2. Simplified educational approximation. */
export function averageIv(longIv: number, shortIv: number): number {
  return (longIv + shortIv) / 2;
}

/** Expected 1-sigma move = Underlying Price x Average IV x sqrt(DTE / 365). */
export function expectedMove(underlyingPrice: number, avgIv: number, dte: number): number {
  return underlyingPrice * avgIv * Math.sqrt(dte / 365);
}

/** Lower/Upper 1-sigma boundaries = Underlying Price -/+ Expected Move. */
export function oneSigmaBounds(
  underlyingPrice: number,
  expectedMoveValue: number,
): [number, number] {
  return [underlyingPrice - expectedMoveValue, underlyingPrice + expectedMoveValue];
}

/** z = (Breakeven - Underlying Price) / Expected Move. */
export function zScore(
  breakeven: number,
  underlyingPrice: number,
  expectedMoveValue: number,
): number {
  if (expectedMoveValue === 0) {
    throw new Error(
      "Expected move is zero, so a z-score cannot be computed. Check that DTE and IV are both greater than zero.",
    );
  }
  return (breakeven - underlyingPrice) / expectedMoveValue;
}

/**
 * Standard normal CDF via the Abramowitz & Stegun 7.1.26 rational
 * approximation (max error ~1.5e-7). JavaScript has no built-in erf,
 * unlike Python's math.erf, so this well-known closed-form
 * approximation stands in for it -- it is not a lookup table and not
 * a black box: the formula is fully visible below.
 */
export function normalCdf(z: number): number {
  const sign = z < 0 ? -1 : 1;
  const x = Math.abs(z) / Math.SQRT2;

  // Abramowitz & Stegun formula 7.1.26 for erf(x).
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;

  const t = 1 / (1 + p * x);
  const y = 1 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
  const erf = sign * y;

  return 0.5 * (1 + erf);
}

/** Approximate probability of finishing below breakeven = Normal CDF(z). */
export function probabilityBelowBreakeven(z: number): number {
  return normalCdf(z);
}

/** Long Put Intrinsic Value = max(Long Strike - Expiration Price, 0). */
export function longPutIntrinsicValue(longStrike: number, expirationPrice: number): number {
  return Math.max(longStrike - expirationPrice, 0);
}

/** Short Put Intrinsic Value = max(Short Strike - Expiration Price, 0). */
export function shortPutIntrinsicValue(shortStrike: number, expirationPrice: number): number {
  return Math.max(shortStrike - expirationPrice, 0);
}

/** Spread Intrinsic Value = Long Put Intrinsic - Short Put Intrinsic. */
export function spreadIntrinsicValue(longIntrinsic: number, shortIntrinsic: number): number {
  return longIntrinsic - shortIntrinsic;
}

/** P/L per share = Spread Intrinsic Value - Debit. */
export function payoffPlPerShare(spreadIntrinsic: number, debitShare: number): number {
  return spreadIntrinsic - debitShare;
}

/** P/L per contract = P/L per share x 100. */
export function payoffPlPerContract(plShare: number): number {
  return plShare * CONTRACT_MULTIPLIER;
}

export interface PayoffResult {
  expirationPrice: number;
  longPutValue: number;
  shortPutValue: number;
  spreadValue: number;
  plPerShare: number;
  plPerContract: number;
}

/** Chains the payoff steps above for one expiration price. */
export function payoffAtExpiration(
  longStrike: number,
  shortStrike: number,
  expirationPrice: number,
  debitShare: number,
): PayoffResult {
  const longValue = longPutIntrinsicValue(longStrike, expirationPrice);
  const shortValue = shortPutIntrinsicValue(shortStrike, expirationPrice);
  const spreadValue = spreadIntrinsicValue(longValue, shortValue);
  const plShare = payoffPlPerShare(spreadValue, debitShare);
  const plContract = payoffPlPerContract(plShare);
  return {
    expirationPrice,
    longPutValue: longValue,
    shortPutValue: shortValue,
    spreadValue,
    plPerShare: plShare,
    plPerContract: plContract,
  };
}
