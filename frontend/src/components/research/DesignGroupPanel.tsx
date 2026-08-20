import { useEffect, useState } from "react";
import { listExperiments, previewConditions } from "../../api/client";
import type { Experiment } from "../../types/research";
import { apiErrorMessage, describeConditions } from "../../utils/researchFormat";
import { DecisionLog } from "./DecisionLog";

/**
 * DESIGN stage (spec section 8): compare candidate definitions of the
 * same hypothesis BEFORE any of them has been run. Sample size here
 * comes from POST /research/conditions/preview -- a count with no
 * outcome ever computed (backend/app/research/design_preview.py) --
 * never from running the candidate itself, which is what would let
 * outcome performance quietly influence which one gets picked.
 */
export function DesignGroupPanel({ designGroupId, onBack }: { designGroupId: string; onBack: () => void }) {
  const [candidates, setCandidates] = useState<Experiment[] | null>(null);
  const [previews, setPreviews] = useState<Record<string, number | "loading" | "error">>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listExperiments()
      .then((all) => setCandidates(all.filter((e) => e.design_group_id === designGroupId)))
      .catch((err) => setError(apiErrorMessage(err, "Could not load candidates.")));
  }, [designGroupId]);

  async function loadPreview(candidate: Experiment) {
    setPreviews((prev) => ({ ...prev, [candidate.id]: "loading" }));
    try {
      const result = await previewConditions({
        symbol: candidate.symbol,
        start_date: candidate.start_date,
        end_date: candidate.end_date,
        timeframe: candidate.timeframe,
        provider: candidate.provider,
        conditions: candidate.conditions,
      });
      setPreviews((prev) => ({ ...prev, [candidate.id]: result.matching_signal_count }));
    } catch {
      setPreviews((prev) => ({ ...prev, [candidate.id]: "error" }));
    }
  }

  return (
    <div className="page design-group-panel">
      <div className="experiment-list-header">
        <button type="button" onClick={onBack}>
          ← Back
        </button>
      </div>
      <section className="section">
        <h2 className="section-title">Design group: {designGroupId}</h2>
        <p className="section-subtitle">
          Candidate definitions considered together, before any outcome data existed. "Preview sample
          size" counts qualifying signals only -- it never computes or shows what happened afterward.
        </p>
        {error && <div className="error-banner">{error}</div>}
        {candidates === null && <p>Loading…</p>}
        {candidates !== null && candidates.length === 0 && (
          <p className="research-gap-note">No experiments share this design group id yet.</p>
        )}
        {candidates !== null && candidates.length > 0 && (
          <div className="table-wrap">
            <table className="payoff-table">
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th>Name</th>
                  <th>Conditions</th>
                  <th>Sample size (pre-outcome)</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((c) => (
                  <tr key={c.id}>
                    <td>{c.candidate_label ?? "—"}</td>
                    <td>{c.name}</td>
                    <td className="research-block code">{describeConditions(c.conditions)}</td>
                    <td>
                      {previews[c.id] === undefined && (
                        <button type="button" onClick={() => loadPreview(c)}>
                          Preview sample size
                        </button>
                      )}
                      {previews[c.id] === "loading" && "Loading…"}
                      {previews[c.id] === "error" && "Could not compute"}
                      {typeof previews[c.id] === "number" && `${previews[c.id]} matching bar(s)`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {candidates !== null && (
        <section className="section">
          <h2 className="section-title">Decision log</h2>
          <DecisionLog
            designGroupId={designGroupId}
            candidateOptions={candidates.map((c) => ({ id: c.id, label: `${c.candidate_label ?? c.name} (${c.name})` }))}
          />
        </section>
      )}
    </div>
  );
}
