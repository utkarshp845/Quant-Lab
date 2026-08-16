import { useState } from "react";
import {
  ApiError,
  compareHistoricalCsv,
  getHistoricalBars,
  getStoredHistoricalBars,
  saveHistoricalBars,
} from "../api/client";
import type {
  HistoricalBar,
  HistoricalBarsResponse,
  HistoricalComparisonResponse,
  HistoricalDataProvider,
  HistoricalDataSymbol,
  Timeframe,
} from "../types/marketData";
import { fmtNumber } from "../utils/format";

const PROVIDERS: { value: HistoricalDataProvider; label: string }[] = [
  { value: "alpaca", label: "Alpaca" },
  { value: "massive", label: "Massive" },
];

const SYMBOLS: HistoricalDataSymbol[] = ["TSLA", "NVDA"];
const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "1h", "1d"];

const ROWS_PREVIEW_COUNT = 5;

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysAgoIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

const fmtTimestamp = (iso: string) => new Date(iso).toLocaleString([], { hour12: false });

/**
 * A minimal test surface for GET /market-data/{symbol}/history and
 * POST /market-data/history/compare (v0.1.16) -- proves historical bars
 * flow Provider -> Backend -> HistoricalBar (normalized) -> this UI, the
 * same "investigate, don't auto-apply" proof-of-concept LiveQuotePanel /
 * LiveStreamPanel already give for live quotes/streaming. Fetching bars
 * or running a comparison here never touches the calculator, the CSV
 * import pipeline, or any quantitative calculation.
 *
 * Two independent actions, sharing the same symbol/provider/timeframe/
 * date controls: "Fetch bars" (what does the provider return?) and
 * "Compare against a CSV" (does an existing OHLCV export agree with
 * it?) -- the latter is this feature's most important test. See
 * backend/app/api/historical_comparison.py's module docstring for why
 * this repo's own CSV fixture (an options-CHAIN snapshot for "MCL")
 * can't be used here: this comparison needs an OHLCV bar export for
 * TSLA or NVDA over the same period, not an option chain.
 */
