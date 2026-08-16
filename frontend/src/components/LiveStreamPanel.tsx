import { useState } from "react";
import { useQuoteStream } from "../hooks/useQuoteStream";
import type { StreamProvider } from "../types/marketData";
import { fmtUsd } from "../utils/format";

interface LiveStreamPanelProps {
  symbol: string;
}

const STREAM_PROVIDERS: { value: StreamProvider; label: string }[] = [
  { value: "alpaca", label: "Alpaca" },
  { value: "massive", label: "Massive" },
];

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
 * Continuous, automatic updates for one symbol pushed over the
 * backend's WebSocket relay of a provider's real-time market-data
 * stream (Alpaca v0.1.12, Massive v0.1.13 -- see useQuoteStream) -- no
 * button, no manual refresh; this panel updates for as long as it's
 * mounted. The provider dropdown mirrors LiveQuotePanel's, restricted
 * to whichever providers actually support streaming (see
 * StreamProvider's definition) -- switching it tears down the old
 * WebSocket and opens a new one for the newly-selected provider.
 *
 * bid/ask can be null (v0.1.15): if Massive's real-time WebSocket
 * isn't entitled on the current plan, the backend falls back to
 * polling free-tier minute bars instead (see
 * backend/app/streaming/massive_stream.py's MassiveStream) -- a bar
 * has a price and volume but no bid/ask, so those render as "not
 * available" rather than a fabricated number, and the caveat line
 * below the quote card explains which case is showing.
 *
 * Deliberately separate from LiveQuotePanel (the manual, multi-
 * provider, one-shot lookup used elsewhere on this page and in the CSV
 * workflow): that component's click-to-fetch request/response model
 * doesn't fit a server-push stream, and this one doesn't replace it --
 * both stay on CalculatorPage side by side. Same "no credentials past
 * the backend" guarantee applies here: this component only ever sees
 * the already-normalized LiveQuote JSON riding on the WebSocket frames.
 */
export function LiveStreamPanel({ symbol }: LiveStreamPanelProps) {
  const [provider, setProvider] = useState<StreamProvider>("alpaca");
  const { status, statusDetail, quote, lastUpdated } = useQuoteStream(symbol, provider);

  return (
    <div className="live-stream-panel">
      <div className="live-stream-header">
        <div className="live-stream-controls">
          <span className="live-quote-label">Live stream</span>
          <select value={provider} onChange={(e) => setProvider(e.target.value as StreamProvider)}>
            {STREAM_PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
        <span className={`live-stream-status live-stream-status-${status}`}>
          <span className="live-stream-status-dot" />
          {STATUS_LABEL[status] ?? status}
        </span>
      </div>

      {statusDetail && <div className="live-stream-detail">{statusDetail}</div>}

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
            <dd>{quote.bid != null ? fmtUsd(quote.bid) : "not available"}</dd>
            <dt>Ask</dt>
            <dd>{quote.ask != null ? fmtUsd(quote.ask) : "not available"}</dd>
            <dt>Volume</dt>
            <dd>{quote.volume != null ? fmtVolume(quote.volume) : "not available"}</dd>
            <dt>Last update</dt>
            <dd>{lastUpdated ? fmtTime(lastUpdated) : fmtTime(new Date(quote.timestamp))}</dd>
          </dl>
          <div className="live-stream-caveat">
            {quote.bid == null && quote.ask == null
              ? "No live bid/ask on this plan -- price and volume are from the latest polled minute bar, not a real-time quote."
              : "Volume is cumulative since this stream connected, not the full session total."}
          </div>
        </div>
      ) : (
        <div className="live-stream-waiting">Waiting for the first tick…</div>
      )}
    </div>
  );
}
