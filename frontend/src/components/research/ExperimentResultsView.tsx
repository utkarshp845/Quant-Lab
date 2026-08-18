import { useMemo } from "react";
import type { ExperimentEvent, Experiment } from "../../types/research";
import { describeFeatureCondition, fmtNumberOrDash, fmtPercentOrDash } from "../../utils/researchFormat";
import { ResearchDistributionChart } from "./ResearchDistributionChart";
import { conditionCountWarnings, dateRangeWarnings, multipleTestingWarning, sampleSizeWarnings } from "./researchWarnings";
import { SegmentationPanel } from "./SegmentationPanel";
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
}: {
  experiment: Experiment;
  events: ExperimentEvent[] | null;
  eventsLoading: boolean;
  eventsError: string | null;
  running: boolean;
  runError: string | null;
  onRun: () => void;
  sameSymbolExperimentCount: number;
}) {
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
      </section>

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
    </div>
  );
}
