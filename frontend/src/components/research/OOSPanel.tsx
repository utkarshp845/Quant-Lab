import { useEffect, useState } from "react";
import {
  associateOosPartition,
  createOosPartition,
  evaluateOosPeriod,
  getOosEvidence,
  listOosEvaluations,
  listOosPartitions,
  listOosPeriods,
  listOosStatisticalReviews,
  registerOosPeriod,
  runOosEvaluation,
  runOosStatisticalReview,
} from "../../api/client";
import type { Experiment } from "../../types/research";
import type { OOSEvaluationResult } from "../../types/oosEvaluation";
import type { OOSEvidenceSummary, OOSPeriod } from "../../types/oosEvidence";
import type { OOSPartition } from "../../types/oosPartitions";
import type { OOSStatisticalReview } from "../../types/oosStatisticalReview";
import { InfoDisclosure } from "../InfoDisclosure";
import { apiErrorMessage, fmtPercentOrDash } from "../../utils/researchFormat";

const VERDICT_LABELS: Record<string, string> = {
  supported: "Supported",
  not_supported: "Not supported",
  inconclusive: "Inconclusive",
  insufficient_data: "Insufficient data",
};

/**
 * OOS stage: out-of-sample evaluation against holdout data the
 * hypothesis never touched during research (backend/app/oos*,
 * app/oos_evaluation/, app/oos_evidence/, app/oos_statistical_review/
 * -- all reused unmodified). Backtesting is NOT the final validation
 * step; this is the next one.
 */
