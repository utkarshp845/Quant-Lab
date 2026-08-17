import { useState } from "react";
import { ApiError, computeFeatures, getFeatures } from "../api/client";
import type { FeatureField } from "../components/features/FeatureGroupCard";
import { FeatureGroupCard } from "../components/features/FeatureGroupCard";
import type { FeatureRecord } from "../types/features";
import type { Timeframe } from "../types/marketData";

const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "1h", "1d"];
const PROVIDERS = [
  { value: "alpaca", label: "Alpaca" },
  { value: "massive", label: "Massive" },
  { value: "csv", label: "CSV / manually saved" },
];
const SYMBOL_SUGGESTIONS = ["TSLA", "NVDA", "SPY", "QQQ", "MCL"];

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Feature Explorer -- an inspection/debugging tool (per this
 * feature's own scope note), not the primary research workflow: pick
 * a symbol/date/timeframe/provider, load whichever bars in that day
 * already have persisted FeatureRecords (GET /api/features/{symbol}),
 * offering to compute them (POST /api/features/compute) if none exist
 * yet, then pick one exact bar's timestamp to inspect every feature
 * value grouped exactly as the backend groups them (Price/Volume/
 * Volatility/Market Context/Price Position). Every number shown here
 * came directly from the backend -- nothing is computed client-side.
 */
export function FeatureExplorerPage() {
  const [symbol, setSymbol] = useState("TSLA");
  const [date, setDate] = useState(todayIso());
  const [timeframe, setTimeframe] = useState<Timeframe>("5m");
  const [provider, setProvider] = useState(PROVIDERS[0].value);

  const [records, setRecords] = useState<FeatureRecord[] | null>(null);
  const [loadLoading, setLoadLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [computeLoading, setComputeLoading] = useState(false);
  const [computeError, setComputeError] = useState<string | null>(null);
  const [barsSeenButNoFeatures, setBarsSeenButNoFeatures] = useState<number | null>(null);

  const [selectedTimestamp, setSelectedTimestamp] = useState<string | null>(null);

  async function handleLoad() {
    setLoadLoading(true);
    setLoadError(null);
    setComputeError(null);
    setBarsSeenButNoFeatures(null);
    setSelectedTimestamp(null);
    try {
      const result = await getFeatures({ symbol: symbol.trim().toUpperCase(), start: date, end: date, timeframe, provider });
      setRecords(result.features);
      if (result.features.length > 0) setSelectedTimestamp(result.features[0].timestamp);
    } catch (err) {
      setRecords(null);
      setLoadError(err instanceof ApiError ? err.message : "Could not reach the backend. Is it running on http://localhost:8000?");
    } finally {
      setLoadLoading(false);
    }
  }

  async function handleCompute() {
    setComputeLoading(true);
    setComputeError(null);
    try {
      const result = await computeFeatures({
        symbol: symbol.trim().toUpperCase(),
        start_date: date,
        end_date: date,
        timeframe,
        provider,
      });
      setRecords(result.features);
      setSelectedTimestamp(result.features.length > 0 ? result.features[0].timestamp : null);
      setBarsSeenButNoFeatures(result.feature_count === 0 ? result.bar_count : null);
    } catch (err) {
      setComputeError(
        err instanceof ApiError ? err.message : "Could not reach the backend. Is it running on http://localhost:8000?",
      );
    } finally {
      setComputeLoading(false);
    }
  }

  const selected = records?.find((r) => r.timestamp === selectedTimestamp) ?? null;

  return (
    <div className="page feature-explorer-page">
      <header className="page-header">
        <h1>Feature Explorer</h1>
        <p className="tagline">
          Inspect the exact, already-persisted feature values Feature Engine v1 computed for one historical bar.
          Read-only and debugging-oriented -- for building and running experiments, use{" "}
          <strong>Research</strong> instead.
        </p>
      </header>

      <section className="section feature-explorer-controls">
        <div className="feature-explorer-controls-row">
          <label className="field">
            <span className="field-label">Symbol</span>
            <input
              list="feature-explorer-symbols"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              placeholder="TSLA"
            />
            <datalist id="feature-explorer-symbols">
              {SYMBOL_SUGGESTIONS.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
          </label>
          <label className="field">
            <span className="field-label">Date</span>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">Timeframe</span>
            <select value={timeframe} onChange={(e) => setTimeframe(e.target.value as Timeframe)}>
              {TIMEFRAMES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Provider (dataset)</span>
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              {PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="feature-explorer-actions">
          <button type="button" onClick={handleLoad} disabled={loadLoading || !symbol.trim()}>
            {loadLoading ? "Loading…" : "Load features for this date"}
          </button>
        </div>
        {loadError && <div className="error-banner">{loadError}</div>}
      </section>

      {records !== null && records.length === 0 && (
        <section className="section">
          <p className="feature-explorer-empty">
            No feature records are saved yet for {symbol.toUpperCase()} on {date} ({timeframe}, {provider}).
          </p>
          <button type="button" onClick={handleCompute} disabled={computeLoading}>
            {computeLoading ? "Computing…" : "Compute features for this date"}
          </button>
          {computeError && <div className="error-banner">{computeError}</div>}
          {barsSeenButNoFeatures !== null && (
            <p className="feature-explorer-empty">
              {barsSeenButNoFeatures === 0
                ? "No historical bars exist for this symbol/date/timeframe/provider either -- fetch and save historical data first on the Calculator page, then try again."
                : `${barsSeenButNoFeatures} bar(s) were found but none produced a feature record -- unexpected; check the backend logs.`}
            </p>
          )}
        </section>
      )}

      {records !== null && records.length > 0 && (
        <section className="section">
          <h2 className="section-title">
            {records.length} bar{records.length === 1 ? "" : "s"} available on {date}
          </h2>
          <p className="section-subtitle">Pick an exact timestamp to inspect.</p>
          <div className="feature-explorer-timestamp-list">
            {records.map((r) => (
              <button
                key={r.timestamp}
                type="button"
                className={r.timestamp === selectedTimestamp ? "feature-explorer-ts feature-explorer-ts-active" : "feature-explorer-ts"}
                onClick={() => setSelectedTimestamp(r.timestamp)}
              >
                {new Date(r.timestamp).toLocaleString(undefined, {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </button>
            ))}
          </div>
        </section>
      )}

      {selected && (
        <>
          <section className="section feature-explorer-dataset-banner">
            <dl className="feature-explorer-dataset-grid">
              <div>
                <dt>Symbol</dt>
                <dd>{selected.symbol}</dd>
              </div>
              <div>
                <dt>Exact bar timestamp</dt>
                <dd>
                  <code>{selected.timestamp}</code>
                </dd>
              </div>
              <div>
                <dt>Timeframe</dt>
                <dd>{selected.timeframe}</dd>
              </div>
              <div>
                <dt>Source dataset (provider)</dt>
                <dd>{selected.provider}</dd>
              </div>
              <div>
                <dt>Computed at</dt>
                <dd>
                  <code>{selected.calculated_at}</code>
                </dd>
              </div>
              <div>
                <dt>Contract version</dt>
                <dd>{selected.feature_contract_version}</dd>
              </div>
            </dl>
          </section>

          <FeatureGroupCard
            title="Price"
            fields={
              [
                { label: "Return (5m)", value: selected.price.return_5m, format: "percent" },
                { label: "Return (15m)", value: selected.price.return_15m, format: "percent" },
                { label: "Return (30m)", value: selected.price.return_30m, format: "percent" },
                { label: "Return (60m)", value: selected.price.return_60m, format: "percent" },
              ] as FeatureField[]
            }
          />
          <FeatureGroupCard
            title="Volume"
            fields={
              [
                { label: "Volume", value: selected.volume.volume, format: "int" },
                { label: "Relative Volume (RVOL)", value: selected.volume.relative_volume, format: "number" },
                { label: "Volume Acceleration", value: selected.volume.volume_acceleration, format: "number" },
              ] as FeatureField[]
            }
          />
          <FeatureGroupCard
            title="Volatility"
            fields={
              [
                { label: "Realized Volatility (annualized)", value: selected.volatility.realized_volatility, format: "percent" },
                { label: "ATR (14-bar)", value: selected.volatility.atr, format: "number" },
                { label: "Volatility Ratio", value: selected.volatility.volatility_ratio, format: "number" },
                { label: "Volatility Percentile (252-session)", value: selected.volatility.volatility_percentile, format: "percent" },
              ] as FeatureField[]
            }
          />
          <FeatureGroupCard
            title="Market Context"
            subtitle={
              selected.market_context === null
                ? "This symbol is not configured for SPY/QQQ market context."
                : "SPY/QQQ returns and this symbol's relative strength against each, matched by exact timestamp."
            }
            fields={
              selected.market_context === null
                ? []
                : ([
                    { label: "SPY Return (5m)", value: selected.market_context.spy_return_5m, format: "percent" },
                    { label: "SPY Return (60m)", value: selected.market_context.spy_return_60m, format: "percent" },
                    { label: "QQQ Return (5m)", value: selected.market_context.qqq_return_5m, format: "percent" },
                    { label: "QQQ Return (60m)", value: selected.market_context.qqq_return_60m, format: "percent" },
                    {
                      label: "Relative Strength vs SPY (5m)",
                      value: selected.market_context.relative_strength_spy_5m,
                      format: "percent",
                    },
                    {
                      label: "Relative Strength vs SPY (60m)",
                      value: selected.market_context.relative_strength_spy_60m,
                      format: "percent",
                    },
                    {
                      label: "Relative Strength vs QQQ (5m)",
                      value: selected.market_context.relative_strength_qqq_5m,
                      format: "percent",
                    },
                    {
                      label: "Relative Strength vs QQQ (60m)",
                      value: selected.market_context.relative_strength_qqq_60m,
                      format: "percent",
                    },
                  ] as FeatureField[])
            }
          />
          <FeatureGroupCard
            title="Price Position"
            fields={
              [
                { label: "Distance from VWAP", value: selected.price_position.vwap_distance, format: "percent" },
                { label: "Distance from 20-bar MA", value: selected.price_position.ma20_distance, format: "percent" },
                { label: "Distance from 50-bar MA", value: selected.price_position.ma50_distance, format: "percent" },
                {
                  label: "Intraday Range Position",
                  value: selected.price_position.intraday_range_position,
                  format: "percent",
                },
              ] as FeatureField[]
            }
          />
        </>
      )}
    </div>
  );
}
