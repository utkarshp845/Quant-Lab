import { useEffect, useState } from "react";
import { ApiError, getFeatureVocabulary } from "../../api/client";
import type { FeatureDefinition } from "../../types/features";
import type { FeatureCondition, FeatureConditionOperator } from "../../types/research";

/**
 * The condition builder (v0.1.24, Feature <-> Research integration) --
 * requirement 2: the feature dropdown is populated ENTIRELY from
 * GET /api/features/vocabulary (app/features/vocabulary.py), never a
 * hardcoded list in this file. Requirement 4: the operator dropdown for
 * a given row only ever shows operators THAT FEATURE's own
 * `supported_operators` lists (a numeric feature offers the full
 * `< <= = >= > between`; a boolean one -- none exist in this app's real
 * vocabulary yet, see that module's own docstring -- would offer only
 * `=`). Requirement 3: what this builder produces is exactly
 * `{feature_id, operator, value(, value_max)}`, matching
 * ExperimentCreateRequest.conditions verbatim.
 *
 * Multiple rows, AND-combined -- the "+ Add condition" control this
 * used to show permanently disabled (see the old single-Condition
 * version of this file) is now real, restricted to AND per this
 * integration's own spec (no OR/nesting).
 *
 * No unit-aware value formatting: unlike the old builder (which always
 * treated its one threshold as a percent, *100 for display / /100 for
 * the request), a feature_id here can be a return (a fraction), a raw
 * volume count, an ATR (price units), or a 0..1 percentile -- there is
 * no single display convention that fits all of them. `value`/
 * `value_max` are plain number inputs; the selected feature's own
 * description (from the vocabulary) is shown alongside each row so the
 * expected scale is visible without guessing.
 */
export function ConditionBuilder({
  conditions,
  onChange,
}: {
  conditions: FeatureCondition[];
  onChange: (conditions: FeatureCondition[]) => void;
}) {
  const [vocabulary, setVocabulary] = useState<FeatureDefinition[] | null>(null);
  const [vocabularyError, setVocabularyError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getFeatureVocabulary()
      .then((result) => {
        if (!cancelled) setVocabulary(result);
      })
      .catch((err) => {
        if (cancelled) return;
        setVocabularyError(
          err instanceof ApiError ? err.message : "Could not load the feature vocabulary. Is the backend running?",
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function updateRow(index: number, next: FeatureCondition) {
    onChange(conditions.map((c, i) => (i === index ? next : c)));
  }

  function removeRow(index: number) {
    onChange(conditions.filter((_, i) => i !== index));
  }

  function addRow() {
    if (!vocabulary || vocabulary.length === 0) return;
    const first = vocabulary[0];
    onChange([...conditions, defaultCondition(first)]);
  }

  if (vocabularyError) {
    return <div className="error-banner">{vocabularyError}</div>;
  }

  return (
    <div className="condition-builder">
      {vocabulary === null && <p className="condition-row-label">Loading feature vocabulary…</p>}
      {vocabulary !== null &&
        conditions.map((condition, index) => (
          <div key={index}>
            {index > 0 && <div className="condition-and-divider">AND</div>}
            <ConditionRow
              vocabulary={vocabulary}
              value={condition}
              onChange={(next) => updateRow(index, next)}
              onRemove={conditions.length > 1 ? () => removeRow(index) : undefined}
            />
          </div>
        ))}
      <button type="button" className="condition-add-btn" onClick={addRow} disabled={!vocabulary || vocabulary.length === 0}>
        + Add condition (AND)
      </button>
    </div>
  );
}

/** A brand-new row's starting shape: the given feature, its first
 * supported operator, and a zero-ish value -- `false` for a boolean
 * feature (a real, valid value, never a placeholder), `0`/`0`/`1` for
 * "between"'s two bounds otherwise. */
function defaultCondition(definition: FeatureDefinition): FeatureCondition {
  const operator = definition.supported_operators[0] as FeatureConditionOperator;
  const value = definition.value_type === "boolean" ? false : 0;
  return operator === "between" ? { feature_id: definition.feature_id, operator, value: 0, value_max: 1 } : { feature_id: definition.feature_id, operator, value };
}

function ConditionRow({
  vocabulary,
  value,
  onChange,
  onRemove,
}: {
  vocabulary: FeatureDefinition[];
  value: FeatureCondition;
  onChange: (condition: FeatureCondition) => void;
  onRemove?: () => void;
}) {
  const definition = vocabulary.find((f) => f.feature_id === value.feature_id) ?? vocabulary[0];

  function handleFeatureChange(feature_id: string) {
    const next = vocabulary.find((f) => f.feature_id === feature_id);
    if (next) onChange(defaultCondition(next));
  }

  function handleOperatorChange(operator: FeatureConditionOperator) {
    if (operator === "between") {
      onChange({ ...value, operator, value: typeof value.value === "number" ? value.value : 0, value_max: 1 });
    } else {
      onChange({ feature_id: value.feature_id, operator, value: value.value });
    }
  }

  return (
    <div className="condition-row-group">
      <div className="condition-row">
        <select value={definition.feature_id} onChange={(e) => handleFeatureChange(e.target.value)}>
          {vocabulary.map((f) => (
            <option key={f.feature_id} value={f.feature_id}>
              {f.name}
            </option>
          ))}
        </select>
        <select value={value.operator} onChange={(e) => handleOperatorChange(e.target.value as FeatureConditionOperator)}>
          {definition.supported_operators.map((op) => (
            <option key={op} value={op}>
              {op}
            </option>
          ))}
        </select>

        {definition.value_type === "boolean" ? (
          <select
            value={String(value.value)}
            onChange={(e) => onChange({ ...value, value: e.target.value === "true" })}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        ) : (
          <input
            className="condition-threshold-input"
            type="number"
            step="any"
            value={typeof value.value === "number" ? value.value : 0}
            onChange={(e) => onChange({ ...value, value: Number(e.target.value) })}
          />
        )}

        {value.operator === "between" && (
          <>
            <span className="condition-row-label">and</span>
            <input
              className="condition-threshold-input"
              type="number"
              step="any"
              value={value.value_max ?? 0}
              onChange={(e) => onChange({ ...value, value_max: Number(e.target.value) })}
            />
          </>
        )}

        {onRemove && (
          <button type="button" className="condition-remove-btn" onClick={onRemove} title="Remove this condition">
            ✕
          </button>
        )}
      </div>
      <p className="condition-metric-preview">
        <code>{definition.feature_id}</code> — {definition.description}
      </p>
    </div>
  );
}