export function OOSPanel({ experiment, onExperimentUpdated }: { experiment: Experiment; onExperimentUpdated: (e: Experiment) => void }) {
  const [partitions, setPartitions] = useState<OOSPartition[] | null>(null);
  const [selectedPartitionId, setSelectedPartitionId] = useState("");
  const [creatingPartition, setCreatingPartition] = useState(false);
  const [devStart, setDevStart] = useState("");
  const [devEnd, setDevEnd] = useState("");
  const [holdStart, setHoldStart] = useState("");
  const [holdEnd, setHoldEnd] = useState("");
  const [error, setError] = useState<string | null>(null);

  const [evaluations, setEvaluations] = useState<OOSEvaluationResult[] | null>(null);
  const [evaluating, setEvaluating] = useState(false);

  const [periods, setPeriods] = useState<OOSPeriod[] | null>(null);
  const [evidence, setEvidence] = useState<OOSEvidenceSummary | null>(null);
  const [registerPartitionId, setRegisterPartitionId] = useState("");

  const [review, setReview] = useState<OOSStatisticalReview | null>(null);
  const [reviews, setReviews] = useState<OOSStatisticalReview[] | null>(null);
  const [runningReview, setRunningReview] = useState(false);

  useEffect(() => {
    listOosPartitions({ symbol: experiment.symbol, timeframe: experiment.timeframe, provider: experiment.provider }).then(setPartitions);
  }, [experiment.symbol, experiment.timeframe, experiment.provider]);

  const isLocked = experiment.lifecycle_state !== "draft";
  const hasPartition = experiment.oos_partition_id != null;

  useEffect(() => {
    if (!isLocked || !hasPartition) return;
    listOosEvaluations(experiment.id).then(setEvaluations).catch(() => setEvaluations([]));
    listOosPeriods(experiment.id).then(setPeriods).catch(() => setPeriods([]));
    getOosEvidence(experiment.id).then(setEvidence).catch(() => setEvidence(null));
    listOosStatisticalReviews(experiment.id).then(setReviews).catch(() => setReviews([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [experiment.id, isLocked, hasPartition]);

  async function handleAssociate() {
    if (!selectedPartitionId) return;
    setError(null);
    try {
      const updated = await associateOosPartition(experiment.id, { oos_partition_id: selectedPartitionId });
      onExperimentUpdated(updated);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not associate this partition."));
    }
  }

  async function handleCreatePartition() {
    if (!devStart || !devEnd || !holdStart || !holdEnd) return;
    setError(null);
    try {
      const partition = await createOosPartition({
        symbol: experiment.symbol,
        timeframe: experiment.timeframe,
        provider: experiment.provider,
        development_start: new Date(devStart).toISOString(),
        development_end: new Date(devEnd).toISOString(),
        holdout_start: new Date(holdStart).toISOString(),
        holdout_end: new Date(holdEnd).toISOString(),
      });
      setPartitions((prev) => [partition, ...(prev ?? [])]);
      setSelectedPartitionId(partition.id);
      setCreatingPartition(false);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not create this partition."));
    }
  }

  async function handleRunEvaluation() {
    setEvaluating(true);
    setError(null);
    try {
      await runOosEvaluation(experiment.id);
      const [evals, ev] = await Promise.all([listOosEvaluations(experiment.id), getOosEvidence(experiment.id)]);
      setEvaluations(evals);
      setEvidence(ev);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not run OOS evaluation."));
    } finally {
      setEvaluating(false);
    }
  }

  async function handleRegisterAndEvaluate() {
    if (!registerPartitionId) return;
    setError(null);
    try {
      await registerOosPeriod(experiment.id, { oos_partition_id: registerPartitionId });
      await evaluateOosPeriod(experiment.id, registerPartitionId);
      const [evals, per, ev] = await Promise.all([
        listOosEvaluations(experiment.id),
        listOosPeriods(experiment.id),
        getOosEvidence(experiment.id),
      ]);
      setEvaluations(evals);
      setPeriods(per);
      setEvidence(ev);
      setRegisterPartitionId("");
    } catch (err) {
      setError(apiErrorMessage(err, "Could not register/evaluate this period."));
    }
  }

  async function handleRunReview() {
    setRunningReview(true);
    setError(null);
    try {
      const r = await runOosStatisticalReview(experiment.id);
      setReview(r);
      setReviews((prev) => [r, ...(prev ?? [])]);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not run the OOS statistical review."));
    } finally {
      setRunningReview(false);
    }
  }

  if (!isLocked) {
    return (
      <div className="oos-panel">
        <p className="section-subtitle">
          Reserve an out-of-sample holdout partition now, before freezing -- an OOS partition can only be
          linked to a DRAFT experiment; once frozen, this link is permanent.
        </p>
        {error && <div className="error-banner">{error}</div>}
        {partitions && partitions.length > 0 && (
          <label className="field">
            <span className="field-label">Existing partition ({experiment.symbol}/{experiment.timeframe}/{experiment.provider})</span>
            <select value={selectedPartitionId} onChange={(e) => setSelectedPartitionId(e.target.value)}>
              <option value="">— select —</option>
              {partitions.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label ?? p.id.slice(0, 8)} — dev {p.development_start.slice(0, 10)}..{p.development_end.slice(0, 10)}, holdout{" "}
                  {p.holdout_start.slice(0, 10)}..{p.holdout_end.slice(0, 10)}
                </option>
              ))}
            </select>
          </label>
        )}
        {selectedPartitionId && (
          <button type="button" onClick={handleAssociate}>
            Reserve this partition for this experiment
          </button>
        )}
        {!creatingPartition && (
          <button type="button" onClick={() => setCreatingPartition(true)}>
            + Create new partition
          </button>
        )}
        {creatingPartition && (
          <div className="observation-picker-form">
            <div className="experiment-form-row">
              <label className="field">
                <span className="field-label">Development start</span>
                <input type="datetime-local" value={devStart} onChange={(e) => setDevStart(e.target.value)} />
              </label>
              <label className="field">
                <span className="field-label">Development end</span>
                <input type="datetime-local" value={devEnd} onChange={(e) => setDevEnd(e.target.value)} />
              </label>
            </div>
            <div className="experiment-form-row">
              <label className="field">
                <span className="field-label">Holdout start</span>
                <input type="datetime-local" value={holdStart} onChange={(e) => setHoldStart(e.target.value)} />
              </label>
              <label className="field">
                <span className="field-label">Holdout end</span>
                <input type="datetime-local" value={holdEnd} onChange={(e) => setHoldEnd(e.target.value)} />
              </label>
            </div>
            <button type="button" onClick={handleCreatePartition}>
              Save partition
            </button>
          </div>
        )}
        {experiment.oos_partition_id && <p className="research-gap-note">Currently reserved: {experiment.oos_partition_id}</p>}
      </div>
    );
  }

  if (!hasPartition) {
    return (
      <div className="oos-panel">
        <p className="research-warning research-warning-warning">
          This experiment was frozen without an OOS partition reserved -- partitions can only be linked
          before freezing. Create a new version (see Lock section above) and reserve a partition before
          freezing it, to enable OOS evaluation.
        </p>
      </div>
    );
  }

  const completed = (evaluations ?? []).filter((e) => e.status === "completed");

  return (
    <div className="oos-panel">
      {error && <div className="error-banner">{error}</div>}
      <p className="section-subtitle">Reserved partition: <code>{experiment.oos_partition_id}</code></p>

      <button type="button" onClick={handleRunEvaluation} disabled={evaluating}>
        {evaluating ? "Evaluating…" : "Run OOS evaluation (original partition)"}
      </button>

      {evaluations && evaluations.length > 0 && (
        <div className="table-wrap">
          <table className="payoff-table">
            <thead>
              <tr>
                <th>Evaluated</th>
                <th>Status</th>
                <th>Signals</th>
              </tr>
            </thead>
            <tbody>
              {evaluations.map((e) => (
                <tr key={e.id}>
                  <td>{new Date(e.evaluated_at).toLocaleString()}</td>
                  <td>{e.status}</td>
                  <td>{e.signal_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h4 className="experiment-form-subheading">Accumulate more evidence</h4>
      <p className="field-hint">
        Register an additional, independently-created holdout partition as another OOS period for this
        SAME frozen hypothesis -- never touches the hypothesis itself.
      </p>
      {partitions && (
        <label className="field">
          <span className="field-label">Partition to register</span>
          <select value={registerPartitionId} onChange={(e) => setRegisterPartitionId(e.target.value)}>
            <option value="">— select —</option>
            {partitions
              .filter((p) => p.id !== experiment.oos_partition_id && !(periods ?? []).some((per) => per.oos_partition_id === p.id))
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label ?? p.id.slice(0, 8)} — holdout {p.holdout_start.slice(0, 10)}..{p.holdout_end.slice(0, 10)}
                </option>
              ))}
          </select>
        </label>
      )}
      <button type="button" onClick={handleRegisterAndEvaluate} disabled={!registerPartitionId}>
        + Register and evaluate this period
      </button>

      {evidence && (
        <>
          <h4 className="experiment-form-subheading">Accumulated evidence</h4>
          <dl className="feature-explorer-dataset-grid">
            <div>
              <dt>Periods / completed / failed</dt>
              <dd>
                {evidence.oos_period_count} / {evidence.completed_evaluation_count} / {evidence.failed_evaluation_count}
              </dd>
            </div>
            <div>
              <dt>Raw signals (pooled, correlated)</dt>
              <dd>{evidence.total_raw_signals}</dd>
            </div>
            <div>
              <dt>Independent episodes</dt>
              <dd>{evidence.total_independent_episodes}</dd>
            </div>
            <div>
              <dt>Mean / median return</dt>
              <dd>
                {fmtPercentOrDash(evidence.mean_return, 2)} / {fmtPercentOrDash(evidence.median_return, 2)}
              </dd>
            </div>
            <div>
              <dt>Win rate</dt>
              <dd>{fmtPercentOrDash(evidence.win_rate, 1)}</dd>
            </div>
          </dl>
          <p className="research-gap-note">
            Descriptive only -- no significance claim here. See the statistical review below for that.
          </p>
        </>
      )}

      <h4 className="experiment-form-subheading">OOS statistical review</h4>
      <p className="field-hint">
        A formal, read-only review of every completed OOS period accumulated so far -- fewer than 10
        independent episodes always returns INSUFFICIENT_DATA, never a fabricated statistic.
      </p>
      <button type="button" onClick={handleRunReview} disabled={runningReview || completed.length === 0}>
        {runningReview ? "Running…" : "Run OOS statistical review"}
      </button>
      {completed.length === 0 && <p className="research-gap-note">Run at least one completed evaluation first.</p>}

      {(review ?? reviews?.[0]) && (
        <div className={`oos-verdict-card oos-verdict-${(review ?? reviews![0]).verdict}`}>
          <strong>{VERDICT_LABELS[(review ?? reviews![0]).verdict]}</strong>
          <p>{(review ?? reviews![0]).verdict_reasoning}</p>
          <p className="research-gap-note">
            {(review ?? reviews![0]).sample_sizes.episode_count} independent episodes across{" "}
            {(review ?? reviews![0]).sample_sizes.evaluation_count} evaluation(s).
          </p>
        </div>
      )}

      <InfoDisclosure title="+ What does this verdict mean?">
        <p>This is a statement about evidence for the forward-return hypothesis only -- never a trading recommendation, never a claim about profitability.</p>
        <ul className="research-interpretation-list">
          <li><strong>Supported</strong>: both dependence-aware methods independently significant, directionally consistent, non-negligible effect size.</li>
          <li><strong>Not supported</strong>: both methods significant in the OPPOSITE direction.</li>
          <li><strong>Inconclusive</strong>: everything else, including p ≥ 0.05 alone -- this is NOT the same as "not supported".</li>
          <li><strong>Insufficient data</strong>: fewer than 10 independent episodes -- no formal statistic is even computed.</li>
        </ul>
      </InfoDisclosure>
    </div>
  );
}
