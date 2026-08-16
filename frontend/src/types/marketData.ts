// Types mirroring backend/app/models/market_data.py.
// Only Quote/MarketTimestamp so far -- MarketBar has no frontend
// consumer yet (no historical-bars route exists; see
// GET /api/market-data/{symbol}/quote in api/market_data.py).

export interface MarketTimestamp {
  value: string; // ISO datetime string
  source: string; // provider name, e.g. "alpaca"
}

export interface Quote {
  symbol: string;
  bid: number;
  ask: number;
  last: number | null;
  timestamp: MarketTimestamp;
}

export type LiveQuoteProvider = "alpaca" | "massive" | "schwab";
