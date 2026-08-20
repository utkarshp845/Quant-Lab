import { useEffect, useState } from "react";
import { ApiError, computeFeatures, getFeatures, getStoredHistoricalBars, listExperiments } from "../api/client";
import type { FeatureField } from "../components/features/FeatureGroupCard";
import { FeatureGroupCard } from "../components/features/FeatureGroupCard";
import type { FeatureRecord } from "../types/features";
import type { HistoricalDataProvider, Timeframe } from "../types/marketData";
import { fmtNumberOrDash } from "../utils/researchFormat";

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

/** Market State Explorer (spec section 13 -- kept, repositioned from
 * "Feature Explorer"; the nav item stays "Features" per the simple
 * RESEARCH/DATA/FEATURES/CALCULATOR structure, but this page's own
 * framing answers "what was happening in the market?"): pick a
 * symbol/date/timeframe/provider, load whichever bars in that day
 * already have persisted FeatureRecords (GET /api/features/{symbol}),
 * offering to compute them (POST /api/features/compute) if none exist
 * yet, then pick one exact bar's timestamp to inspect RAW DATA (the
 * underlying OHLCV bar, GET /api/features's own sibling historical-
 * bars read) alongside every DERIVED feature value, grouped exactly as
 * the backend groups them (Price/Volume/Volatility/Market Context/
 * Price Position) -- kept visually separate per spec section 13's own
 * "never make derived values look like raw market data" rule. Every
 * number shown here came directly from the backend -- nothing is
 * computed client-side. Each feature row also shows whether/how many
 * Research experiments already use it, and a "Use this feature in
 * Research" action that navigates to Research prefilled with it --
 * referencing the existing feature, never duplicating it.
 */
