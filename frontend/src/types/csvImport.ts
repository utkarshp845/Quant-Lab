// Types mirroring backend/app/models/option_chain.py.

export interface NormalizedOption {
  symbol: string;
  underlying_price: number;
  expiration: string; // ISO date string
  dte: number;
  strike: number;
  option_type: "call" | "put";
  bid: number;
  ask: number;
  last: number | null;
  volume: number | null;
  open_interest: number | null;
  implied_volatility: number; // decimal, e.g. 0.44 for 44%
  delta: number;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
}

export interface CsvRowError {
  row_number: number;
  message: string;
}

export interface CsvImportResponse {
  detected_columns: string[];
  column_mapping: Record<string, string>;
  contracts: NormalizedOption[];
  symbols: string[];
  expirations_by_symbol: Record<string, string[]>;
  row_errors: CsvRowError[];
  total_rows: number;
  imported_rows: number;
}
