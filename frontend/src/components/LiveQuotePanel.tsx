import { useState } from "react";
import { ApiError, getLiveQuote } from "../api/client";
import type { LiveQuote, LiveQuoteProvider } from "../types/marketData";
import { fmtUsd } from "../utils/format";

interface LiveQuotePanelProps {
  symbol: string;
  /** The CSV chain's underlying_price for this symbol, if known --
   * used only to show how far a live quote has drifted from whatever
   * price was baked into the imported file, never fed into any
   * calculation. Omit for a standalone quote lookup with no chain
   * context (see the "TSLA" instance on CalculatorPage). */
  csvUnderlyingPrice?: number;
}

const PROVIDERS: { value: LiveQuoteProvider; label: string }[] = [
  { value: "alpaca", label: "Alpaca" },
  { value: "massive", label: "Massive" },
  { value: "schwab", label: "Schwab" },
];

const fmtVolume = (v: number) => v.toLocaleString();
const fmtTime = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/**
 * A small, optional widget: pick a provider, fetch a live quote for a
 * symbol, see the result -- Symbol / Provider / Price / Bid / Ask /
 * Volume / Updated. This is the proof that data actually flows
 * Provider -> Backend -> Normalized Data (LiveQuote) -> this UI; see
 * README section 13 for the full pipeline. Used two ways: embedded
 * next to a CSV-imported chain's underlying price (staleness check),
 * and standalone with symbol="TSLA" on CalculatorPage.
 *
 * Deliberately separate from the calculator: fetching a quote here
 * never changes any input field or triggers a recalculation. It's a
 * side-by-side reference, not a data source the analysis depends on --
 * same "investigate, don't auto-apply" principle the scanner already
 * holds to. No API credentials are ever present in this component,
 * the response it receives, or any request it makes -- they live only
 * in the backend (see app/config.py); this component only ever sees
 * the already-normalized LiveQuote JSON.
 */
export function LiveQuotePanel({ symbol, csvUnderlyingPrice }: LiveQuotePanelProps) {
  const [provider, setProvider] = useState<LiveQuoteProvider>("alpaca");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quote, setQuote] = useState<LiveQuote | null>(null);

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
    quote?.price != null && csvUnderlyingPrice != null ? quote.price - csvUnderlyingPrice : null;

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
        <div className="live-quote-card">
          <div className="live-quote-card-title">Live Market Data</div>
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
            <dt>Updated</dt>
            <dd>{fmtTime(quote.timestamp)}</dd>
          </dl>
          {driftFromCsv != null && (
            <div className={driftFromCsv >= 0 ? "profit-text" : "loss-text"}>
              {driftFromCsv >= 0 ? "+" : ""}
              {fmtUsd(driftFromCsv)} vs. CSV's {fmtUsd(csvUnderlyingPrice!)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