export function HistoricalDataPanel() {
  const [provider, setProvider] = useState<HistoricalDataProvider>("alpaca");
  const [symbol, setSymbol] = useState<HistoricalDataSymbol>("TSLA");
  const [timeframe, setTimeframe] = useState<Timeframe>("1d");
  const [start, setStart] = useState(daysAgoIso(14));
  const [end, setEnd] = useState(todayIso());

  const [fetchLoading, setFetchLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [result, setResult] = useState<HistoricalBarsResponse | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);
  const [comparison, setComparison] = useState<HistoricalComparisonResponse | null>(null);

  // Historical Data Storage sub-section (v0.1.17) -- deliberately its
  // own result/status state, separate from `result` above: "Fetch bars"
  // above is the existing provider-fetch test surface (also feeds the
  // CSV comparison); this block additionally proves Save/Load work,
  // using the same shared provider/symbol/timeframe/start/end controls.
  const [storageAction, setStorageAction] = useState<"fetch" | "save" | "load" | null>(null);
  const [storageLoading, setStorageLoading] = useState(false);
  const [storageError, setStorageError] = useState<string | null>(null);
  const [storageResult, setStorageResult] = useState<HistoricalBarsResponse | null>(null);
  const [storageStatus, setStorageStatus] = useState<string | null>(null);

  const handleFetch = async () => {
    setFetchLoading(true);
    setFetchError(null);
    setResult(null);
    try {
      const data = await getHistoricalBars({ symbol, start, end, timeframe, provider });
      setResult(data);
    } catch (err) {
      setFetchError(err instanceof ApiError ? err.message : "Could not reach the backend for historical bars.");
    } finally {
      setFetchLoading(false);
    }
  };

  const handleCompare = async () => {
    if (!file) {
      setCompareError("Choose a CSV file of OHLCV bars first.");
      return;
    }
    setCompareLoading(true);
    setCompareError(null);
    setComparison(null);
    try {
      const data = await compareHistoricalCsv(file, { symbol, start, end, timeframe, provider });
      setComparison(data);
    } catch (err) {
      setCompareError(err instanceof ApiError ? err.message : "Could not reach the backend to run the comparison.");
    } finally {
      setCompareLoading(false);
    }
  };

  const handleStorageFetch = async () => {
    setStorageAction("fetch");
    setStorageLoading(true);
    setStorageError(null);
    setStorageStatus(null);
    try {
      const data = await getHistoricalBars({ symbol, start, end, timeframe, provider });
      setStorageResult(data);
      setStorageStatus(`Fetched ${data.bar_count} bar(s) from ${data.provider} -- not yet saved.`);
    } catch (err) {
      setStorageResult(null);
      setStorageError(err instanceof ApiError ? err.message : "Could not reach the backend to fetch bars.");
    } finally {
      setStorageLoading(false);
    }
  };

  const handleSaveToDatabase = async () => {
    if (!storageResult || storageResult.bars.length === 0) {
      setStorageError("Fetch bars first -- there is nothing to save yet.");
      return;
    }
    setStorageAction("save");
    setStorageLoading(true);
    setStorageError(null);
    try {
      const saved = await saveHistoricalBars(storageResult.bars);
      setStorageStatus(
        `Saved: ${saved.total} bar(s) total -- ${saved.inserted} new, ${saved.skipped_duplicates} already in the database.`,
      );
    } catch (err) {
      setStorageError(err instanceof ApiError ? err.message : "Could not reach the backend to save these bars.");
    } finally {
      setStorageLoading(false);
    }
  };

  const handleLoadFromDatabase = async () => {
    setStorageAction("load");
    setStorageLoading(true);
    setStorageError(null);
    try {
      const data = await getStoredHistoricalBars({ symbol, start, end, timeframe, provider });
      setStorageResult(data);
      setStorageStatus(
        data.bar_count > 0
          ? `Loaded ${data.bar_count} bar(s) from the database -- ${provider} was not contacted.`
          : `No bars stored yet for ${symbol}/${timeframe}/${provider} in this date range.`,
      );
    } catch (err) {
      setStorageResult(null);
      setStorageError(err instanceof ApiError ? err.message : "Could not reach the backend to load stored bars.");
    } finally {
      setStorageLoading(false);
    }
  };

  const firstBars = result ? result.bars.slice(0, ROWS_PREVIEW_COUNT) : [];
  const lastBars = result ? result.bars.slice(-ROWS_PREVIEW_COUNT) : [];

  return (
    <div className="historical-data-panel">
      <div className="historical-data-controls">
        <label>
          Provider
          <select value={provider} onChange={(e) => setProvider(e.target.value as HistoricalDataProvider)}>
            {PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Symbol
          <select value={symbol} onChange={(e) => setSymbol(e.target.value as HistoricalDataSymbol)}>
            {SYMBOLS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          Timeframe
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value as Timeframe)}>
            {TIMEFRAMES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label>
          Start
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label>
          End
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
        <button type="button" onClick={handleFetch} disabled={fetchLoading}>
          {fetchLoading ? "Fetching…" : "Fetch bars"}
        </button>
      </div>

      {fetchError && <div className="live-quote-error">{fetchError}</div>}

      {result && (
        <div className="historical-data-result">
          <div className="historical-data-summary">
            <span>
              <strong>{result.bar_count}</strong> bar{result.bar_count === 1 ? "" : "s"}
            </span>
            {result.bar_count > 0 && (
              <>
                <span>
                  First: <code>{result.bars[0].timestamp}</code>
                </span>
                <span>
                  Last: <code>{result.bars[result.bars.length - 1].timestamp}</code>
                </span>
              </>
            )}
          </div>
          {result.bar_count === 0 && <p className="historical-data-empty">No bars returned for this range.</p>}
          {result.bar_count > 0 && (
            <>
              <BarTable title="First rows" bars={firstBars} />
              {result.bar_count > ROWS_PREVIEW_COUNT && <BarTable title="Last rows" bars={lastBars} />}
            </>
          )}
        </div>
      )}

      <div className="historical-compare-section">
        <p className="historical-compare-label">
          Compare against a CSV of OHLCV bars for the same symbol/timeframe/period. Row count, timestamp,
          OHLC, and volume differences are reported as-is -- nothing here is auto-corrected.
        </p>
        <div className="historical-compare-controls">
          <input
            type="file"
            accept=".csv"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setComparison(null);
              setCompareError(null);
            }}
          />
          <button type="button" onClick={handleCompare} disabled={compareLoading || !file}>
            {compareLoading ? "Comparing…" : "Compare against CSV"}
          </button>
        </div>

        {compareError && <div className="live-quote-error">{compareError}</div>}

        {comparison && <ComparisonReport data={comparison} />}
      </div>

      <div className="historical-storage-section">
        <p className="historical-storage-label">
          Historical Data Storage — persists fetched bars to a local database (v0.1.17) and reads them
          back without contacting the provider again. Uses the same symbol/provider/timeframe/date
          controls above. Nothing is saved automatically; each action below is deliberate.
        </p>
        <div className="historical-storage-controls">
          <button type="button" onClick={handleStorageFetch} disabled={storageLoading}>
            {storageLoading && storageAction === "fetch" ? "Fetching…" : "Fetch from Provider"}
          </button>
          <button
            type="button"
            onClick={handleSaveToDatabase}
            disabled={storageLoading || !storageResult || storageResult.bars.length === 0}
          >
            {storageLoading && storageAction === "save" ? "Saving…" : "Save to Database"}
          </button>
          <button type="button" onClick={handleLoadFromDatabase} disabled={storageLoading}>
            {storageLoading && storageAction === "load" ? "Loading…" : "Load from Database"}
          </button>
        </div>

        {storageError && <div className="live-quote-error">{storageError}</div>}

        {(storageResult || storageStatus) && (
          <dl className="historical-storage-status-grid">
            <dt>Provider</dt>
            <dd>{storageResult?.provider ?? provider}</dd>
            <dt>Symbol</dt>
            <dd>{storageResult?.symbol ?? symbol}</dd>
            <dt>Timeframe</dt>
            <dd>{storageResult?.timeframe ?? timeframe}</dd>
            <dt>Number of bars</dt>
            <dd>{storageResult?.bar_count ?? 0}</dd>
            <dt>First timestamp</dt>
            <dd>{storageResult && storageResult.bar_count > 0 ? storageResult.bars[0].timestamp : "—"}</dd>
            <dt>Last timestamp</dt>
            <dd>
              {storageResult && storageResult.bar_count > 0
                ? storageResult.bars[storageResult.bars.length - 1].timestamp
                : "—"}
            </dd>
            <dt>Storage status</dt>
            <dd className="historical-storage-status-message">{storageStatus ?? "—"}</dd>
          </dl>
        )}
      </div>
    </div>
  );
}

