import { useEffect, useState } from "react";
import { getExperimentVersions } from "../../api/client";
import type { ExperimentVersionsResponse } from "../../types/researchNotebook";
import { apiErrorMessage } from "../../utils/researchFormat";

/**
 * Experiment versioning (spec section 10): "Experiment 2 -> Definition
 * C -> Locked; Experiment 2A -> Definition C + changed threshold;
 * Experiment 2B -> Definition B. Show what changed between versions."
 * Driven entirely by GET .../versions (backend/app/research/versions.py)
 * -- a version tree is just Experiment rows linked by
 * parent_experiment_id, not a second entity.
 */
export function ExperimentVersions({
  experimentId,
  onView,
  onNewVersion,
}: {
  experimentId: string;
  onView: (id: string) => void;
  onNewVersion: () => void;
}) {
  const [data, setData] = useState<ExperimentVersionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getExperimentVersions(experimentId)
      .then(setData)
      .catch((err) => setError(apiErrorMessage(err, "Could not load version history.")));
  }, [experimentId]);

  if (error) return <div className="error-banner">{error}</div>;
  if (!data) return <p className="research-gap-note">Loading version history…</p>;

  return (
    <div className="experiment-versions">
      {data.versions.length <= 1 ? (
        <p className="research-gap-note">No other versions yet.</p>
      ) : (
        <ul className="experiment-versions-list">
          {data.versions.map((v) => (
            <li key={v.id} className={v.id === experimentId ? "experiment-versions-current" : ""}>
              <button type="button" className="experiment-versions-link" onClick={() => onView(v.id)}>
                {v.version_label ?? v.name}
              </button>
              <span className={`experiment-lifecycle experiment-lifecycle-${v.lifecycle_state}`}>{v.lifecycle_state}</span>
              {v.parent_experiment_id && <span className="experiment-versions-parent-note">from {v.parent_experiment_id.slice(0, 8)}…</span>}
            </li>
          ))}
        </ul>
      )}

      {data.diff_from_parent && (
        <div className="experiment-versions-diff">
          <h4 className="experiment-form-subheading">Changed from parent</h4>
          <table className="payoff-table">
            <thead>
              <tr>
                <th>Field</th>
                <th>Parent</th>
                <th>This version</th>
              </tr>
            </thead>
            <tbody>
              {data.diff_from_parent.map((d) => (
                <tr key={d.field}>
                  <td>{d.field}</td>
                  <td className="research-block code">{d.parent_value}</td>
                  <td className="research-block code">{d.child_value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <button type="button" onClick={onNewVersion}>
        + New version of this experiment
      </button>
    </div>
  );
}
