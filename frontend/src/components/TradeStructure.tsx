import type { BearPutSpreadResponse } from "../types/bearPutSpread";
import { FormulaBox } from "./FormulaBox";
import { Tooltip } from "./Tooltip";
import { fmtSigned, fmtUsd } from "../utils/format";

interface TradeStructureProps {
  data: BearPutSpreadResponse;
  longStrike: number;
  shortStrike: number;
}

export function TradeStructure({ data, longStrike, shortStrike }: TradeStructureProps) {
  const { debit, execution_check: exec } = data;
  return (
    <section className="section">
      <h2 className="section-title">2. Trade Structure</h2>
      <p className="section-subtitle">
        A bear put spread buys a higher-strike put and sells a lower-strike put. This app
        computes the debit two different ways -- a <strong>Mid Debit</strong> for theoretical
        analysis, and a <strong>Conservative Entry Debit</strong> for a realistic execution check
        -- see below.
      </p>
      <div className="structure-row">
        <div className="structure-leg accent-buy">
          <span className="badge badge-buy">BUY</span>
          <div>Long Put, Strike ${longStrike}</div>
          <div className="structure-price">
            Bid {fmtUsd(debit.long_put_bid)} / Ask {fmtUsd(debit.long_put_ask)} / Mid{" "}
            {fmtUsd(debit.long_put_mid)}
          </div>
        </div>
        <div className="structure-leg accent-sell">
          <span className="badge badge-sell">SELL</span>
          <div>Short Put, Strike ${shortStrike}</div>
          <div className="structure-price">
            Bid {fmtUsd(debit.short_put_bid)} / Ask {fmtUsd(debit.short_put_ask)} / Mid{" "}
            {fmtUsd(debit.short_put_mid)}
          </div>
        </div>
      </div>

      <div className="metric-block">
        <div className="metric-heading">
          Mid Debit (Primary){" "}
          <Tooltip text="Uses the midpoint of each leg's bid/ask, ignoring the spread -- what the market is 'really' quoting the trade at. This is the debit that drives every calculation elsewhere in this app: Max Loss/Profit, Breakeven, Probability, and Monte Carlo." />
        </div>
        <FormulaBox
          formula={debit.formula_mid_price}
          substitution={`Long: (${fmtUsd(debit.long_put_bid)} + ${fmtUsd(debit.long_put_ask)}) / 2 = ${fmtUsd(debit.long_put_mid)}`}
        />
        <FormulaBox
          substitution={`Short: (${fmtUsd(debit.short_put_bid)} + ${fmtUsd(debit.short_put_ask)}) / 2 = ${fmtUsd(debit.short_put_mid)}`}
        />
        <FormulaBox
          formula={debit.formula}
          substitution={`${fmtUsd(debit.long_put_mid)} - ${fmtUsd(debit.short_put_mid)}`}
          result={`${fmtUsd(debit.debit_per_share)} per share`}
        />
        <div className="metric-secondary">
          Mid Debit per contract = {fmtUsd(debit.debit_per_share)} x 100 ={" "}
          <strong>{fmtUsd(debit.debit_per_contract)}</strong>
        </div>
        <p className="disclaimer-note">
          Use Mid Debit for theoretical trade comparison, expected-value modeling, comparing many
          candidates, and understanding the market's quoted valuation.
        </p>
      </div>

      <div className="metric-block execution-check-panel">
        <div className="metric-heading">
          Execution Reality Check{" "}
          <Tooltip text="Uses the ask (what a buyer must pay) for the long put and the bid (what a seller receives) for the short put -- the worst realistic price if you had to cross the full spread on both legs at once. Does not feed any other calculation in this app." />
        </div>
        <FormulaBox
          formula={exec.formula_debit}
          substitution={`${fmtUsd(exec.long_put_ask)} - ${fmtUsd(exec.short_put_bid)}`}
          result={`${fmtUsd(exec.conservative_debit_per_share)} per share = ${fmtUsd(exec.conservative_debit_per_contract)} per contract`}
        />
        <div className="exec-check-grid">
          <div>
            <span className="exec-check-label">Conservative Max Loss</span>
            <span className="exec-check-value loss-text">{fmtUsd(exec.conservative_max_loss_per_contract)}</span>
          </div>
          <div>
            <span className="exec-check-label">Conservative Max Profit</span>
            <span className="exec-check-value profit-text">{fmtUsd(exec.conservative_max_profit_per_contract)}</span>
          </div>
          <div>
            <span className="exec-check-label">Conservative Breakeven</span>
            <span className="exec-check-value">{fmtUsd(exec.conservative_breakeven)}</span>
          </div>
          <div>
            <span className="exec-check-label">Slippage Cost vs. Mid</span>
            <span className="exec-check-value">{fmtSigned(exec.slippage_cost_per_contract)}</span>
          </div>
        </div>
        <p className="disclaimer-note">
          Use the Conservative Entry Debit for realistic entry cost, conservative P/L, and
          determining whether the trade survives transaction costs and slippage. Slippage Cost is
          the extra amount you'd pay entering at these prices instead of at the mid.
        </p>
      </div>
    </section>
  );
}
