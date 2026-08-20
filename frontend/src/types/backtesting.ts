// Types mirroring backend/app/models/backtesting.py (Backtesting v1).
//
// Backtesting v1 answers exactly one question: "when this Research
// condition occurred historically, what happened afterward?" -- a
// Backtest always references an existing Experiment by id; it never
// redefines conditions. Next-bar-open entry (never the signal bar's
// own close) + MFE/MAE per configured forward window, in bars (not
// minutes).

export type BacktestStatus = "draft" | "running" | "completed" | "failed";

export const DEFAULT_BACKTEST_WINDOWS = [5, 15, 30, 60];

export interface BacktestWindowOutcome {
  window_bars: number;
  outcome_timestamp: string;
  forward_return: number;
  mfe: number;
  mae: number;
}

export interface BacktestSignal {
  backtest_id: string;
  experiment_id: string;
  symbol: string;
  timeframe: string;
  signal_timestamp: string;
  entry_timestamp: string;
  entry_price: number;
  feature_values: Record<string, number | boolean>;
  outcomes: BacktestWindowOutcome[];
}

export interface BacktestWindowResults {
  window_bars: number;
  signal_count: number;
  win_count: number;
  win_rate: number | null;
  mean_return: number | null;
  median_return: number | null;
  std_dev_return: number | null;
  best_return: number | null;
  worst_return: number | null;
  mean_mfe: number | null;
  mean_mae: number | null;
}

export interface BacktestResults {
  windows: BacktestWindowResults[];
}

export interface BacktestCreateRequest {
  experiment_id: string;
  windows?: number[];
}

export interface Backtest {
  id: string;
  experiment_id: string;
  symbol: string;
  timeframe: string;
  provider: string;
  windows: number[];
  feature_contract_version: string;
  status: BacktestStatus;
  created_at: string;
  completed_at: string | null;
  results: BacktestResults | null;
  error_message: string | null;
}

export interface BacktestSignalsResponse {
  backtest_id: string;
  signal_count: number;
  signals: BacktestSignal[];
}
