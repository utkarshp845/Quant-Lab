import { CONDITION_OPERATORS, type Condition, type ConditionOperator } from "../../types/research";

/**
 * The condition builder -- deliberately a SINGLE row, not the free-form
 * "pick any feature, AND/OR groups" builder the product vision
 * describes. The backend's `Condition.metric` only accepts the shape
 * "{N}m_return" (a trailing N-minute return, validated server-side by
 * regex) and an `Experiment` holds exactly one `Condition` -- there is
 * no boolean composition or arbitrary-feature support to build a
 * richer UI against yet. The "+ Add condition" control is shown,
 * disabled, so the intended shape of this workflow stays visible
 * rather than silently absent -- see the tooltip for why it's off.
 */
export function ConditionBuilder({
  value,
  minutes,
  onChangeMinutes,
  onChange,
}: {
  value: Condition;
  minutes: string;
  onChangeMinutes: (minutes: string) => void;
  onChange: (condition: Condition) => void;
}) {
  return (
    <div className="condition-builder">
      <div className="condition-row">
        <span className="condition-row-label">Trailing return over</span>
        <input
          className="condition-minutes-input"
          type="number"
          min={1}
          step={1}
          value={minutes}
          onChange={(e) => onChangeMinutes(e.target.value)}
        />
        <span className="condition-row-label">minutes is</span>
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
        Backend metric: <code>{value.metric}</code>
      </p>
      <button type="button" className="condition-add-btn" disabled title="Requires a backend extension -- v1 supports exactly one condition per experiment.">
        + Add condition (AND / OR)
      </button>
    </div>
  );
}
