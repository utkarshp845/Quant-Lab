import { useMemo, useState } from "react";
import type { ExperimentEvent, Experiment } from "../../types/research";
import { describeFeatureCondition, fmtNumberOrDash, fmtPercentOrDash } from "../../utils/researchFormat";
import { BacktestPanel } from "./BacktestPanel";
import { ComingSoonPanel } from "../ComingSoonPanel";
import { ConclusionForm } from "./ConclusionForm";
import { ConditionFunnel } from "./ConditionFunnel";
import { ExperimentVersions } from "./ExperimentVersions";
import { LineageView } from "./LineageView";
import { OOSPanel } from "./OOSPanel";
import { ResearchDistributionChart } from "./ResearchDistributionChart";
import { conditionCountWarnings, dateRangeWarnings, multipleTestingWarning, sampleSizeWarnings } from "./researchWarnings";
import { SegmentationPanel } from "./SegmentationPanel";
import { StatisticalValidationPanel } from "./StatisticalValidationPanel";
import { WarningsPanel } from "./WarningsPanel";

export function ExperimentResultsView({
  experiment,
  events,
  eventsLoading,
  eventsError,
  running,
  runError,
  onRun,
  sameSymbolExperimentCount,
  freezing,
  freezeError,
  onFreeze,
  onViewDesignGroup,
  onViewVersion,
  onNewVersion,
  onExperimentUpdated,
}: {
  experiment: Experiment;
  events: ExperimentEvent[] | null;
  eventsLoading: boolean;
  eventsError: string | null;
  running: boolean;
  runError: string | null;
  onRun: () => void;
  sameSymbolExperimentCount: number;
  freezing: boolean;
  freezeError: string | null;
  onFreeze: () => void;
  onViewDesignGroup: (designGroupId: string) => void;
  onViewVersion: (id: string) => void;
  onNewVersion: () => void;
  onExperimentUpdated: (experiment: Experiment) => void;
}) {
  const [drillDownTimestamp, setDrillDownTimestamp] = useState<string | null>(null);
  const [selectedBacktestId, setSelectedBacktestId] = useState<string | null>(null);

  const warnings = useMemo(() => {
    const list = [
      ...dateRangeWarnings(experiment.start_date, experiment.end_date),
      ...conditionCountWarnings(experiment.conditions.length),
    ];
    if (experiment.results) list.push(...sampleSizeWarnings(experiment.results.total_events));
    list.push(...multipleTestingWarning(sameSymbolExperimentCount));
    return list;
  }, [experiment, sameSymbolExperimentCount]);

  return (
    <div className="experiment-results-view">
      {/* ---- Dataset / date range ---- */}
      <section className="section research-block research-block-dataset">
        <h2 className="section-title">Dataset</h2>
        <dl className="feature-explorer-dataset-grid">
          <div>
            <dt>Symbol</dt>
            <dd>{experiment.symbol}</dd>
          </div>
          <div>
            <dt>Date range</dt>
            <dd>
              {experiment.start_date} → {experiment.end_date}
            </dd>
          </div>
          <div>
            <dt>Timeframe</dt>
            <dd>{experiment.timeframe}</dd>
          </div>
          <div>
            <dt>Provider (dataset)</dt>
            <dd>{experiment.provider}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd className={`experiment-status experiment-status-${experiment.status}`}>{experiment.status}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>
              <code>{experiment.created_at}</code>
            </dd>
          </div>
          {experiment.completed_at && (
            <div>
              <dt>Last run completed</dt>
              <dd>
                <code>{experiment.completed_at}</code>
              </dd>
            </div>
          )}
        </dl>
      </section>

      {/* ---- Hypothesis ---- */}
      <section className="section research-block research-block-hypothesis">
        <h2 className="section-title">Hypothesis</h2>
        <p>{experiment.hypothesis}</p>
        {(experiment.expected_direction || experiment.expected_behavior || experiment.rationale || experiment.invalidation_criteria) ? (
          <dl className="feature-explorer-dataset-grid">
            {experiment.expected_direction && (
              <div>
                <dt>Expected direction</dt>
                <dd>{experiment.expected_direction}</dd>
              </div>
            )}
            {experiment.expected_behavior && (
              <div>
                <dt>Expected behavior</dt>
                <dd>{experiment.expected_behavior}</dd>
              </div>
            )}
            {experiment.rationale && (
              <div>
                <dt>Rationale</dt>
                <dd>{experiment.rationale}</dd>
              </div>
            )}
            {experiment.invalidation_criteria && (
              <div>
                <dt>Invalidation criteria</dt>
                <dd>{experiment.invalidation_criteria}</dd>
              </div>
            )}
          </dl>
        ) : (
          <p className="research-gap-note">Legacy experiment — structured hypothesis metadata unavailable.</p>
        )}
      </section>

      {/* ---- Design / provenance ---- */}
      {experiment.design_group_id && (
        <section className="section research-block">
          <h2 className="section-title">Design</h2>
          <p>
            Candidate <strong>{experiment.candidate_label ?? "?"}</strong> in design group{" "}
            <code>{experiment.design_group_id}</code>.
          </p>
          <button type="button" onClick={() => onViewDesignGroup(experiment.design_group_id!)}>
            View design group &amp; decision log
          </button>
        </section>
      )}

      {/* ---- Conditions & outcome ---- */}
      <section className="section research-block">
        <h2 className="section-title">Conditions (AND)</h2>
        <ul className="research-condition-list">
          {experiment.conditions.map((condition, i) => (
            <li key={i}>
              <code>{describeFeatureCondition(condition)}</code>
            </li>
          ))}
        </ul>
        <p className="condition-metric-preview">Feature Engine contract version: <code>{experiment.feature_contract_version}</code></p>
        <h2 className="section-title research-outcome-heading">Outcome</h2>
        <p>
          <code>forward_return</code> ({experiment.outcome.horizon_minutes}m) {experiment.outcome.operator}{" "}
          {(experiment.outcome.threshold * 100).toFixed(2)}%
        </p>
      </section>

      <div className="experiment-run-row">
        <button type="button" onClick={onRun} disabled={running}>
          {running ? "Running…" : experiment.status === "draft" ? "Run experiment" : "Re-run"}
        </button>
        {running && (
          <span className="loading-pill experiment-run-pill">
            Running against historical data — this can take a moment for a wide date range…
          </span>
        )}
      </div>
      {runError && <div className="error-banner">{runError}</div>}
      {experiment.status === "failed" && experiment.error_message && (
        <div className="error-banner">Run failed: {experiment.error_message}</div>
      )}

      {/* ---- Lock ---- */}
      <section className="section research-block">
        <h2 className="section-title">
          Lock{" "}
          <span className={`experiment-lifecycle experiment-lifecycle-${experiment.lifecycle_state}`}>
            {experiment.lifecycle_state}
          </span>
        </h2>
        {experiment.lifecycle_state === "draft" ? (
          <>
            <p className="section-subtitle">
              Freezing commits this hypothesis's definition -- after that point, it cannot silently change.
              Do this once you're done iterating, before backtesting or OOS evaluation.
            </p>
            <button type="button" onClick={onFreeze} disabled={freezing}>
              {freezing ? "Freezing…" : "Freeze this experiment"}
            </button>
            {freezeError && <div className="error-banner">{freezeError}</div>}
          </>
        ) : (
          <p className="section-subtitle">
            Frozen at <code>{experiment.frozen_at}</code>. Hash <code>{experiment.hypothesis_hash?.slice(0, 16)}…</code>
          </p>
        )}
        <h3 className="experiment-form-subheading">Versions</h3>
        <ExperimentVersions experimentId={experiment.id} onView={onViewVersion} onNewVersion={onNewVersion} />
      </section>

      {/* ---- Detect ---- */}
      <section className="section research-block">
        <h2 className="section-title">Detect</h2>
        <p className="section-subtitle">
          How much data survives each condition, in order -- counts and percentages, computed with no
          outcome ever attached (same preview endpoint the Design stage uses).
        </p>
        <ConditionFunnel experiment={experiment} />
      </section>

      {experiment.status === "completed" && experiment.results && (
        <>
          <WarningsPanel warnings={warnings} />

          {/* ---- Results ---- */}
          <section className="section research-block research-block-results">
            <h2 className="section-title">Results</h2>
            {experiment.results.total_events === 0 ? (
              <p className="research-no-results">
                No qualifying signals found for this condition in the selected date range. This is a valid result --
                the hypothesis's condition never occurred (or never occurred with enough remaining data to measure
                the outcome) in this dataset.
              </p>
            ) : (
              <div className="research-results-grid">
                <div className="metric-card">
                  <span className="exec-check-label">Sample count</span>
                  <span className="exec-check-value">{experiment.results.total_events}</span>
                </div>
                <div className="metric-card">
                  <span className="exec-check-label">Successful / Failed</span>
                  <span className="exec-check-value">
                    {experiment.results.successful_events} / {experiment.results.failed_events}
                  </span>
                </div>
                <div className="metric-card">
                  <span className="exec-check-label">Success rate</span>
                  <span className="exec-check-value">{fmtPercentOrDash(experiment.results.success_rate, 1)}</span>
                </div>
                <div className="metric-card">
                  <span className="exec-check-label">Mean outcome</span>
                  <span className="exec-check-value">{fmtPercentOrDash(experiment.results.average_outcome, 2)}</span>
                </div>
                <div className="metric-card">
                  <span className="exec-check-label">Median outcome</span>
                  <span className="exec-check-value">{fmtPercentOrDash(experiment.results.median_outcome, 2)}</span>
                </div>
                <div className="metric-card">
                  <span className="exec-check-label">Std deviation</span>
                  <span className="exec-check-value">{fmtNumberOrDash(experiment.results.std_dev_outcome, 4)}</span>
                </div>
                <div className="metric-card">
                  <span className="exec-check-label">Min outcome</span>
                  <span className="exec-check-value">{fmtPercentOrDash(experiment.results.min_outcome, 2)}</span>
                </div>
                <div className="metric-card">
                  <span className="exec-check-label">Max outcome</span>
                  <span className="exec-check-value">{fmtPercentOrDash(experiment.results.max_outcome, 2)}</span>
                </div>
              </div>
            )}
            <p className="research-gap-note">
              Percentile statistics (5th/25th/75th/95th) and probability above/below a configurable threshold are not
              shown here -- the Research Engine does not compute them yet (only the 9 stats above). See this
              workspace's gap notice.
            </p>
          </section>

          {experiment.results.total_events > 0 && (
            <section className="section research-block">
              <h2 className="section-title">Forward-return distribution</h2>
              <p className="section-subtitle">
                One dot per qualifying signal's actual outcome (not a histogram -- see caption below the chart).
              </p>
              {eventsLoading && <p>Loading events…</p>}
              {eventsError && <div className="error-banner">{eventsError}</div>}
              {events && <ResearchDistributionChart events={events} results={experiment.results} />}
            </section>
          )}

          {events && events.length > 0 && (
            <section className="section research-block">
              <h2 className="section-title">Individual events</h2>
              <p className="section-subtitle">Every qualifying signal, not just the aggregate -- click through to see exactly why each one qualified.</p>
              <div className="table-wrap">
                <table className="payoff-table">
                  <thead>
                    <tr>
                      <th>Signal time</th>
                      <th>Signal price</th>
                      <th>Outcome</th>
                      <th>Success</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {events.slice(0, 50).map((e) => (
                      <tr key={e.signal_timestamp}>
                        <td>
                          <code>{e.signal_timestamp}</code>
                        </td>
                        <td>{fmtNumberOrDash(e.signal_price, 2)}</td>
                        <td>{fmtPercentOrDash(e.outcome_value, 2)}</td>
                        <td>{e.success ? "yes" : "no"}</td>
                        <td>
                          <button type="button" onClick={() => setDrillDownTimestamp(e.signal_timestamp)}>
                            Why did this qualify?
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {events.length > 50 && <p className="research-gap-note">Showing the first 50 of {events.length} events.</p>}
            </section>
          )}

          <SegmentationPanel />

          {/* ---- Interpretation ---- */}
          <section className="section research-block research-block-interpretation">
            <h2 className="section-title">Interpretation</h2>
            <ul className="research-interpretation-list">
              <li>
                <strong>Success rate</strong> measures how often the outcome you configured above was satisfied when
                the condition fired -- it is not "probability of profit" unless your outcome happens to be defined
                that way.
              </li>
              <li>
                Mean/median/min/max/std-dev describe <em>this sample, in this date range</em> -- they are not a
                forecast or a guarantee about what happens next.
              </li>
              <li>Review the warnings above before treating this as a meaningful pattern rather than noise.</li>
              <li>This is not financial advice and not a trading signal -- it is a statistic about historical data.</li>
            </ul>
          </section>
        </>
      )}

      {/* ---- Backtest ---- */}
      <section className="section research-block">
        <h2 className="section-title">Backtest</h2>
        <BacktestPanel experimentId={experiment.id} onSelectBacktest={(id) => setSelectedBacktestId(id)} />
      </section>

      {/* ---- Compare + Validate ---- */}
      {selectedBacktestId && (
        <section className="section research-block">
          <StatisticalValidationPanel backtestId={selectedBacktestId} />
        </section>
      )}

      {/* ---- Conclude ---- */}
      <section className="section research-block">
        <h2 className="section-title">Conclude</h2>
        <ConclusionForm experimentId={experiment.id} />
      </section>

      {/* ---- OOS ---- */}
      <section className="section research-block">
        <h2 className="section-title">OOS — out-of-sample evaluation</h2>
        <p className="section-subtitle">
          Backtesting is not the final validation step. This evaluates the FROZEN hypothesis against
          holdout data it has never touched during research.
        </p>
        <OOSPanel experiment={experiment} onExperimentUpdated={onExperimentUpdated} />
      </section>

      {/* ---- Paper Trade / Live (honest placeholder) ---- */}
      <ComingSoonPanel
        title="Paper Trade / Live"
        reason="Not implemented. No simulated account, no order execution, at any point in this project. The intended eventual path is Research -> Validation -> Backtest -> OOS -> Paper Trade -> Live -- this placeholder exists so that path stays visible rather than silently missing."
      />

      {drillDownTimestamp && (
        <LineageView experimentId={experiment.id} signalTimestamp={drillDownTimestamp} onClose={() => setDrillDownTimestamp(null)} />
      )}
    </div>
  );
}