export function FeatureExplorerPage({ onUseInResearch }: { onUseInResearch: (featureId: string) => void }) {
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
  const [rawBars, setRawBars] = useState<Record<string, { open: number; high: number; low: number; close: number; volume: number }>>({});
  const [experimentsUsingFeature, setExperimentsUsingFeature] = useState<Record<string, number>>({});

  // RESEARCH column (spec section 13): how many saved experiments
  // reference each feature_id in their own conditions -- fetched once,
  // read-only, never re-derives anything Research itself already owns.
  useEffect(() => {
    listExperiments()
      .then((experiments) => {
        const counts: Record<string, number> = {};
        for (const experiment of experiments) {
          for (const condition of experiment.conditions) {
            counts[condition.feature_id] = (counts[condition.feature_id] ?? 0) + 1;
          }
        }
        setExperimentsUsingFeature(counts);
      })
      .catch(() => setExperimentsUsingFeature({}));
  }, []);

  // RAW DATA (spec section 13): the underlying OHLCV bar for whichever
  // timestamps are currently loaded -- a sibling read of the same
  // already-saved historical_bars table Feature Engine computed these
  // FeatureRecords from, never re-derived from the feature values.
  useEffect(() => {
    if (!records || records.length === 0) {
      setRawBars({});
      return;
    }
    getStoredHistoricalBars({
      symbol: symbol.trim().toUpperCase(),
      start: date,
      end: date,
      timeframe,
      provider: provider as HistoricalDataProvider,
    })
      .then((res) => {
        const byTimestamp: Record<string, { open: number; high: number; low: number; close: number; volume: number }> = {};
        for (const bar of res.bars) byTimestamp[bar.timestamp] = bar;
        setRawBars(byTimestamp);
      })
      .catch(() => setRawBars({}));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [records]);

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
        <h1>Market State Explorer</h1>
        <p className="tagline">
          "What was happening in the market?" -- raw OHLCV alongside every derived Feature Engine value
          for one exact historical bar, kept visually distinct. Read-only and inspection-oriented -- for
          building and running experiments, use <strong>Research</strong> instead (each feature row below
          links there directly).
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
            <h2 className="section-title">Raw data</h2>
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
              {rawBars[selected.timestamp] ? (
                <>
                  <div>
                    <dt>OHLC</dt>
                    <dd>
                      {fmtNumberOrDash(rawBars[selected.timestamp].open, 2)} / {fmtNumberOrDash(rawBars[selected.timestamp].high, 2)} /{" "}
                      {fmtNumberOrDash(rawBars[selected.timestamp].low, 2)} / {fmtNumberOrDash(rawBars[selected.timestamp].close, 2)}
                    </dd>
                  </div>
                  <div>
                    <dt>Volume</dt>
                    <dd>{rawBars[selected.timestamp].volume.toLocaleString()}</dd>
                  </div>
                </>
              ) : (
                <div>
                  <dt>OHLCV</dt>
                  <dd className="research-gap-note">Not available (bar may have been removed since features were computed).</dd>
                </div>
              )}
            </dl>
          </section>

          <section className="section feature-explorer-dataset-banner">
            <h2 className="section-title">Feature computation metadata</h2>
            <dl className="feature-explorer-dataset-grid">
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
            experimentsUsingFeature={experimentsUsingFeature}
            onUseInResearch={onUseInResearch}
            fields={
              [
                { label: "Return (5m)", value: selected.price.return_5m, format: "percent", featureId: "price.return_5m" },
                { label: "Return (15m)", value: selected.price.return_15m, format: "percent", featureId: "price.return_15m" },
                { label: "Return (30m)", value: selected.price.return_30m, format: "percent", featureId: "price.return_30m" },
                { label: "Return (60m)", value: selected.price.return_60m, format: "percent", featureId: "price.return_60m" },
              ] as FeatureField[]
            }
          />
          <FeatureGroupCard
            title="Volume"
            experimentsUsingFeature={experimentsUsingFeature}
            onUseInResearch={onUseInResearch}
            fields={
              [
                { label: "Volume", value: selected.volume.volume, format: "int", featureId: "volume.volume" },
                { label: "Relative Volume (RVOL)", value: selected.volume.relative_volume, format: "number", featureId: "volume.relative_volume" },
                { label: "Volume Acceleration", value: selected.volume.volume_acceleration, format: "number", featureId: "volume.volume_acceleration" },
              ] as FeatureField[]
            }
          />
          <FeatureGroupCard
            title="Volatility"
            experimentsUsingFeature={experimentsUsingFeature}
            onUseInResearch={onUseInResearch}
            fields={
              [
                { label: "Realized Volatility (annualized)", value: selected.volatility.realized_volatility, format: "percent", featureId: "volatility.realized_volatility" },
                { label: "ATR (14-bar)", value: selected.volatility.atr, format: "number", featureId: "volatility.atr" },
                { label: "Volatility Ratio", value: selected.volatility.volatility_ratio, format: "number", featureId: "volatility.volatility_ratio" },
                { label: "Volatility Percentile (252-session)", value: selected.volatility.volatility_percentile, format: "percent", featureId: "volatility.volatility_percentile" },
              ] as FeatureField[]
            }
          />
          <FeatureGroupCard
            title="Market Context"
            experimentsUsingFeature={experimentsUsingFeature}
            onUseInResearch={onUseInResearch}
            subtitle={
              selected.market_context === null
                ? "This symbol is not configured for SPY/QQQ market context."
                : "SPY/QQQ returns and this symbol's relative strength against each, matched by exact timestamp."
            }
            fields={
              selected.market_context === null
                ? []
                : ([
                    { label: "SPY Return (5m)", value: selected.market_context.spy_return_5m, format: "percent", featureId: "market_context.spy_return_5m" },
                    { label: "SPY Return (60m)", value: selected.market_context.spy_return_60m, format: "percent", featureId: "market_context.spy_return_60m" },
                    { label: "QQQ Return (5m)", value: selected.market_context.qqq_return_5m, format: "percent", featureId: "market_context.qqq_return_5m" },
                    { label: "QQQ Return (60m)", value: selected.market_context.qqq_return_60m, format: "percent", featureId: "market_context.qqq_return_60m" },
                    {
                      label: "Relative Strength vs SPY (5m)",
                      value: selected.market_context.relative_strength_spy_5m,
                      format: "percent",
                      featureId: "market_context.relative_strength_spy_5m",
                    },
                    {
                      label: "Relative Strength vs SPY (60m)",
                      value: selected.market_context.relative_strength_spy_60m,
                      format: "percent",
                      featureId: "market_context.relative_strength_spy_60m",
                    },
                    {
                      label: "Relative Strength vs QQQ (5m)",
                      value: selected.market_context.relative_strength_qqq_5m,
                      format: "percent",
                      featureId: "market_context.relative_strength_qqq_5m",
                    },
                    {
                      label: "Relative Strength vs QQQ (60m)",
                      value: selected.market_context.relative_strength_qqq_60m,
                      format: "percent",
                      featureId: "market_context.relative_strength_qqq_60m",
                    },
                  ] as FeatureField[])
            }
          />
          <FeatureGroupCard
            title="Price Position"
            experimentsUsingFeature={experimentsUsingFeature}
            onUseInResearch={onUseInResearch}
            fields={
              [
                { label: "Distance from VWAP", value: selected.price_position.vwap_distance, format: "percent", featureId: "price_position.vwap_distance" },
                { label: "Distance from 20-bar MA", value: selected.price_position.ma20_distance, format: "percent", featureId: "price_position.ma20_distance" },
                { label: "Distance from 50-bar MA", value: selected.price_position.ma50_distance, format: "percent", featureId: "price_position.ma50_distance" },
                {
                  label: "Intraday Range Position",
                  value: selected.price_position.intraday_range_position,
                  format: "percent",
                  featureId: "price_position.intraday_range_position",
                },
              ] as FeatureField[]
            }
          />
        </>
      )}
    </div>
  );
}
