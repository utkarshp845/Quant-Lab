import { useEffect, useState } from "react";
import { createBacktest, getBacktestSignals, listBacktests, runBacktest } from "../../api/client";
import type { Backtest, BacktestSignal } from "../../types/backtesting";
import { apiErrorMessage, fmtNumberOrDash, fmtPercentOrDash } from "../../utils/researchFormat";
import { LineageView } from "./LineageView";

/**
 * BACKTEST stage: signal-level historical outcome measurement
 * (Backtesting v1, backend/app/backtesting/) -- next-bar-open entry,
 * forward return/MFE/MAE per configured horizon. Deliberately NOT
 * position sizing, capital tracking, or simulated P&L -- see the
 * honest placeholder at the bottom of this component for that gap
 * (spec section 19's own "Strategy Definition required" language).
 */
export function BacktestPanel({
  experimentId,
  onChanged,
  onSelectBacktest,
}: {
  experimentId: string;
  onChanged?: () => void;
  onSelectBacktest?: (backtestId: string | null, status: Backtest["status"] | null) => void;
}) {
  const [backtests, setBacktests] = useState<Backtest[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [signals, setSignals] = useState<BacktestSignal[] | null>(null);
  const [drillDownTimestamp, setDrillDownTimestamp] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reload() {
    listBacktests(experimentId)
      .then((list) => {
        setBacktests(list);
        setSelectedId((prev) => prev ?? list[0]?.id ?? null);
      })
      .catch((err) => setError(apiErrorMessage(err, "Could not load backtests.")));
  }

  useEffect(reload, [experimentId]);

  useEffect(() => {
    if (!selectedId) {
      setSignals(null);
      return;
    }
    const backtest = backtests?.find((b) => b.id === selectedId);
    if (backtest?.status !== "completed") {
      setSignals(null);
      return;
    }
    getBacktestSignals(selectedId)
      .then((r) => setSignals(r.signals))
      .catch(() => setSignals(null));
  }, [selectedId, backtests]);

  async function handleCreateAndRun() {
    setCreating(true);
    setError(null);
    try {
      const created = await createBacktest({ experiment_id: experimentId });
      const ran = await runBacktest(created.id);
      setSelectedId(ran.id);
      reload();
      onChanged?.();
    } catch (err) {
      setError(apiErrorMessage(err, "Could not create/run this backtest."));
    } finally {
      setCreating(false);
    }
  }

  const selected = backtests?.find((b) => b.id === selectedId) ?? null;

  useEffect(() => {
    onSelectBacktest?.(selectedId, selected?.status ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, selected?.status]);

  return (
    <div className="backtest-panel">
      <p className="section-subtitle">
        "When this research condition occurred historically, what happened afterward?" -- next-bar-open
        entry (never the signal bar's own close), forward return + MFE/MAE per horizon. This measures
        signal outcomes, not a trading strategy -- see the note below.
      </p>

      {error && <div className="error-banner">{error}</div>}
      {backtests === null && <p className="research-gap-note">Loading backtests…</p>}

      {backtests !== null && backtests.length > 0 && (
        <label className="field">
          <span className="field-label">Backtest run</span>
          <select value={selectedId ?? ""} onChange={(e) => setSelectedId(e.target.value)}>
            {backtests.map((b) => (
              <option key={b.id} value={b.id}>
                {new Date(b.created_at).toLocaleString()} — {b.status} — windows [{b.windows.join(", ")}]
              </option>
            ))}
          </select>
        </label>
      )}

      <button type="button" onClick={handleCreateAndRun} disabled={creating}>
        {creating ? "Running…" : "+ Create and run backtest"}
      </button>

      {selected?.status === "running" && <p className="loading-pill">Running…</p>}
      {selected?.status === "failed" && <div className="error-banner">Backtest failed: {selected.error_message}</div>}

      {selected?.status === "completed" && selected.results && (
        <>
          <div className="table-wrap">
            <table className="payoff-table">
              <thead>
                <tr>
                  <th>Window (bars)</th>
                  <th>Signals</th>
                  <th>Win rate</th>
                  <th>Mean return</th>
                  <th>Median return</th>
                  <th>Mean MFE</th>
                  <th>Mean MAE</th>
                </tr>
              </thead>
              <tbody>
                {selected.results.windows.map((w) => (
                  <tr key={w.window_bars}>
                    <td>{w.window_bars}</td>
                    <td>{w.signal_count}</td>
                    <td>{fmtPercentOrDash(w.win_rate, 1)}</td>
                    <td>{fmtPercentOrDash(w.mean_return, 2)}</td>
                    <td>{fmtPercentOrDash(w.median_return, 2)}</td>
                    <td>{fmtPercentOrDash(w.mean_mfe, 2)}</td>
                    <td>{fmtPercentOrDash(w.mean_mae, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {signals && signals.length > 0 && (
            <>
              <h4 className="experiment-form-subheading">Individual signals</h4>
              <div className="table-wrap">
                <table className="payoff-table">
                  <thead>
                    <tr>
                      <th>Signal time</th>
                      <th>Entry time (next bar open)</th>
                      <th>Entry price</th>
                      <th>5-bar / first-window return</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {signals.slice(0, 50).map((s) => (
                      <tr key={s.signal_timestamp}>
                        <td>
                          <code>{s.signal_timestamp}</code>
                        </td>
                        <td>
                          <code>{s.entry_timestamp}</code>
                        </td>
                        <td>{fmtNumberOrDash(s.entry_price, 2)}</td>
                        <td>{fmtPercentOrDash(s.outcomes[0]?.forward_return ?? null, 2)}</td>
                        <td>
                          <button type="button" onClick={() => setDrillDownTimestamp(s.signal_timestamp)}>
                            Why did this qualify?
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {signals.length > 50 && (
                <p className="research-gap-note">Showing the first 50 of {signals.length} signals.</p>
              )}
            </>
          )}
        </>
      )}

      <div className="research-gap-banner backtest-gap-banner">
        <strong>Strategy Definition required — not yet implemented.</strong> This measures per-signal
        forward return/MFE/MAE only. Converting this into an explicit trading strategy (entry/exit
        rules, position sizing, stop/profit-taking rules, transaction costs, slippage, capital
        assumptions, an equity curve, drawdown, expectancy) needs a strategy-level backtesting engine
        that does not exist in this codebase yet -- shown here honestly rather than fabricated.
      </div>

      {drillDownTimestamp && (
        <LineageView experimentId={experimentId} signalTimestamp={drillDownTimestamp} onClose={() => setDrillDownTimestamp(null)} />
      )}
    </div>
  );
}
