import { useState } from "react";
import { ApiError, runMonteCarloSimulation } from "../api/client";
import type { BearPutSpreadRequest } from "../types/bearPutSpread";
import type { MonteCarloResult } from "../types/monteCarlo";
import { FormulaBox } from "./FormulaBox";
import { MonteCarloHistogramChart } from "./MonteCarloHistogramChart";
import { Tooltip } from "./Tooltip";
import { fmtPercent, fmtSigned, fmtUsd } from "../utils/format";

interface MonteCarloSectionProps {
  request: BearPutSpreadRequest | null;
  breakeven: number;
  underlyingPrice: number;
  debitPerContract: number;
}

const SIMULATION_COUNTS = [1_000, 10_000, 100_000] as const;

export function MonteCarloSection({
  request,
  breakeven,
  underlyingPrice,
  debitPerContract,
}: MonteCarloSectionProps) {
  const [numSimulations, setNumSimulations] = useState<number>(100_000);
  const [result, setResult] = useState<MonteCarloResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runSimulation = async () => {
    if (!request) return;
    setLoading(true);
    setError(null);
    try {
      const data = await runMonteCarloSimulation({ ...request, num_simulations: numSimulations });
      setResult(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the backend for the simulation.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="section">
      <h2 className="section-title">11. Monte Carlo Simulation</h2>
      <p className="section-subtitle">
        Phase 2 above computed an exact answer by integrating the normal distribution in closed
        form. This instead draws {numSimulations.toLocaleString()} random expiration prices from
        that same model, runs each one through the same payoff formulas, and summarizes what
        actually happened across all of them. As the simulation count grows, its numbers should
        converge toward Phase 2's closed-form numbers -- a built-in correctness check.
      </p>

      <div className="mc-controls">
        <div className="mc-sim-count-group" role="group" aria-label="Number of simulations">
          {SIMULATION_COUNTS.map((n) => (
            <button
              key={n}
              type="button"
              className={`mc-count-btn ${numSimulations === n ? "mc-count-btn-active" : ""}`}
              onClick={() => setNumSimulations(n)}
            >
              {n.toLocaleString()}
            </button>
          ))}
        </div>
        <button type="button" className="mc-run-btn" onClick={runSimulation} disabled={!request || loading}>
          {loading ? "Simulating…" : "Run Simulation"}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {result && (
        <div className="mc-results">
          <p className="mc-headline">
            Based on the assumptions of this model,{" "}
            <strong>{fmtPercent(result.probability_of_profit, 1)}</strong> of{" "}
            {result.num_simulations.toLocaleString()} simulated outcomes were profitable.
          </p>
          <p className="disclaimer-note">
            This is a model output describing simulated outcomes under a simplified assumption --
            not a measured fact about the real market.
          </p>

          <div className="mc-stat-grid">
            <div className="mc-stat-card">
              <div className="mc-stat-label">
                Probability of Profit <Tooltip text="Share of simulated outcomes with positive P/L." />
              </div>
              <div className="mc-stat-value">{fmtPercent(result.probability_of_profit, 1)}</div>
            </div>
            <div className="mc-stat-card">
              <div className="mc-stat-label">
                Probability of Max Loss{" "}
                <Tooltip text="Share of simulated outcomes where the underlying finished at or above the long strike (the flat max-loss region)." />
              </div>
              <div className="mc-stat-value loss-text">{fmtPercent(result.probability_of_max_loss, 1)}</div>
            </div>
            <div className="mc-stat-card">
              <div className="mc-stat-label">
                Probability of Max Profit{" "}
                <Tooltip text="Share of simulated outcomes where the underlying finished at or below the short strike (the flat max-profit region)." />
              </div>
              <div className="mc-stat-value profit-text">{fmtPercent(result.probability_of_max_profit, 1)}</div>
            </div>
          </div>

          <div className="metric-block">
            <div className="metric-heading">
              Expected Value: Simulated vs. Closed-Form{" "}
              <Tooltip text="Both use the same underlying model -- the simulated value is a random estimate that should land close to the exact closed-form value from Phase 2." />
            </div>
            <div className="mc-ev-compare">
              <div>
                <span className="mc-ev-label">Monte Carlo (simulated)</span>
                <span className={`mc-ev-value ${result.expected_value_per_contract >= 0 ? "profit-text" : "loss-text"}`}>
                  {fmtSigned(result.expected_value_per_contract)}
                </span>
              </div>
              <div>
                <span className="mc-ev-label">Phase 2 (closed-form)</span>
                <span
                  className={`mc-ev-value ${result.closed_form_expected_value_per_contract >= 0 ? "profit-text" : "loss-text"}`}
                >
                  {fmtSigned(result.closed_form_expected_value_per_contract)}
                </span>
              </div>
            </div>
            <FormulaBox
              formula={result.formula_expected_return}
              substitution={`${fmtSigned(result.expected_value_per_contract)} / ${fmtUsd(debitPerContract)}`}
              result={fmtPercent(result.expected_return_pct, 1)}
            />
          </div>

          <div className="metric-block">
            <div className="metric-heading">
              Outcome Percentiles (P/L per contract){" "}
              <Tooltip text="The value at/below which a given percentage of simulated outcomes fell. With a capped payoff like this spread, percentiles often land exactly on the max-loss or max-profit plateau." />
            </div>
            <div className="table-wrap">
              <table className="payoff-table">
                <thead>
                  <tr>
                    <th>5th</th>
                    <th>25th</th>
                    <th>Median</th>
                    <th>75th</th>
                    <th>95th</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>{fmtSigned(result.percentile_5_pl_per_contract)}</td>
                    <td>{fmtSigned(result.percentile_25_pl_per_contract)}</td>
                    <td>{fmtSigned(result.median_pl_per_contract)}</td>
                    <td>{fmtSigned(result.percentile_75_pl_per_contract)}</td>
                    <td>{fmtSigned(result.percentile_95_pl_per_contract)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div className="mc-gain-loss-row">
              <span>
                Expected Gain (winners only): <strong className="profit-text">{fmtSigned(result.expected_gain_per_contract)}</strong>
              </span>
              <span>
                Expected Loss (losers only): <strong className="loss-text">{fmtSigned(result.expected_loss_per_contract)}</strong>
              </span>
            </div>
          </div>

          <div className="metric-block">
            <div className="metric-heading">Simulated Distribution</div>
            <MonteCarloHistogramChart
              histogram={result.histogram}
              breakeven={breakeven}
              underlyingPrice={underlyingPrice}
            />
          </div>

          <details className="distribution-table-details">
            <summary>Show first {result.sample_paths.length} simulated paths</summary>
            <div className="table-wrap">
              <table className="payoff-table">
                <thead>
                  <tr>
                    <th>Simulation #</th>
                    <th>Simulated Price</th>
                    <th>P/L per Contract</th>
                  </tr>
                </thead>
                <tbody>
                  {result.sample_paths.map((p) => (
                    <tr key={p.index} className={p.pl_per_contract > 0 ? "row-profit" : "row-loss"}>
                      <td>#{p.index}</td>
                      <td>{fmtUsd(p.simulated_price)}</td>
                      <td>{fmtSigned(p.pl_per_contract)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>

          <div className="disclaimer-banner">
            <strong>These are simulated model outputs, not facts.</strong> Every number in this
            section comes from random draws under the same simplified normal-distribution
            assumption used throughout this app (no volatility skew, no drift, no fat tails). A
            different run with a different random seed will give slightly different numbers; a
            larger simulation count will converge more tightly toward the Phase 2 closed-form
            values shown above.
          </div>
        </div>
      )}
    </section>
  );
}
