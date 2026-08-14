import type { BearPutSpreadResponse } from "../types/bearPutSpread";
import { FormulaBox } from "./FormulaBox";
import { Tooltip } from "./Tooltip";
import { fmtNumber, fmtPercent, fmtUsd } from "../utils/format";

interface ProbabilitySectionProps {
  data: BearPutSpreadResponse;
  underlyingPrice: number;
}

export function ProbabilitySection({ data, underlyingPrice }: ProbabilitySectionProps) {
  const { probability, risk_reward: rr, volatility } = data;
  return (
    <section className="section">
      <h2 className="section-title">6. Probability</h2>

      <div className="metric-block">
        <div className="metric-heading">Z-Score of Breakeven</div>
        <FormulaBox
          formula={probability.formula_z}
          substitution={`(${fmtUsd(rr.breakeven)} - ${fmtUsd(underlyingPrice)}) / ${fmtUsd(volatility.expected_move)}`}
          result={fmtNumber(probability.z_score, 3)}
        />
      </div>

      <div className="metric-block probability-callout">
        <div className="metric-heading">
          Approx. Probability of Finishing Below Breakeven{" "}
          <Tooltip text="A simplified normal-distribution estimate. It is NOT a guaranteed or exact probability." />
        </div>
        <div className="metric-big">{fmtPercent(probability.probability_below_breakeven)}</div>
        <FormulaBox
          formula={probability.formula_probability}
          substitution={`Normal CDF(${fmtNumber(probability.z_score, 3)})`}
          result={fmtPercent(probability.probability_below_breakeven)}
        />
        <div className="disclaimer-banner">
          <strong>Educational approximation</strong> — assumes a simplified normal price
          distribution. This is <em>not</em> the true probability of profit implied by the option
          chain. Real option-implied distributions can differ because of volatility skew and
          smile, interest rates, dividends/carry, and other factors this tool does not model.
        </div>
      </div>
    </section>
  );
}
