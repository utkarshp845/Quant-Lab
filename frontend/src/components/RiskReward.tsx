import type { BearPutSpreadResponse } from "../types/bearPutSpread";
import { FormulaBox } from "./FormulaBox";
import { Tooltip } from "./Tooltip";
import { fmtUsd } from "../utils/format";

interface RiskRewardProps {
  data: BearPutSpreadResponse;
  longStrike: number;
  shortStrike: number;
}

export function RiskReward({ data, longStrike, shortStrike }: RiskRewardProps) {
  const { risk_reward: rr, debit } = data;
  return (
    <section className="section">
      <h2 className="section-title">3. Risk / Reward</h2>
      <div className="risk-reward-grid">
        <div className="metric-card loss-card">
          <div className="metric-heading">
            Max Loss{" "}
            <Tooltip text="The maximum amount that can be lost if the underlying finishes at or above the long put strike at expiration, ignoring commissions and fees." />
          </div>
          <div className="metric-big">{fmtUsd(rr.max_loss_per_contract)}</div>
          <FormulaBox
            formula={rr.formula_max_loss}
            substitution={`${fmtUsd(debit.debit_per_share)} x 100`}
            result={fmtUsd(rr.max_loss_per_contract)}
          />
        </div>

        <div className="metric-card profit-card">
          <div className="metric-heading">
            Max Profit{" "}
            <Tooltip text="The maximum amount the spread can be worth at expiration minus the initial debit." />
          </div>
          <div className="metric-big">{fmtUsd(rr.max_profit_per_contract)}</div>
          <FormulaBox
            formula={rr.formula_strike_width}
            substitution={`${longStrike} - ${shortStrike}`}
            result={`${fmtUsd(rr.strike_width)} strike width`}
          />
          <FormulaBox
            formula={rr.formula_max_profit}
            substitution={`(${fmtUsd(rr.strike_width)} - ${fmtUsd(debit.debit_per_share)}) x 100`}
            result={fmtUsd(rr.max_profit_per_contract)}
          />
        </div>

        <div className="metric-card breakeven-card">
          <div className="metric-heading">
            Breakeven{" "}
            <Tooltip text="The underlying price at expiration where the spread's intrinsic value exactly equals the debit paid." />
          </div>
          <div className="metric-big">{fmtUsd(rr.breakeven)}</div>
          <FormulaBox
            formula={rr.formula_breakeven}
            substitution={`${longStrike} - ${fmtUsd(debit.debit_per_share)}`}
            result={fmtUsd(rr.breakeven)}
          />
        </div>
      </div>
    </section>
  );
}
