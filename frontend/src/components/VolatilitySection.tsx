import type { BearPutSpreadResponse } from "../types/bearPutSpread";
import { FormulaBox } from "./FormulaBox";
import { Tooltip } from "./Tooltip";
import { fmtPercent, fmtUsd } from "../utils/format";

interface VolatilitySectionProps {
  data: BearPutSpreadResponse;
  underlyingPrice: number;
  dte: number;
  longIvPercent: number;
  shortIvPercent: number;
}

export function VolatilitySection({
  data,
  underlyingPrice,
  dte,
  longIvPercent,
  shortIvPercent,
}: VolatilitySectionProps) {
  const { volatility } = data;
  return (
    <section className="section">
      <h2 className="section-title">5. Volatility</h2>
      <div className="metric-block">
        <div className="metric-heading">
          Average IV{" "}
          <Tooltip text="A simplified average of the two legs' quoted IVs. This is an educational approximation, not the true combined volatility of the spread." />
        </div>
        <FormulaBox
          formula={volatility.formula_average_iv}
          substitution={`(${longIvPercent}% + ${shortIvPercent}%) / 2`}
          result={fmtPercent(volatility.average_iv)}
        />
        <p className="disclaimer-note">
          Averaging the two legs' IVs is a simplification. It does not produce the exact
          volatility of the combined spread position.
        </p>
      </div>

      <div className="metric-block">
        <div className="metric-heading">
          Expected 1-Standard-Deviation Move{" "}
          <Tooltip text="A simplified estimate of a one-standard-deviation price move using implied volatility, scaled by the square root of time to expiration." />
        </div>
        <FormulaBox
          formula={volatility.formula_expected_move}
          substitution={`${fmtUsd(underlyingPrice)} x ${fmtPercent(volatility.average_iv)} x sqrt(${dte} / 365)`}
          result={`±${fmtUsd(volatility.expected_move)}`}
        />
        <div className="sigma-bounds">
          <span>1σ Lower: {fmtUsd(volatility.lower_1sd)}</span>
          <span>Current: {fmtUsd(underlyingPrice)}</span>
          <span>1σ Upper: {fmtUsd(volatility.upper_1sd)}</span>
        </div>
        <p className="disclaimer-note">
          This is an approximation based on annualized implied volatility and assumes a
          simplified, symmetric price distribution. It does not account for volatility skew or
          smile.
        </p>
      </div>
    </section>
  );
}
