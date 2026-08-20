import type { BearPutSpreadRequest, BearPutSpreadResponse } from "../types/bearPutSpread";
import type { CsvImportResponse } from "../types/csvImport";
import type { FeatureComputeRequest, FeatureComputeResponse, FeatureDefinition, FeatureRecordsResponse } from "../types/features";
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
import type { Backtest, BacktestCreateRequest, BacktestSignalsResponse } from "../types/backtesting";
import type { OOSPartition, OOSPartitionCreateRequest } from "../types/oosPartitions";
import type { ExperimentFreezeSnapshot, ExperimentProvenance, OOSPartitionLinkRequest } from "../types/experimentFreeze";
import type { OOSEvaluationResult, OOSSignal } from "../types/oosEvaluation";
import type { OOSEvidenceSummary, OOSPeriod, OOSPeriodLinkRequest } from "../types/oosEvidence";
import type { StatisticalValidationReport } from "../types/statisticalValidation";
import type { StatisticalValidationReportV2 } from "../types/statisticalValidationV2";
import type { OOSStatisticalReview } from "../types/oosStatisticalReview";
import type {
  Conclusion,
  ConclusionCreateRequest,
  ConditionPreviewRequest,
  ConditionPreviewResponse,
  ExperimentVersionsResponse,
  Observation,
  ObservationCreateRequest,
  ResearchDecision,
  ResearchDecisionCreateRequest,
} from "../types/researchNotebook";
import type { PipelineStatusResponse } from "../types/pipelineStatus";
import type { EventLineage } from "../types/researchLineage";

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
 * GET /api/features/vocabulary -- the canonical feature vocabulary
 * (v0.1.24, app/features/vocabulary.py). Research's condition builder
 * (src/components/research/ConditionBuilder.tsx) populates its feature
 * dropdown from this call rather than a hardcoded list -- requirement
 * 2 of the Feature <-> Research integration. A static, in-process
 * backend response (no database read) -- safe to fetch once per form
 * mount.
 */
