import { useAlpacaQuoteStream } from "../hooks/useAlpacaQuoteStream";
import { fmtUsd } from "../utils/format";

interface LiveStreamPanelProps {
  symbol: string;
}

const STATUS_LABEL: Record<string, string> = {
  connecting: "Connecting…",
  connected: "Connected",
  disconnected: "Disconnected",
  error: "Error",
};

const fmtVolume = (v: number) => v.toLocaleString();
const fmtTime = (d: Date) =>
  d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/**
 * Continuous, automatic TSLA updates pushed over the backend's
 * WebSocket relay of Alpaca's real-time market-data stream (v0.1.12) --
 * no button, no manual refresh; this panel updates for as long as it's
 * mounted. Alpaca only, for now (see useAlpacaQuoteStream and
 * backend/app/streaming for the full scope and why).
 *
 * Deliberately separate from LiveQuotePanel (the manual, multi-provider,
 * one-shot lookup used elsewhere on this page and in the CSV workflow):
 * that component's click-to-fetch request/response model doesn't fit a
 * server-push stream, and this one doesn't replace it -- both stay on
 * CalculatorPage side by side. Same "no credentials past the backend"
 * guarantee applies here: this component only ever sees the already-
 * normalized LiveQuote JSON riding on the WebSocket frames.
 */
export function LiveStreamPanel({ symbol }: LiveStreamPanelProps) {
  const { status, statusDetail, quote, lastUpdated } = useAlpacaQuoteStream(symbol);

  return (
    <div className="live-stream-panel">
      <div className="live-stream-header">
        <span className="live-quote-label">Live stream (Alpaca)</span>
        <span className={`live-stream-status live-stream-status-${status}`}>
          <span className="live-stream-status-dot" />
          {STATUS_LABEL[status] ?? status}
        </span>
      </div>

      {statusDetail && status !== "connected" && <div className="live-stream-detail">{statusDetail}</div>}

      {quote ? (
        <div className="live-quote-card">
          <div className="live-quote-card-title">{symbol} — Live Market Data</div>
          <dl className="live-quote-card-grid">
            <dt>Symbol</dt>
            <dd>{quote.symbol}</dd>
            <dt>Provider</dt>
            <dd className="live-quote-card-provider">{quote.provider}</dd>
            <dt>Price</dt>
            <dd>{quote.price != null ? fmtUsd(quote.price) : "—"}</dd>
            <dt>Bid</dt>
            <dd>{fmtUsd(quote.bid)}</dd>
            <dt>Ask</dt>
            <dd>{fmtUsd(quote.ask)}</dd>
            <dt>Volume</dt>
            <dd>{quote.volume != null ? fmtVolume(quote.volume) : "not available"}</dd>
            <dt>Last update</dt>
            <dd>{lastUpdated ? fmtTime(lastUpdated) : fmtTime(new Date(quote.timestamp))}</dd>
          </dl>
          <div className="live-stream-caveat">
            Volume is cumulative since this stream connected, not the full session total.
          </div>
        </div>
      ) : (
        <div className="live-stream-waiting">Waiting for the first tick…</div>
      )}
    </div>
  );
}
