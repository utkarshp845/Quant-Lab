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
import { fmtNumber, fmtUsd } from "../utils/format";

interface SpreadBuilderPreviewProps {
  long: NormalizedOption;
  short: NormalizedOption;
}

/**
 * The "Spread Builder": as soon as a long and short put are both
 * picked from the chain, show what they construct -- instantly,
 * client-side, using the same Mid Debit math as the rest of the app
 * (calculations/bearPutSpread.ts). This is deliberately a compact,
 * glanceable summary (no formula boxes) -- the point is the "click
 * two legs, see the instrument" feel of building from market data.
 * "Analyze Spread" right below it remains the way to get the full
 * formula-by-formula breakdown, payoff chart, probability engine, and
 * Monte Carlo simulation -- this card is a preview, not a replacement.
 */
export function SpreadBuilderPreview({ long, short }: SpreadBuilderPreviewProps) {
  const longMid = midPrice(long.bid, long.ask);
  const shortMid = midPrice(short.bid, short.ask);
  const debitShare = debitPerShare(longMid, shortMid);
  const debitContract = debitPerContract(debitShare);
  const width = strikeWidth(long.strike, short.strike);
  const maxLoss = maxLossPerContract(debitShare);
  const maxProfitShare = maxProfitPerShare(width, debitShare);
  const maxProfitContract = maxProfitPerContract(maxProfitShare);
  const breakeven = breakevenPrice(long.strike, debitShare);
  const netDelta = spreadDelta(long.delta, short.delta);

  return (
    <div className="spread-builder-card">
      <div className="spread-builder-header">
        <span className="spread-builder-title">Bear Put Spread</span>
        <span className="spread-builder-legs">
          BUY {long.strike}P / SELL {short.strike}P
        </span>
      </div>
      <div className="spread-builder-grid">
        <div>
          <span className="spread-builder-label">Debit</span>
          <span className="spread-builder-value">{fmtUsd(debitContract)}</span>
        </div>
        <div>
          <span className="spread-builder-label">Max Loss</span>
          <span className="spread-builder-value loss-text">{fmtUsd(maxLoss)}</span>
        </div>
        <div>
          <span className="spread-builder-label">Max Profit</span>
          <span className="spread-builder-value profit-text">{fmtUsd(maxProfitContract)}</span>
        </div>
        <div>
          <span className="spread-builder-label">Breakeven</span>
          <span className="spread-builder-value">{fmtUsd(breakeven)}</span>
        </div>
        <div>
          <span className="spread-builder-label">Delta</span>
          <span className="spread-builder-value">{fmtNumber(netDelta)}</span>
        </div>
      </div>
      <p className="disclaimer-note">
        Live preview computed client-side from Mid Debit (bid/ask midpoint), same convention as
        the rest of this app. Click "Analyze Spread" below for the full formula-by-formula
        breakdown, payoff chart, probability engine, and Monte Carlo simulation.
      </p>
    </div>
  );
}
