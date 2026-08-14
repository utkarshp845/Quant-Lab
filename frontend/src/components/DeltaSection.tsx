import type { BearPutSpreadResponse } from "../types/bearPutSpread";
import { FormulaBox } from "./FormulaBox";
import { Tooltip } from "./Tooltip";
import { fmtNumber } from "../utils/format";

interface DeltaSectionProps {
  data: BearPutSpreadResponse;
}

export function DeltaSection({ data }: DeltaSectionProps) {
  const { delta } = data;
  return (
    <section className="section">
      <h2 className="section-title">4. Spread Delta</h2>
      <div className="metric-block">
        <div className="metric-heading">
          Net Spread Delta{" "}
          <Tooltip text="Approximate sensitivity of the option's value to a $1 move in the underlying. Delta changes over time and with price." />
        </div>
        <div className="delta-row">
          <span>Long Delta: {fmtNumber(delta.long_delta)}</span>
          <span>Short Delta: {fmtNumber(delta.short_delta)}</span>
        </div>
        <FormulaBox
          formula={delta.formula}
          substitution={`${fmtNumber(delta.long_delta)} - (${fmtNumber(delta.short_delta)})`}
          result={fmtNumber(delta.spread_delta)}
        />
        <p className="disclaimer-note">
          This is only an approximation of the position's current delta. Delta changes as the
          underlying price moves and as time passes (gamma and theta effects are not modeled in
          v0.1).
        </p>
      </div>
    </section>
  );
}
