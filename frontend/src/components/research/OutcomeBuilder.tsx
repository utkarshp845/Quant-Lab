import { CONDITION_OPERATORS, type ConditionOperator, type Outcome } from "../../types/research";

// The four horizons this feature's spec names explicitly ("OUTCOME V1:
// Support forward return at 5m/15m/30m/60m"). The backend itself
// accepts any positive `horizon_minutes` that evenly divides the
// experiment's timeframe (app/research/metrics.py::bars_for_window) --
// this UI intentionally offers only these four rather than a free
// numeric field, to stay exactly within what was asked for.
const OUTCOME_HORIZONS_MINUTES = [5, 15, 30, 60];

/** `Outcome.metric` is always "forward_return" -- the backend accepts
 * no other value, so it's shown read-only rather than as a dropdown
 * with one option. */
export function OutcomeBuilder({ value, onChange }: { value: Outcome; onChange: (outcome: Outcome) => void }) {
  return (
    <div className="condition-builder">
      <div className="condition-row">
        <span className="condition-row-label">Forward return over</span>
        <select
          value={value.horizon_minutes}
          onChange={(e) => onChange({ ...value, horizon_minutes: Number(e.target.value) })}
        >
          {OUTCOME_HORIZONS_MINUTES.map((m) => (
            <option key={m} value={m}>
              {m}m
            </option>
          ))}
        </select>
        <span className="condition-row-label">is</span>
        <select
          value={value.operator}
          onChange={(e) => onChange({ ...value, operator: e.target.value as ConditionOperator })}
        >
          {CONDITION_OPERATORS.map((op) => (
            <option key={op} value={op}>
              {op}
            </option>
          ))}
        </select>
        <input
          className="condition-threshold-input"
          type="number"
          step={0.01}
          value={value.threshold * 100}
          onChange={(e) => onChange({ ...value, threshold: Number(e.target.value) / 100 })}
        />
        <span className="condition-row-label">%</span>
      </div>
      <p className="condition-metric-preview">
        Backend metric: <code>forward_return</code>
      </p>
    </div>
  );
}
