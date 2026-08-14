import { useState } from "react";
import { payoffAtExpiration } from "../calculations/bearPutSpread";
import { FormulaBox } from "./FormulaBox";
import { fmtUsd, fmtSigned } from "../utils/format";

interface PayoffCalculatorProps {
  longStrike: number;
  shortStrike: number;
  debitPerShare: number;
  defaultPrice: number;
}

/**
 * Lets the user type an arbitrary hypothetical expiration price and see
 * the resulting P/L immediately. Computed client-side with the same
 * formulas as the backend (see src/calculations/bearPutSpread.ts,
 * mirroring backend/app/calculations/bear_put_spread.py) so there is
 * no network round trip per keystroke.
 */
export function PayoffCalculator({
  longStrike,
  shortStrike,
  debitPerShare,
  defaultPrice,
}: PayoffCalculatorProps) {
  const [priceInput, setPriceInput] = useState(String(defaultPrice));
  const parsedPrice = Number(priceInput);
  const valid = Number.isFinite(parsedPrice);

  const result = valid
    ? payoffAtExpiration(longStrike, shortStrike, parsedPrice, debitPerShare)
    : null;

  return (
    <section className="section">
      <h2 className="section-title">7. Payoff Calculator</h2>
      <p className="section-subtitle">
        Enter a hypothetical price for the underlying at expiration to see the spread's value and
        profit/loss at that price.
      </p>
      <label className="field payoff-price-field">
        <span className="field-label">Expiration Price ($)</span>
        <input
          type="number"
          step="0.01"
          value={priceInput}
          onChange={(e) => setPriceInput(e.target.value)}
        />
      </label>

      {result && (
        <div className="payoff-breakdown">
          <FormulaBox
            formula="Long Put Intrinsic Value = max(Long Strike - Expiration Price, 0)"
            substitution={`max(${longStrike} - ${fmtUsd(result.expirationPrice)}, 0)`}
            result={fmtUsd(result.longPutValue)}
          />
          <FormulaBox
            formula="Short Put Intrinsic Value = max(Short Strike - Expiration Price, 0)"
            substitution={`max(${shortStrike} - ${fmtUsd(result.expirationPrice)}, 0)`}
            result={fmtUsd(result.shortPutValue)}
          />
          <FormulaBox
            formula="Spread Intrinsic Value = Long Put Intrinsic - Short Put Intrinsic"
            substitution={`${fmtUsd(result.longPutValue)} - ${fmtUsd(result.shortPutValue)}`}
            result={fmtUsd(result.spreadValue)}
          />
          <FormulaBox
            formula="P/L per share = Spread Intrinsic Value - Debit"
            substitution={`${fmtUsd(result.spreadValue)} - ${fmtUsd(debitPerShare)}`}
            result={fmtSigned(result.plPerShare)}
          />
          <div className={`metric-big ${result.plPerShare >= 0 ? "profit-text" : "loss-text"}`}>
            P/L per contract: {fmtSigned(result.plPerContract)}
          </div>
        </div>
      )}
    </section>
  );
}
