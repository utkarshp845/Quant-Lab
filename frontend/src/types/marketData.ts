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
