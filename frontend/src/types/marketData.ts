// Types mirroring backend/app/models/market_data.py.
// LiveQuote mirrors LiveQuote (the flat HTTP response shape
// GET /market-data/{symbol}/quote actually returns, v0.1.11) --
// not the provider-facing Quote model, which the frontend never sees.

export interface LiveQuote {
  symbol: string;
  price: number | null;
  // Optional as of v0.1.15: a REST-derived quote always has both, but
  // the streaming route's Massive polling fallback (used when the
  // account isn't entitled to Massive's real-time WebSocket) is built
  // from an OHLCV bar, which has no bid/ask at all -- null there,
  // never a fabricated number standing in for one.
  bid: number | null;
  ask: number | null;
  volume: number | null; // best-effort; null if the provider/plan doesn't return it
  timestamp: string; // ISO datetime string
  provider: string;
}

export type LiveQuoteProvider = "alpaca" | "massive" | "schwab";

// Mirrors the JSON frames GET (WebSocket) /market-data/stream sends --
// see backend/app/api/market_data_stream.py's docstring for the exact
// protocol (v0.1.12). Server push only; the frontend never sends
// anything back over this socket.

export type StreamConnectionStatus = "connecting" | "connected" | "disconnected" | "error";

export interface StreamStatusMessage {
  type: "status";
  status: StreamConnectionStatus;
  detail: string | null;
}

export interface StreamQuoteMessage {
  type: "quote";
  quote: LiveQuote;
}

export type StreamMessage = StreamStatusMessage | StreamQuoteMessage;

// Providers the streaming route (backend/app/streaming/hub.py's
// STREAM_FACTORIES) actually supports -- a narrower set than
// LiveQuoteProvider above, since Schwab has no streaming integration
// yet. Massive added v0.1.13, mirroring Alpaca's v0.1.12 shape.
export type StreamProvider = "alpaca" | "massive";

// ---- Historical market data (v0.1.16) ----
// Mirrors backend/app/models/market_data.py's HistoricalBar /
// HistoricalBarsResponse -- the flat HTTP shapes
// GET /market-data/{symbol}/history actually returns, not MarketBar
// (the provider-facing contract, which the frontend never sees).

// Narrower than LiveQuoteProvider on purpose: the first historical-data
// implementation supports Alpaca and Massive only (both have real
// get_historical_data() integrations) -- see
// backend/app/api/historical_data.py's ALLOWED_SYMBOLS/ALLOWED_TIMEFRAMES
// for the matching backend-side restriction.
export type HistoricalDataProvider = "alpaca" | "massive";

// TSLA/NVDA only, for this first implementation -- see
// backend/app/api/historical_data.py's ALLOWED_SYMBOLS docstring for why.
export type HistoricalDataSymbol = "TSLA" | "NVDA";

export type Timeframe = "1m" | "5m" | "15m" | "1h" | "1d";

export interface HistoricalBar {
  symbol: string;
  timestamp: string; // ISO datetime string
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  provider: string;
  timeframe: string; // v0.1.17: travels with the bar itself -- see backend's HistoricalBar docstring
}

export interface HistoricalBarsResponse {
  symbol: string;
  provider: string;
  timeframe: string;
  start: string; // ISO date string
  end: string;
  bar_count: number;
  bars: HistoricalBar[];
}

// ---- Historical-bar storage (v0.1.17) ----
// Mirrors backend/app/models/historical_storage.py's SaveBarsResponse.
// GET .../history/stored returns HistoricalBarsResponse (above) --
// deliberately the identical shape a live provider fetch returns, so
// the UI can render "fetched from provider" and "loaded from database"
// results with the same code.

export interface SaveBarsResponse {
  total: number;
  inserted: number;
  skipped_duplicates: number;
}

// Mirrors backend/app/models/historical_comparison.py exactly --
// see that file's docstring for why there is deliberately no
// pass/fail verdict field anywhere in this shape.
export interface CsvBarRowError {
  row_number: number;
  message: string;
}

export interface HistoricalComparisonRow {
  timestamp: string;
  in_csv: boolean;
  in_api: boolean;
  csv_open: number | null;
  csv_high: number | null;
  csv_low: number | null;
  csv_close: number | null;
  csv_volume: number | null;
  api_open: number | null;
  api_high: number | null;
  api_low: number | null;
  api_close: number | null;
  api_volume: number | null;
  open_diff: number | null;
  high_diff: number | null;
  low_diff: number | null;
  close_diff: number | null;
  volume_diff: number | null;
}

export interface HistoricalComparisonResponse {
  symbol: string;
  provider: string;
  timeframe: string;
  start: string;
  end: string;
  csv_row_count: number;
  api_row_count: number;
  row_count_diff: number;
  matched_count: number;
  csv_only_count: number;
  api_only_count: number;
  rows_with_value_diffs: number;
  max_open_diff: number | null;
  max_high_diff: number | null;
  max_low_diff: number | null;
  max_close_diff: number | null;
  max_volume_diff: number | null;
  csv_total_rows: number;
  csv_row_errors: CsvBarRowError[];
  truncated: boolean;
  rows: HistoricalComparisonRow[];
  notes: string[];
}
