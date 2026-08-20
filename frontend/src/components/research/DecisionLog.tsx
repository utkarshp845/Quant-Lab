import { useEffect, useState } from "react";
import { createDecision, listDecisions } from "../../api/client";
import type { ResearchDecision } from "../../types/researchNotebook";
import { apiErrorMessage } from "../../utils/researchFormat";

/**
 * The full, ordered decision-log history for one design group (spec
 * section 9's worked example: "Selected Candidate C / Reason: largest
 * viable sample... / Outcome data available: NO / Status: LOCKED").
 * Append-only on the backend -- this view only ever adds a new entry,
 * never edits one, matching that.
 */
export function DecisionLog({
  designGroupId,
  candidateOptions,
  refreshKey,
}: {
  designGroupId: string;
  /** id/label pairs a new decision can point at as `resulting_experiment_id`. */
  candidateOptions: { id: string; label: string }[];
  refreshKey?: number;
}) {
  const [decisions, setDecisions] = useState<ResearchDecision[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [decision, setDecision] = useState("");
  const [reason, setReason] = useState("");
  const [criteria, setCriteria] = useState<string[]>([]);
  const [infoAvailable, setInfoAvailable] = useState<string[]>([]);
  const [resultingId, setResultingId] = useState("");
  const [saving, setSaving] = useState(false);

  function reload() {
    listDecisions(designGroupId)
      .then(setDecisions)
      .catch(() => setDecisions([]));
  }

  useEffect(reload, [designGroupId, refreshKey]);

  const CRITERIA_OPTIONS = ["sample_size", "conceptual_validity", "data_availability", "implementation_simplicity", "domain_rationale"];

  function toggle(list: string[], setList: (v: string[]) => void, value: string) {
    setList(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  }

  async function handleSubmit() {
    if (!decision.trim() || !reason.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await createDecision({
        design_group_id: designGroupId,
        decision: decision.trim(),
        reason: reason.trim(),
        selection_criteria: criteria,
        // Deliberately always false here -- this log entry is being
        // written from the Design stage, before any candidate has
        // been run, so outcome data structurally cannot have been
        // available yet (spec section 9's own worked example). A
        // decision made AFTER results exist belongs in Conclude, not
        // here.
        information_available: infoAvailable,
        outcome_data_available: false,
        resulting_experiment_id: resultingId || null,
      });
      setDecision("");
      setReason("");
      setCriteria([]);
      setInfoAvailable([]);
      setResultingId("");
      reload();
    } catch (err) {
      setError(apiErrorMessage(err, "Could not save this decision."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="decision-log">
      {decisions === null && <p className="research-gap-note">Loading decision history…</p>}
      {decisions !== null && decisions.length === 0 && (
        <p className="research-gap-note">No decisions recorded yet for this design group.</p>
      )}
      {decisions !== null && decisions.length > 0 && (
        <ol className="decision-log-list">
          {decisions.map((d) => (
            <li key={d.id} className="decision-log-entry">
              <div className="decision-log-entry-header">
                <strong>{d.decision}</strong>
                <span className="decision-log-timestamp">{new Date(d.created_at).toLocaleString()}</span>
              </div>
              <p className="decision-log-reason">Reason: {d.reason}</p>
              {d.selection_criteria.length > 0 && (
                <p className="decision-log-meta">Selection criteria: {d.selection_criteria.join(", ")}</p>
              )}
              {d.information_available.length > 0 && (
                <p className="decision-log-meta">Information available: {d.information_available.join(", ")}</p>
              )}
              <p className={`decision-log-outcome-flag decision-log-outcome-flag-${d.outcome_data_available ? "yes" : "no"}`}>
                Outcome data available at decision time: {d.outcome_data_available ? "YES" : "NO"}
              </p>
              {d.resulting_experiment_id && (
                <p className="decision-log-meta">
                  Resulting experiment: {candidateOptions.find((c) => c.id === d.resulting_experiment_id)?.label ?? d.resulting_experiment_id}
                </p>
              )}
            </li>
          ))}
        </ol>
      )}

      <h4 className="experiment-form-subheading">Record a decision</h4>
      <label className="field">
        <span className="field-label">Decision</span>
        <input value={decision} onChange={(e) => setDecision(e.target.value)} placeholder="e.g. Selected Candidate C" />
      </label>
      <label className="field">
        <span className="field-label">Reason</span>
        <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why this one, in terms available BEFORE outcome data" />
      </label>
      <fieldset className="decision-log-checkboxes">
        <legend>Selection criteria used</legend>
        {CRITERIA_OPTIONS.map((c) => (
          <label key={c} className="decision-log-checkbox">
            <input type="checkbox" checked={criteria.includes(c)} onChange={() => toggle(criteria, setCriteria, c)} />
            {c.replace(/_/g, " ")}
          </label>
        ))}
      </fieldset>
      <fieldset className="decision-log-checkboxes">
        <legend>Information available at decision time</legend>
        {["sample_size", "conceptual_validity", "data_availability"].map((c) => (
          <label key={c} className="decision-log-checkbox">
            <input type="checkbox" checked={infoAvailable.includes(c)} onChange={() => toggle(infoAvailable, setInfoAvailable, c)} />
            {c.replace(/_/g, " ")}
          </label>
        ))}
      </fieldset>
      {candidateOptions.length > 0 && (
        <label className="field">
          <span className="field-label">Resulting experiment (optional)</span>
          <select value={resultingId} onChange={(e) => setResultingId(e.target.value)}>
            <option value="">— none —</option>
            {candidateOptions.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {error && <div className="error-banner">{error}</div>}
      <button type="button" onClick={handleSubmit} disabled={saving || !decision.trim() || !reason.trim()}>
        {saving ? "Saving…" : "+ Add decision log entry"}
      </button>
    </div>
  );
}
