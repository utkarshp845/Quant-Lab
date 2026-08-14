import type { BearPutSpreadResponse } from "../types/bearPutSpread";
import { fmtNumber, fmtPercent, fmtSigned, fmtUsd } from "../utils/format";

interface TradeSummaryProps {
  data: BearPutSpreadResponse;
}

export function TradeSummary({ data }: TradeSummaryProps) {
  const { summary } = data;
  return (
    <section className="section">
      <h2 className="section-title">Trade Analysis</h2>
      <p className="section-subtitle">
        A summary of the figures above. This is analysis, not a recommendation -- Pandey Quant Lab
        does not tell you whether to buy or sell this spread.
      </p>
      <div className="summary-card">
        <div className="summary-row">
          <span>Symbol</span>
          <strong>{summary.symbol}</strong>
        </div>
        <div className="summary-row">
          <span>Current Price</span>
          <strong>{fmtUsd(summary.underlying_price)}</strong>
        </div>
        <div className="summary-row">
          <span>DTE</span>
          <strong>{summary.dte}</strong>
        </div>
        <div className="summary-row">
          <span>Long Put</span>
          <strong>${summary.long_put_strike} strike</strong>
        </div>
        <div className="summary-row">
          <span>Short Put</span>
          <strong>${summary.short_put_strike} strike</strong>
        </div>
        <div className="summary-row">
          <span>Debit (Mid, primary)</span>
          <strong>{fmtUsd(summary.debit_per_contract)}</strong>
        </div>
        <div className="summary-row">
          <span>Conservative Entry Debit</span>
          <strong>{fmtUsd(summary.conservative_debit_per_contract)}</strong>
        </div>
        <div className="summary-row">
          <span>Max Loss</span>
          <strong className="loss-text">{fmtUsd(summary.max_loss_per_contract)}</strong>
        </div>
        <div className="summary-row">
          <span>Max Profit</span>
          <strong className="profit-text">{fmtUsd(summary.max_profit_per_contract)}</strong>
        </div>
        <div className="summary-row">
          <span>Breakeven</span>
          <strong>{fmtUsd(summary.breakeven)}</strong>
        </div>
        <div className="summary-row">
          <span>Net Delta</span>
          <strong>{fmtNumber(summary.spread_delta)}</strong>
        </div>
        <div className="summary-row">
          <span>Average IV</span>
          <strong>{fmtPercent(summary.average_iv)}</strong>
        </div>
        <div className="summary-row">
          <span>Expected Move</span>
          <strong>{fmtSigned(summary.expected_move)}</strong>
        </div>
        <div className="summary-row">
          <span>Approx. Probability Below Breakeven</span>
          <strong>{fmtPercent(summary.probability_below_breakeven)}</strong>
        </div>
        <div className="summary-row">
          <span>Expected Value (simplified model)</span>
          <strong className={summary.expected_value_per_contract >= 0 ? "profit-text" : "loss-text"}>
            {fmtSigned(summary.expected_value_per_contract)}
          </strong>
        </div>
      </div>
    </section>
  );
}
