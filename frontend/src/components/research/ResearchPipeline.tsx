import { useEffect, useState } from "react";
import { getPipelineStatus } from "../../api/client";
import type { PipelineStage, PipelineStatusResponse } from "../../types/pipelineStatus";
import { apiErrorMessage } from "../../utils/researchFormat";

/**
 * The persistent visual Research Pipeline (spec section 3) -- the
 * primary navigation model for one experiment, not decoration. Driven
 * entirely by GET /research/experiments/{id}/pipeline-status
 * (backend/app/research/pipeline_status.py): every stage's purpose/
 * status/inputs/outputs/warnings/clickability comes from that response,
 * never computed client-side, so this component can never silently
 * disagree with what the backend actually knows about the experiment.
 *
 * DATA → FEATURES → OBSERVE → HYPOTHESIZE → DESIGN → DEFINE → LOCK →
 * DETECT → MEASURE → COMPARE → VALIDATE → CONCLUDE → BACKTEST → OOS.
 * PAPER TRADE/LIVE are deliberately NOT part of this list -- nothing in
 * the backend implements them; see OOSPanel's own placeholder for that
 * honest "not implemented" state instead of a fake pipeline stage.
 */

const STATUS_SYMBOL: Record<string, string> = {
  complete: "✓",
  in_progress: "…",
  warning: "⚠",
  blocked: "✕",
  not_started: "○",
};

export function ResearchPipeline({
  experimentId,
  onFocusStage,
  refreshKey,
}: {
  experimentId: string;
  /** Called whenever the focused stage changes (click, or the initial
   * load focusing `current_stage`) -- the parent (ResearchWorkspacePage
   * today; a future dedicated stage UI later) decides what showing
   * that stage actually means. */
  onFocusStage?: (stageId: string) => void;
  /** Bump this to force a re-fetch (e.g. after running the experiment,
   * freezing it, or creating a backtest) -- pipeline-status is a
   * server-computed read, never inferred client-side. */
  refreshKey?: number;
}) {
  const [status, setStatus] = useState<PipelineStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getPipelineStatus(experimentId)
      .then((response) => {
        if (cancelled) return;
        setStatus(response);
        setError(null);
        setFocusedId((prev) => prev ?? response.current_stage);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(apiErrorMessage(err, "Could not load pipeline status."));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [experimentId, refreshKey]);

  if (error) return <div className="error-banner">{error}</div>;
  if (!status) return <p className="research-pipeline-loading">Loading pipeline status…</p>;

  const focused = status.stages.find((s) => s.id === focusedId) ?? status.stages[0];

  function focus(stage: PipelineStage) {
    setFocusedId(stage.id);
    onFocusStage?.(stage.id);
  }

  return (
    <div className="research-pipeline">
      <div className="research-pipeline-next-action">
        <span className="research-pipeline-next-action-label">What should I do next?</span>
        <span className="research-pipeline-next-action-text">{status.next_action}</span>
      </div>

      <ol className="research-pipeline-stages" aria-label="Research pipeline">
        {status.stages.map((stage) => (
          <li key={stage.id}>
            <button
              type="button"
              className={[
                "research-pipeline-stage",
                `research-pipeline-stage-${stage.status}`,
                stage.id === focused.id ? "research-pipeline-stage-focused" : "",
                stage.id === status.current_stage ? "research-pipeline-stage-current" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              onClick={() => focus(stage)}
              title={stage.purpose}
            >
              <span className="research-pipeline-stage-symbol" aria-hidden="true">
                {STATUS_SYMBOL[stage.status] ?? "○"}
              </span>
              <span className="research-pipeline-stage-label">{stage.label}</span>
            </button>
          </li>
        ))}
      </ol>

      <div className="research-pipeline-detail">
        <h3>
          {focused.label}
          {focused.id === status.current_stage && <span className="research-pipeline-current-badge">current stage</span>}
        </h3>
        <p className="research-pipeline-purpose">{focused.purpose}</p>
        <dl className="research-pipeline-detail-grid">
          <div>
            <dt>Status</dt>
            <dd className={`research-pipeline-status-text research-pipeline-status-${focused.status}`}>
              {focused.status.replace("_", " ")}
            </dd>
          </div>
          <div>
            <dt>Inputs</dt>
            <dd>{focused.inputs.length > 0 ? focused.inputs.join(", ") : "—"}</dd>
          </div>
          <div>
            <dt>Outputs</dt>
            <dd>{focused.outputs.length > 0 ? focused.outputs.join(", ") : "—"}</dd>
          </div>
        </dl>
        {focused.warnings.length > 0 && (
          <div className="research-warnings">
            {focused.warnings.map((w, i) => (
              <div key={i} className="research-warning research-warning-warning">
                {w}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