function BarTable({ title, bars }: { title: string; bars: HistoricalBar[] }) {
  return (
    <div className="historical-bar-table-wrap">
      <div className="historical-bar-table-title">{title}</div>
      <table className="historical-bar-table">
        <thead>
          <tr>
            <th>Timestamp</th>
            <th>Open</th>
            <th>High</th>
            <th>Low</th>
            <th>Close</th>
            <th>Volume</th>
          </tr>
        </thead>
        <tbody>
          {bars.map((b) => (
            <tr key={b.timestamp}>
              <td>{fmtTimestamp(b.timestamp)}</td>
              <td>{fmtNumber(b.open)}</td>
              <td>{fmtNumber(b.high)}</td>
              <td>{fmtNumber(b.low)}</td>
              <td>{fmtNumber(b.close)}</td>
              <td>{b.volume.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function diffCell(value: number | null) {
  if (value == null) return <td className="historical-diff-na">—</td>;
  if (value === 0) return <td>0</td>;
  const cls = value > 0 ? "profit-text" : "loss-text";
  return (
    <td className={cls}>
      {value > 0 ? "+" : ""}
      {fmtNumber(value, 4)}
    </td>
  );
}

function ComparisonReport({ data }: { data: HistoricalComparisonResponse }) {
  return (
    <div className="historical-comparison-report">
      <dl className="historical-comparison-summary">
        <dt>CSV rows (in range)</dt>
        <dd>{data.csv_row_count}</dd>
        <dt>API rows</dt>
        <dd>{data.api_row_count}</dd>
        <dt>Row count difference (CSV − API)</dt>
        <dd>{data.row_count_diff}</dd>
        <dt>Matched timestamps</dt>
        <dd>{data.matched_count}</dd>
        <dt>CSV-only timestamps</dt>
        <dd>{data.csv_only_count}</dd>
        <dt>API-only timestamps</dt>
        <dd>{data.api_only_count}</dd>
        <dt>Matched rows with any value difference</dt>
        <dd>{data.rows_with_value_diffs}</dd>
        <dt>Max open diff (API − CSV)</dt>
        <dd>{data.max_open_diff != null ? fmtNumber(data.max_open_diff, 4) : "—"}</dd>
        <dt>Max high diff</dt>
        <dd>{data.max_high_diff != null ? fmtNumber(data.max_high_diff, 4) : "—"}</dd>
        <dt>Max low diff</dt>
        <dd>{data.max_low_diff != null ? fmtNumber(data.max_low_diff, 4) : "—"}</dd>
        <dt>Max close diff</dt>
        <dd>{data.max_close_diff != null ? fmtNumber(data.max_close_diff, 4) : "—"}</dd>
        <dt>Max volume diff</dt>
        <dd>{data.max_volume_diff != null ? data.max_volume_diff.toLocaleString() : "—"}</dd>
      </dl>

      {data.csv_row_errors.length > 0 && (
        <div className="historical-comparison-row-errors">
          <div className="historical-bar-table-title">CSV row errors ({data.csv_row_errors.length})</div>
          <ul>
            {data.csv_row_errors.map((e) => (
              <li key={e.row_number}>
                Row {e.row_number}: {e.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.notes.length > 0 && (
        <ul className="historical-comparison-notes">
          {data.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}

      {data.truncated && (
        <p className="historical-comparison-truncated">
          Showing the first {data.rows.length} timestamps -- narrow the date range to see the rest.
        </p>
      )}

      <div className="historical-bar-table-wrap">
        <table className="historical-bar-table historical-comparison-table">
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>In CSV</th>
              <th>In API</th>
              <th>Open diff</th>
              <th>High diff</th>
              <th>Low diff</th>
              <th>Close diff</th>
              <th>Volume diff</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.timestamp}>
                <td>{fmtTimestamp(r.timestamp)}</td>
                <td>{r.in_csv ? "✓" : "—"}</td>
                <td>{r.in_api ? "✓" : "—"}</td>
                {diffCell(r.open_diff)}
                {diffCell(r.high_diff)}
                {diffCell(r.low_diff)}
                {diffCell(r.close_diff)}
                {diffCell(r.volume_diff)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
