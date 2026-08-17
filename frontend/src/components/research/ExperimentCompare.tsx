import type { Experiment } from "../../types/research";
import { fmtNumberOrDash, fmtPercentOrDash } from "../../utils/researchFormat";

const ROWS: Array<{ label: string; get: (e: Experiment) => string }> = [
  { label: "Symbol", get: (e) => e.symbol },
  { label: "Date range", get: (e) => `${e.start_date} → ${e.end_date}` },
  { label: "Timeframe / Provider", get: (e) => `${e.timeframe} / ${e.provider}` },
  { label: "Condition", get: (e) => `${e.condition.metric} ${e.condition.operator} ${(e.condition.threshold * 100).toFixed(2)}%` },
  {
    label: "Outcome",
    get: (e) => `forward_return (${e.outcome.horizon_minutes}m) ${e.outcome.operator} ${(e.outcome.threshold * 100).toFixed(2)}%`,
  },
  { label: "Sample count", get: (e) => String(e.results?.total_events ?? "—") },
  { label: "Success rate", get: (e) => fmtPercentOrDash(e.results?.success_rate ?? null, 1) },
  { label: "Mean outcome", get: (e) => fmtPercentOrDash(e.results?.average_outcome ?? null, 2) },
  { label: "Median outcome", get: (e) => fmtPercentOrDash(e.results?.median_outcome ?? null, 2) },
  { label: "Std deviation", get: (e) => fmtNumberOrDash(e.results?.std_dev_outcome ?? null, 4) },
];

/** Side-by-side comparison of two already-fetched, already-run
 * experiments -- pure display of two result sets the backend already
 * computed, no new comparison statistic (e.g. a significance test
 * between the two) is calculated here. */
export function ExperimentCompare({ left, right, onBack }: { left: Experiment; right: Experiment; onBack: () => void }) {
  return (
    <section className="section">
      <div className="experiment-list-header">
        <h2 className="section-title">Compare experiments</h2>
        <button type="button" onClick={onBack}>
          Back to list
        </button>
      </div>
      {(left.status !== "completed" || right.status !== "completed") && (
        <div className="error-banner">Both experiments must have completed results to compare meaningfully.</div>
      )}
      <div className="table-wrap">
        <table className="payoff-table">
          <thead>
            <tr>
              <th></th>
              <th>{left.name}</th>
              <th>{right.name}</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map((row) => (
              <tr key={row.label}>
                <td>{row.label}</td>
                <td>{row.get(left)}</td>
                <td>{row.get(right)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
