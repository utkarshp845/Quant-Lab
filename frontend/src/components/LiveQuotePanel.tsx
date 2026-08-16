import { useState } from "react";
import { ApiError, getLiveQuote } from "../api/client";
import type { LiveQuoteProvider, Quote } from "../types/marketData";
import { fmtUsd } from "../utils/format";

interface LiveQuotePanelProps {
  symbol: string;
  /** The CSV chain's underlying_price for this symbol, if known --
   * used only to show how far a live quote has drifted from whatever
   * price was baked into the imported file, never fed into any
   * calculation. */
  csvUnderlyingPrice?: number;
}

const PROVIDERS: { value: LiveQuoteProvider; label: string }[] = [
  { value: "alpaca", label: "Alpaca" },
  { value: "massive", label: "Massive" },
  { value: "schwab", label: "Schwab" },
];

/**
 * A small, optional widget: pick a provider, fetch a live quote for
 * the symbol currently being browsed, see how it compares to the CSV
 * file's (possibly stale) underlying_price. This is the first place
 * in the app where Alpaca/Massive/Schwab data reaches the UI --
 * everything before this was backend-only (see README section 13).
 *
 * Deliberately separate from the calculator: fetching a quote here
 * never changes any input field or triggers a recalculation. It's a
 * side-by-side reference, not a data source the analysis depends on --
 * same "investigate, don't auto-apply" principle the scanner already
 * holds to.
 */
export function LiveQuotePanel({ symbol, csvUnderlyingPrice }: LiveQuotePanelProps) {
  const [provider, setProvider] = useState<LiveQuoteProvider>("alpaca");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quote, setQuote] = useState<Quote | null>(null);

  const handleFetch = async () => {
    setLoading(true);
    setError(null);
    setQuote(null);
    try {
      const result = await getLiveQuote(symbol, provider);
      setQuote(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the backend for a live quote.");
    } finally {
      setLoading(false);
    }
  };

  const driftFromCsv =
    quote?.last != null && csvUnderlyingPrice != null ? quote.last - csvUnderlyingPrice : null;

  return (
    <div className="live-quote-panel">
      <div className="live-quote-controls">
        <span className="live-quote-label">Live quote</span>
        <select
          value={provider}
          onChange={(e) => setProvider(e.target.value as LiveQuoteProvider)}
          disabled={loading}
        >
          {PROVIDERS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
        <button type="button" className="live-quote-btn" onClick={handleFetch} disabled={loading}>
          {loading ? "Fetching…" : `Get ${symbol} quote`}
        </button>
      </div>

      {error && <div className="live-quote-error">{error}</div>}

      {quote && (
        <div className="live-quote-result">
          <span>
            Bid <strong>{fmtUsd(quote.bid)}</strong>
          </span>
          <span>
            Ask <strong>{fmtUsd(quote.ask)}</strong>
          </span>
          {quote.last != null && (
            <span>
              Last <strong>{fmtUsd(quote.last)}</strong>
            </span>
          )}
          <span className="live-quote-source">
            via {quote.timestamp.source} at {new Date(quote.timestamp.value).toLocaleString()}
          </span>
          {driftFromCsv != null && (
            <span className={driftFromCsv >= 0 ? "profit-text" : "loss-text"}>
              {driftFromCsv >= 0 ? "+" : ""}
              {fmtUsd(driftFromCsv)} vs. CSV's {fmtUsd(csvUnderlyingPrice!)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
