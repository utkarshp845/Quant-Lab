import type { BearPutSpreadRequest, BearPutSpreadResponse } from "../types/bearPutSpread";
import type { CsvImportResponse } from "../types/csvImport";
import type { FeatureComputeRequest, FeatureComputeResponse, FeatureRecordsResponse } from "../types/features";
import type {
  HistoricalBar,
  HistoricalBarsResponse,
  HistoricalComparisonResponse,
  HistoricalDataProvider,
  LiveQuote,
  LiveQuoteProvider,
  SaveBarsResponse,
  Timeframe,
} from "../types/marketData";
import type { MonteCarloRequest, MonteCarloResult } from "../types/monteCarlo";
import type { Experiment, ExperimentCreateRequest, ExperimentEventsResponse } from "../types/research";

// Exported so anything that needs the backend's origin without going
// through one of this file's request helpers -- currently just the
// WebSocket stream hook (see hooks/useQuoteStream.ts), which derives
// its ws:// URL from this instead of hardcoding it a second time.
export const API_BASE = "http://localhost:8000/api";

export class ApiError extends Error {
  details: string[];
  constructor(message: string, details: string[] = []) {
    super(message);
    this.details = details;
  }
}

/**
 * Parses a FastAPI/pydantic error body into readable strings.
 *
 * FastAPI returns `detail` in two shapes we need to handle: a list of
 * pydantic field-validation errors (e.g. "long_put -> Ask must be
 * greater than or equal to Bid"), or a single plain string, as raised
 * by our own `HTTPException(422, detail="...")` calls for calculation
 * errors like an undefined z-score.
 */
function formatValidationErrors(body: unknown): string[] {
  if (typeof body !== "object" || body === null || !("detail" in body)) {
    return [];
  }
  const detail = (body as { detail: unknown }).detail;

  if (typeof detail === "string") {
    return [detail];
  }

  if (Array.isArray(detail)) {
    return (detail as Array<{ loc?: unknown[]; msg?: string }>).map((item) => {
      // pydantic v2 prefixes custom validator ValueErrors with "Value
      // error, " -- strip it, it just repeats what the reader can
      // already see (this is an error message).
      const msg = (item.msg ?? "Invalid input").replace(/^Value error,\s*/, "");
      const loc = Array.isArray(item.loc) ? item.loc.filter((p) => p !== "body").join(" -> ") : "";
      return loc ? `${loc}: ${msg}` : msg;
    });
  }

  return [];
}

async function postJson<TResponse>(path: string, body: unknown): Promise<TResponse> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const errorBody = await res.json().catch(() => null);
    const details = formatValidationErrors(errorBody);
    throw new ApiError(
      details.length > 0 ? "Please fix the highlighted inputs." : "The server rejected this request.",
      details,
    );
  }

  return res.json();
}

async function getJson<TResponse>(path: string): Promise<TResponse> {
  const res = await fetch(`${API_BASE}${path}`);

  if (!res.ok) {
    const errorBody = await res.json().catch(() => null);
    const details = formatValidationErrors(errorBody);
    throw new ApiError(details[0] ?? "The server rejected this request.", details);
  }

  return res.json();
}

/**
 * Fetches a live quote from GET /api/market-data/{symbol}/quote (see
 * backend/app/api/market_data.py). This is separate from
 * analyzeBearPutSpread/importCsv on purpose -- it never touches the
 * calculator or the CSV pipeline, it just asks a provider for a quote.
 * The response is LiveQuote -- a flat shape (price/volume/provider
 * promoted to top-level fields) the backend assembles from the
 * provider layer; no API credentials are ever part of this response
 * or any request the frontend makes -- they never leave the backend.
 */
export function getLiveQuote(symbol: string, provider: LiveQuoteProvider): Promise<LiveQuote> {
  return getJson<LiveQuote>(`/market-data/${encodeURIComponent(symbol)}/quote?provider=${provider}`);
}

export function analyzeBearPutSpread(request: BearPutSpreadRequest): Promise<BearPutSpreadResponse> {
  return postJson<BearPutSpreadResponse>("/bear-put-spread", request);
}

export function runMonteCarloSimulation(request: MonteCarloRequest): Promise<MonteCarloResult> {
  return postJson<MonteCarloResult>("/bear-put-spread/monte-carlo", request);
}

/**
 * Fetches historical OHLCV bars from GET /api/market-data/{symbol}/history
 * (see backend/app/api/historical_data.py, v0.1.16). Independent of the
 * live-quote and CSV-import paths above -- the response is
 * HistoricalBarsResponse, a normalized, provider-independent shape the
 * backend assembles from whichever provider was asked; no API
 * credentials are ever part of this request or response.
 */
export function getHistoricalBars(params: {
  symbol: string;
  start: string; // YYYY-MM-DD
  end: string;
  timeframe: Timeframe;
  provider: HistoricalDataProvider;
}): Promise<HistoricalBarsResponse> {
  const qs = new URLSearchParams({
    start: params.start,
    end: params.end,
    timeframe: params.timeframe,
    provider: params.provider,
  });
  return getJson<HistoricalBarsResponse>(`/market-data/${encodeURIComponent(params.symbol)}/history?${qs}`);
}

/**
 * Uploads a CSV of OHLCV bars and diffs it against the same provider
 * request GET .../history makes, via POST /api/market-data/history/compare
 * (see backend/app/api/historical_comparison.py, v0.1.16). This is the
 * "most important test" for the historical-data feature -- see that
 * route's docstring for what the response deliberately does and doesn't
 * assert (numbers only, never a pass/fail verdict).
 */
