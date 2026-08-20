import { HistoricalDataPanel } from "../components/HistoricalDataPanel";
import { LiveQuotePanel } from "../components/LiveQuotePanel";
import { LiveStreamPanel } from "../components/LiveStreamPanel";

/**
 * DATA -- the first stage of the research pipeline: "what data
 * exists, and how was it prepared?" Moved out of CalculatorPage.tsx
 * (redesign, this page did not exist before) -- LiveQuotePanel/
 * LiveStreamPanel/HistoricalDataPanel are the SAME, unmodified
 * components the Calculator used to render inline; only their parent
 * composition changed; no market-data logic was touched or duplicated.
 *
 * This is a provider/session inspection tool, not a research action:
 * Research reads already-saved historical bars through its own
 * symbol/date-range/timeframe/provider selectors (see ExperimentForm),
 * never through anything on this page directly.
 */
export function DataPage() {
  return (
    <div className="page">
      <header className="page-header">
        <h1>Data</h1>
        <p className="tagline">
          What data exists, and how was it prepared? Live quotes and streaming are side-by-side
          references — never inputs anything downstream applies automatically. Historical bars
          fetched, validated, and saved here are what Features and Research read.
        </p>
      </header>

      <section className="live-data-proof-of-concept">
        <p className="live-data-proof-of-concept-label">
          Live market data — Provider → Backend → Normalized Data → this UI. Picking a provider or
          fetching a quote never feeds the Calculator or Research automatically.
        </p>
        <LiveQuotePanel symbol="TSLA" />
        <LiveStreamPanel symbol="TSLA" />
      </section>

      <section className="historical-data-test">
        <p className="historical-data-test-label">
          Historical bars — fetch, validate, and save OHLCV bars for a symbol/timeframe/provider,
          or diff a CSV export against a live provider for the same period. This is the raw data
          Feature Engine and Research read; nothing downstream recomputes it.
        </p>
        <HistoricalDataPanel />
      </section>
    </div>
  );
}