export function getFeatureVocabulary(): Promise<FeatureDefinition[]> {
  return getJson<FeatureDefinition[]>("/features/vocabulary");
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

// ---- Backtesting v1 (see backend/app/api/backtesting.py) ----
// A Backtest always references an existing Experiment by id -- never a
// second way to define conditions. Signal-level outcome measurement
// (next-bar-open entry, forward return/MFE/MAE per horizon) -- NOT
// position sizing, capital tracking, or simulated P&L (see the
// Backtest stage UI's own "Strategy Definition required" placeholder
// for that gap).

export function createBacktest(request: BacktestCreateRequest): Promise<Backtest> {
  return postJson<Backtest>("/backtests", request);
}

export function listBacktests(experimentId?: string): Promise<Backtest[]> {
  const qs = experimentId ? `?experiment_id=${encodeURIComponent(experimentId)}` : "";
  return getJson<Backtest[]>(`/backtests${qs}`);
}

export function getBacktest(id: string): Promise<Backtest> {
  return getJson<Backtest>(`/backtests/${encodeURIComponent(id)}`);
}

export function getBacktestSignals(id: string): Promise<BacktestSignalsResponse> {
  return getJson<BacktestSignalsResponse>(`/backtests/${encodeURIComponent(id)}/signals`);
}

export function runBacktest(id: string): Promise<Backtest> {
  return postJson<Backtest>(`/backtests/${encodeURIComponent(id)}/run`, undefined);
}

// ---- Statistical Validation V1/V2 (see backend/app/api/statistical_validation.py) ----
// Derived, on-demand reports -- never persisted, always recomputed
// from a Backtest's own already-persisted signals. V2 is the
// dependence-aware successor; prefer it, V1 is shown for comparison.

export function getStatisticalValidation(
  backtestId: string,
  params?: { primary_window_bars?: number },
): Promise<StatisticalValidationReport> {
  const qs = params?.primary_window_bars != null ? `?primary_window_bars=${params.primary_window_bars}` : "";
  return getJson<StatisticalValidationReport>(`/backtests/${encodeURIComponent(backtestId)}/statistical-validation${qs}`);
}

export function getStatisticalValidationV2(
  backtestId: string,
  params?: { primary_window_bars?: number },
): Promise<StatisticalValidationReportV2> {
  const qs = params?.primary_window_bars != null ? `?primary_window_bars=${params.primary_window_bars}` : "";
  return getJson<StatisticalValidationReportV2>(
    `/backtests/${encodeURIComponent(backtestId)}/statistical-validation-v2${qs}`,
  );
}

// ---- OOS / Holdout Partition Framework v1 (see backend/app/api/oos_partitions.py) ----

export function createOosPartition(request: OOSPartitionCreateRequest): Promise<OOSPartition> {
  return postJson<OOSPartition>("/oos/partitions", request);
}

export function listOosPartitions(params?: { symbol?: string; timeframe?: string; provider?: string }): Promise<OOSPartition[]> {
  const qs = new URLSearchParams();
  if (params?.symbol) qs.set("symbol", params.symbol);
  if (params?.timeframe) qs.set("timeframe", params.timeframe);
  if (params?.provider) qs.set("provider", params.provider);
  const suffix = qs.toString() ? `?${qs}` : "";
  return getJson<OOSPartition[]>(`/oos/partitions${suffix}`);
}

export function getOosPartition(id: string): Promise<OOSPartition> {
  return getJson<OOSPartition>(`/oos/partitions/${encodeURIComponent(id)}`);
}

// ---- Experiment Freeze & Provenance v1 (see backend/app/api/experiment_freeze.py) ----

export function associateOosPartition(experimentId: string, request: OOSPartitionLinkRequest): Promise<Experiment> {
  return postJson<Experiment>(`/research/experiments/${encodeURIComponent(experimentId)}/oos-partition`, request);
}

export function freezeExperiment(experimentId: string): Promise<Experiment> {
  return postJson<Experiment>(`/research/experiments/${encodeURIComponent(experimentId)}/freeze`, undefined);
}

export function getFrozenSnapshot(experimentId: string): Promise<ExperimentFreezeSnapshot> {
  return getJson<ExperimentFreezeSnapshot>(`/research/experiments/${encodeURIComponent(experimentId)}/frozen`);
}

export function getExperimentProvenance(experimentId: string): Promise<ExperimentProvenance> {
  return getJson<ExperimentProvenance>(`/research/experiments/${encodeURIComponent(experimentId)}/provenance`);
}

export function archiveExperiment(experimentId: string): Promise<Experiment> {
  return postJson<Experiment>(`/research/experiments/${encodeURIComponent(experimentId)}/archive`, undefined);
}

// ---- OOS Evaluation v1 (see backend/app/api/oos_evaluation.py) ----
// Request body is always ignored server-side -- every research-defining
// fact comes from the frozen snapshot + linked partition, never the caller.

export function runOosEvaluation(experimentId: string): Promise<OOSEvaluationResult> {
  return postJson<OOSEvaluationResult>(`/research/experiments/${encodeURIComponent(experimentId)}/oos-evaluate`, undefined);
}

export function listOosEvaluations(experimentId: string): Promise<OOSEvaluationResult[]> {
  return getJson<OOSEvaluationResult[]>(`/research/experiments/${encodeURIComponent(experimentId)}/oos-evaluations`);
}

export function getOosEvaluation(evaluationId: string): Promise<OOSEvaluationResult> {
  return getJson<OOSEvaluationResult>(`/research/oos-evaluations/${encodeURIComponent(evaluationId)}`);
}

export function getOosEvaluationSignals(evaluationId: string): Promise<OOSSignal[]> {
  return getJson<OOSSignal[]>(`/research/oos-evaluations/${encodeURIComponent(evaluationId)}/signals`);
}

// ---- OOS Evidence Accumulation V1 (see backend/app/api/oos_evidence.py) ----

export function registerOosPeriod(experimentId: string, request: OOSPeriodLinkRequest): Promise<OOSPeriod> {
  return postJson<OOSPeriod>(`/research/experiments/${encodeURIComponent(experimentId)}/oos-periods`, request);
}

export function listOosPeriods(experimentId: string): Promise<OOSPeriod[]> {
  return getJson<OOSPeriod[]>(`/research/experiments/${encodeURIComponent(experimentId)}/oos-periods`);
}

export function evaluateOosPeriod(experimentId: string, oosPartitionId: string): Promise<OOSEvaluationResult> {
  return postJson<OOSEvaluationResult>(
    `/research/experiments/${encodeURIComponent(experimentId)}/oos-periods/${encodeURIComponent(oosPartitionId)}/evaluate`,
    undefined,
  );
}

export function getOosEvidence(experimentId: string): Promise<OOSEvidenceSummary> {
  return getJson<OOSEvidenceSummary>(`/research/experiments/${encodeURIComponent(experimentId)}/oos-evidence`);
}

// ---- OOS Statistical Review V1 (see backend/app/api/oos_statistical_review.py) ----
// No request body -- every config value, including the resampling
// seed, is fixed and immutable.

export function runOosStatisticalReview(experimentId: string): Promise<OOSStatisticalReview> {
  return postJson<OOSStatisticalReview>(
    `/research/experiments/${encodeURIComponent(experimentId)}/oos-statistical-review`,
    undefined,
  );
}

export function listOosStatisticalReviews(experimentId: string): Promise<OOSStatisticalReview[]> {
  return getJson<OOSStatisticalReview[]>(`/research/experiments/${encodeURIComponent(experimentId)}/oos-statistical-reviews`);
}

export function getOosStatisticalReview(reviewId: string): Promise<OOSStatisticalReview> {
  return getJson<OOSStatisticalReview>(`/research/oos-statistical-reviews/${encodeURIComponent(reviewId)}`);
}

// ---- Research Notebook v1 (see backend/app/api/research_notebook.py) ----
// Observation ("what happened"), Decision (candidate-selection
// provenance log), Conclusion (a verdict that must reference its own
// evidence), and the experiment version tree. None of these duplicate
// Research v1 -- they're provenance/methodology metadata alongside it.

export function createObservation(request: ObservationCreateRequest): Promise<Observation> {
  return postJson<Observation>("/research/observations", request);
}

export function listObservations(symbol?: string): Promise<Observation[]> {
  const qs = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
  return getJson<Observation[]>(`/research/observations${qs}`);
}

export function getObservation(id: string): Promise<Observation> {
  return getJson<Observation>(`/research/observations/${encodeURIComponent(id)}`);
}

export function createDecision(request: ResearchDecisionCreateRequest): Promise<ResearchDecision> {
  return postJson<ResearchDecision>("/research/decisions", request);
}

export function listDecisions(designGroupId: string): Promise<ResearchDecision[]> {
  return getJson<ResearchDecision[]>(`/research/design-groups/${encodeURIComponent(designGroupId)}/decisions`);
}

export function createConclusion(experimentId: string, request: ConclusionCreateRequest): Promise<Conclusion> {
  return postJson<Conclusion>(`/research/experiments/${encodeURIComponent(experimentId)}/conclusions`, request);
}

export function listConclusions(experimentId: string): Promise<Conclusion[]> {
  return getJson<Conclusion[]>(`/research/experiments/${encodeURIComponent(experimentId)}/conclusions`);
}

export function getExperimentVersions(experimentId: string): Promise<ExperimentVersionsResponse> {
  return getJson<ExperimentVersionsResponse>(`/research/experiments/${encodeURIComponent(experimentId)}/versions`);
}

export function previewConditions(request: ConditionPreviewRequest): Promise<ConditionPreviewResponse> {
  return postJson<ConditionPreviewResponse>("/research/conditions/preview", request);
}

// ---- Pipeline status (see backend/app/api/research_pipeline.py) ----
// The single source of truth for "what stage is this experiment at,
// right now" -- drives components/research/ResearchPipeline.tsx.

export function getPipelineStatus(experimentId: string): Promise<PipelineStatusResponse> {
  return getJson<PipelineStatusResponse>(`/research/experiments/${encodeURIComponent(experimentId)}/pipeline-status`);
}

// ---- Data lineage (see backend/app/api/research_lineage.py) ----
// "Why did this event qualify?" -- a read-only bundle of the signal
// bar, feature record, condition evaluations, and outcome bar for one
// already-detected event.

export function getEventLineage(experimentId: string, signalTimestamp: string): Promise<EventLineage> {
  const qs = `?signal_timestamp=${encodeURIComponent(signalTimestamp)}`;
  return getJson<EventLineage>(`/research/experiments/${encodeURIComponent(experimentId)}/lineage${qs}`);
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