export async function compareHistoricalCsv(
  file: File,
  params: { symbol: string; start: string; end: string; timeframe: Timeframe; provider: HistoricalDataProvider },
): Promise<HistoricalComparisonResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("symbol", params.symbol);
  formData.append("start", params.start);
  formData.append("end", params.end);
  formData.append("timeframe", params.timeframe);
  formData.append("provider", params.provider);

  const res = await fetch(`${API_BASE}/market-data/history/compare`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    const errorBody = await res.json().catch(() => null);
    const details = formatValidationErrors(errorBody);
    throw new ApiError(details[0] ?? "Could not compare this CSV against the provider.", details);
  }

  return res.json();
}

/**
 * Persists already-fetched bars via POST /api/market-data/history/save
 * (see backend/app/api/historical_storage.py, v0.1.17). Deliberately
 * takes the bars the caller already has (from a prior getHistoricalBars
 * call) rather than symbol/start/end/provider -- saving never re-fetches
 * from a provider itself, and is never triggered automatically by a
 * fetch; it's its own explicit action (the "Save to Database" button).
 */
export function saveHistoricalBars(bars: HistoricalBar[]): Promise<SaveBarsResponse> {
  return postJson<SaveBarsResponse>("/market-data/history/save", { bars });
}

/**
 * Loads previously-saved bars from GET /api/market-data/{symbol}/history/stored
 * (v0.1.17) -- never contacts Alpaca/Massive/any provider. Returns the
 * identical HistoricalBarsResponse shape getHistoricalBars() does, so
 * "fetched live" and "loaded from database" results render with the
 * same UI code.
 */
export function getStoredHistoricalBars(params: {
  symbol: string;
  start: string; // YYYY-MM-DD
  end: string;
  timeframe: Timeframe;
  provider: HistoricalDataProvider;
}): Promise<HistoricalBarsResponse> {
  const qs = new URLSearchParams({
    start: params.start,
    end: params.end,
    timeframe: params.timeframe,
    provider: params.provider,
  });
  return getJson<HistoricalBarsResponse>(`/market-data/${encodeURIComponent(params.symbol)}/history/stored?${qs}`);
}

// ---- Feature Engine v1 (see backend/app/api/features.py) ----
// Reads/writes ONLY the already-normalized historical_bars ->
// historical_features pipeline; no feature math happens in this
// frontend at all -- every value rendered came back from one of these
// two calls verbatim.

/**
 * POST /api/features/compute -- fetches bars (and, for eligible
 * symbols, SPY/QQQ bars) for the given symbol/timeframe/provider/date
 * range, computes the full feature contract, and persists it. Also
 * returns the computed rows directly, so a caller doesn't need a
 * second GET just to see what it produced.
 */
export function computeFeatures(request: FeatureComputeRequest): Promise<FeatureComputeResponse> {
  return postJson<FeatureComputeResponse>("/features/compute", request);
}

/**
 * GET /api/features/{symbol} -- reads back previously computed and
 * persisted FeatureRecords; never triggers a computation itself.
 */
export function getFeatures(params: {
  symbol: string;
  start: string; // YYYY-MM-DD
  end: string;
  timeframe: string;
  provider: string;
}): Promise<FeatureRecordsResponse> {
  const qs = new URLSearchParams({
    start: params.start,
    end: params.end,
    timeframe: params.timeframe,
    provider: params.provider,
  });
  return getJson<FeatureRecordsResponse>(`/features/${encodeURIComponent(params.symbol)}?${qs}`);
}

// ---- Research v1 (see backend/app/api/research.py) ----
// Every statistic rendered anywhere in the Research workspace comes
// from one of these calls -- no aggregation, percentile, threshold-
// probability, or segmentation math is computed in this frontend (the
// backend does not yet expose those; see the Research workspace's own
// gap notices rather than approximating them here).

export function createExperiment(request: ExperimentCreateRequest): Promise<Experiment> {
  return postJson<Experiment>("/research/experiments", request);
}

export function listExperiments(): Promise<Experiment[]> {
  return getJson<Experiment[]>("/research/experiments");
}

export function getExperiment(id: string): Promise<Experiment> {
  return getJson<Experiment>(`/research/experiments/${encodeURIComponent(id)}`);
}

export function getExperimentEvents(id: string): Promise<ExperimentEventsResponse> {
  return getJson<ExperimentEventsResponse>(`/research/experiments/${encodeURIComponent(id)}/events`);
}

/** Runs (or re-runs) an experiment in place, against its own fixed
 * symbol/date-range/timeframe/provider -- see ExperimentForm's
 * "duplicate" flow for how a DIFFERENT date range/dataset is run
 * instead (a new experiment, not a mutation of this one), matching the
 * backend's deliberate "parameters are immutable after creation"
 * reproducibility guarantee. */
export function runExperiment(id: string): Promise<Experiment> {
  return postJson<Experiment>(`/research/experiments/${encodeURIComponent(id)}/run`, undefined);
}

export async function importCsv(file: File): Promise<CsvImportResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${API_BASE}/csv-import`, {
    method: "POST",
    // No Content-Type header -- the browser sets the multipart
    // boundary itself when the body is a FormData instance.
    body: formData,
  });

  if (!res.ok) {
    const errorBody = await res.json().catch(() => null);
    const details = formatValidationErrors(errorBody);
    throw new ApiError(details[0] ?? "Could not import this CSV file.", details);
  }

  return res.json();
}
