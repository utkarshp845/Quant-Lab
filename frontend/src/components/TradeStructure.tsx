import type { BearPutSpreadResponse } from "../types/bearPutSpread";
import { FormulaBox } from "./FormulaBox";
import { Tooltip } from "./Tooltip";
import { fmtUsd } from "../utils/format";

interface TradeStructureProps {
  data: BearPutSpreadResponse;
  longStrike: number;
  shortStrike: number;
}

export function TradeStructure({ data, longStrike, shortStrike }: TradeStructureProps) {
  const { debit } = data;
  return (
    <section className="section">
      <h2 className="section-title">2. Trade Structure</h2>
      <p className="section-subtitle">
        A bear put spread buys a higher-strike put and sells a lower-strike put. We buy at the
        ask (what a buyer pays) and sell at the bid (what a seller receives).
      </p>
      <div className="structure-row">
        <div className="structure-leg accent-buy">
          <span className="badge badge-buy">BUY</span>
          <div>Long Put, Strike ${longStrike}</div>
          <div className="structure-price">Execution price = Ask = {fmtUsd(debit.long_put_ask)}</div>
        </div>
        <div className="structure-leg accent-sell">
          <span className="badge badge-sell">SELL</span>
          <div>Short Put, Strike ${shortStrike}</div>
          <div className="structure-price">Execution price = Bid = {fmtUsd(debit.short_put_bid)}</div>
        </div>
      </div>

      <div className="metric-block">
        <div className="metric-heading">
          Debit <Tooltip text="The estimated amount paid to enter the spread, using the long option's ask and the short option's bid." />
        </div>
        <FormulaBox
          formula={debit.formula}
          substitution={`${fmtUsd(debit.long_put_ask)} - ${fmtUsd(debit.short_put_bid)}`}
          result={`${fmtUsd(debit.debit_per_share)} per share`}
        />
        <div className="metric-secondary">
          Debit per contract = {fmtUsd(debit.debit_per_share)} x 100 ={" "}
          <strong>{fmtUsd(debit.debit_per_contract)}</strong>
        </div>
      </div>
    </section>
  );
}
