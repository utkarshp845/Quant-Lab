import { useEffect, useState } from "react";
import { createConclusion, listConclusions } from "../../api/client";
import type { Conclusion, ConclusionState } from "../../types/researchNotebook";
import { apiErrorMessage } from "../../utils/researchFormat";

const STATES: { value: ConclusionState; label: string }[] = [
  { value: "supported", label: "Supported" },
  { value: "weakened", label: "Weakened" },
  { value: "inconclusive", label: "Inconclusive" },
  { value: "rejected", label: "Rejected" },
  { value: "needs_more_data", label: "Needs more data" },
];

/**
 * CONCLUDE stage (spec section 18): an explicit research verdict --
 * never automatically "profitable research = successful strategy".
 * Every reference field is REQUIRED (backend/app/models/
 * research_notebook.py::ConclusionCreateRequest rejects a blank one) --
 * this form cannot be submitted without stating what the conclusion is
 * based on.
 */
export function ConclusionForm({ experimentId }: { experimentId: string }) {
  const [conclusions, setConclusions] = useState<Conclusion[] | null>(null);
  const [state, setState] = useState<ConclusionState>("inconclusive");
  const [statement, setStatement] = useState("");
  const [refHypothesis, setRefHypothesis] = useState("");
  const [refSample, setRefSample] = useState("");
  const [refBaseline, setRefBaseline] = useState("");
  const [refOutcomes, setRefOutcomes] = useState("");
  const [refValidation, setRefValidation] = useState("");
  const [limitations, setLimitations] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reload() {
    listConclusions(experimentId)
      .then(setConclusions)
      .catch(() => setConclusions([]));
  }
  useEffect(reload, [experimentId]);

  const allFilled = [statement, refHypothesis, refSample, refBaseline, refOutcomes, refValidation, limitations].every(
    (v) => v.trim() !== "",
  );

  async function handleSubmit() {
    if (!allFilled) return;
    setSaving(true);
    setError(null);
    try {
      await createConclusion(experimentId, {
        state,
        statement: statement.trim(),
        references_hypothesis: refHypothesis.trim(),
        references_sample: refSample.trim(),
        references_baseline: refBaseline.trim(),
        references_outcomes: refOutcomes.trim(),
        references_statistical_validation: refValidation.trim(),
        limitations: limitations.trim(),
      });
      setStatement("");
      setRefHypothesis("");
      setRefSample("");
      setRefBaseline("");
      setRefOutcomes("");
      setRefValidation("");
      setLimitations("");
      reload();
    } catch (err) {
      setError(apiErrorMessage(err, "Could not save this conclusion."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="conclusion-form">
      {conclusions && conclusions.length > 0 && (
        <>
          <h4 className="experiment-form-subheading">Current conclusion</h4>
          <div className={`conclusion-card conclusion-card-${conclusions[0].state}`}>
            <strong>{STATES.find((s) => s.value === conclusions[0].state)?.label}</strong>
            <p>{conclusions[0].statement}</p>
            <p className="decision-log-timestamp">{new Date(conclusions[0].created_at).toLocaleString()}</p>
          </div>
          {conclusions.length > 1 && (
            <p className="research-gap-note">{conclusions.length - 1} earlier conclusion(s) also recorded — history preserved, never overwritten.</p>
          )}
        </>
      )}

      <h4 className="experiment-form-subheading">Record a conclusion</h4>
      <p className="field-hint">
        Every field below is required — a conclusion cannot be saved without stating what it's based
        on. This never automatically follows from a profitable-looking result.
      </p>
      <label className="field">
        <span className="field-label">State</span>
        <select value={state} onChange={(e) => setState(e.target.value as ConclusionState)}>
          {STATES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span className="field-label">Statement</span>
        <textarea rows={2} value={statement} onChange={(e) => setStatement(e.target.value)} placeholder="What did this experiment actually teach us?" />
      </label>
      <label className="field">
        <span className="field-label">References hypothesis</span>
        <input value={refHypothesis} onChange={(e) => setRefHypothesis(e.target.value)} placeholder="Which hypothesis, stated how?" />
      </label>
      <label className="field">
        <span className="field-label">References sample</span>
        <input value={refSample} onChange={(e) => setRefSample(e.target.value)} placeholder="e.g. 63 independent episodes" />
      </label>
      <label className="field">
        <span className="field-label">References baseline</span>
        <input value={refBaseline} onChange={(e) => setRefBaseline(e.target.value)} placeholder="What was it compared against?" />
      </label>
      <label className="field">
        <span className="field-label">References outcomes</span>
        <input value={refOutcomes} onChange={(e) => setRefOutcomes(e.target.value)} placeholder="e.g. mean -0.03%, median -0.06%" />
      </label>
      <label className="field">
        <span className="field-label">References statistical validation</span>
        <input value={refValidation} onChange={(e) => setRefValidation(e.target.value)} placeholder="e.g. p=0.25 (Method A), p=0.41 (Method B), both n.s." />
      </label>
      <label className="field">
        <span className="field-label">Limitations</span>
        <textarea rows={2} value={limitations} onChange={(e) => setLimitations(e.target.value)} placeholder="What remains uncertain? What would change this conclusion?" />
      </label>
      {error && <div className="error-banner">{error}</div>}
      <button type="button" onClick={handleSubmit} disabled={!allFilled || saving}>
        {saving ? "Saving…" : "Save conclusion"}
      </button>
    </div>
  );
}
