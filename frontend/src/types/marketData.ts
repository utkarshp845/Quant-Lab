// Types mirroring backend/app/models/market_data.py.
// LiveQuote mirrors LiveQuote (the flat HTTP response shape
// GET /market-data/{symbol}/quote actually returns, v0.1.11) --
// not the provider-facing Quote model, which the frontend never sees.

export interface LiveQuote {
  symbol: string;
  price: number | null;
  bid: number;
  ask: number;
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
