import type { BearPutSpreadRequest, BearPutSpreadResponse } from "../types/bearPutSpread";
import type { CsvImportResponse } from "../types/csvImport";
import type { LiveQuote, LiveQuoteProvider } from "../types/marketData";
import type { MonteCarloRequest, MonteCarloResult } from "../types/monteCarlo";

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
