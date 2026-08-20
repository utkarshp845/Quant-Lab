import { useEffect, useState } from "react";
import { getEventLineage } from "../../api/client";
import type { EventLineage } from "../../types/researchLineage";
import { apiErrorMessage, fmtNumberOrDash } from "../../utils/researchFormat";

/**
 * "Why did this event qualify?" (spec section 12): RAW MARKET DATA ->
 * SESSION CLASSIFICATION -> FEATURE CALCULATION -> CONDITION
 * EVALUATION -> EVENT DETECTION -> SIGNAL TIMESTAMP/PRICE -> FORWARD
 * BARS -> OUTCOME, assembled entirely from backend/app/api/
 * research_lineage.py's already-persisted bundle. RAW (signal_bar/
 * outcome_bar) and DERIVED (feature_record, condition evaluations) are
 * rendered in visually distinct blocks -- never merged into one list --
 * per that spec section's own "never make derived values look like raw
 * market data" rule.
 */
export function LineageView({
  experimentId,
  signalTimestamp,
  onClose,
}: {
  experimentId: string;
  signalTimestamp: string;
  onClose: () => void;
}) {
  const [lineage, setLineage] = useState<EventLineage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLineage(null);
    setError(null);
    getEventLineage(experimentId, signalTimestamp)
      .then(setLineage)
      .catch((err) => setError(apiErrorMessage(err, "Could not load lineage for this event.")));
  }, [experimentId, signalTimestamp]);

  return (
    <div className="lineage-view-overlay" role="dialog" aria-label="Why did this event qualify?">
      <div className="lineage-view">
        <div className="experiment-list-header">
          <h3 className="section-title">Why did this event qualify?</h3>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>

        {error && <div className="error-banner">{error}</div>}
        {!lineage && !error && <p>Loading…</p>}

        {lineage && (
          <div className="lineage-chain">
            <div className="lineage-step lineage-step-raw">
              <span className="lineage-step-label">RAW · Signal bar</span>
              {lineage.signal_bar ? (
                <dl className="feature-explorer-dataset-grid">
                  <div>
                    <dt>Timestamp</dt>
                    <dd>
                      <code>{lineage.signal_bar.timestamp}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>OHLC</dt>
                    <dd>
                      {fmtNumberOrDash(lineage.signal_bar.open, 2)} / {fmtNumberOrDash(lineage.signal_bar.high, 2)} /{" "}
                      {fmtNumberOrDash(lineage.signal_bar.low, 2)} / {fmtNumberOrDash(lineage.signal_bar.close, 2)}
                    </dd>
                  </div>
                  <div>
                    <dt>Volume</dt>
                    <dd>{lineage.signal_bar.volume.toLocaleString()}</dd>
                  </div>
                </dl>
              ) : (
                <p className="research-gap-note">Bar no longer available.</p>
              )}
            </div>

            <div className="lineage-arrow">↓</div>

            <div className="lineage-step lineage-step-derived">
              <span className="lineage-step-label">DERIVED · Feature values at this bar</span>
              {lineage.feature_record ? (
                <p className="research-gap-note">
                  Feature Engine contract <code>{lineage.feature_record.feature_contract_version}</code>, computed{" "}
                  <code>{lineage.feature_record.calculated_at}</code>
                </p>
              ) : (
                <p className="research-gap-note">Feature record no longer available.</p>
              )}
            </div>

            <div className="lineage-arrow">↓</div>

            <div className="lineage-step lineage-step-condition">
              <span className="lineage-step-label">CONDITION EVALUATION</span>
              <ul className="research-condition-list">
                {lineage.condition_evaluations.map((c) => (
                  <li key={c.feature_id}>
                    <strong>{c.feature_name}</strong> ({c.feature_id}) {c.operator}{" "}
                    {c.value_max != null ? `${c.value} and ${c.value_max}` : String(c.value)} — observed{" "}
                    <code>{String(c.observed_value)}</code>
                    <div className="lineage-feature-description">{c.feature_description}</div>
                  </li>
                ))}
              </ul>
            </div>

            <div className="lineage-arrow">↓</div>

            <div className="lineage-step lineage-step-event">
              <span className="lineage-step-label">EVENT QUALIFIED</span>
              <p>
                <code>{lineage.signal_timestamp}</code> @ {fmtNumberOrDash(lineage.signal_bar?.close ?? null, 2)}
              </p>
            </div>

            <div className="lineage-arrow">↓</div>

            <div className="lineage-step lineage-step-raw">
              <span className="lineage-step-label">RAW · Outcome bar</span>
              {lineage.outcome_bar ? (
                <dl className="feature-explorer-dataset-grid">
                  <div>
                    <dt>Timestamp</dt>
                    <dd>
                      <code>{lineage.outcome_timestamp}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>Close</dt>
                    <dd>{fmtNumberOrDash(lineage.outcome_bar.close, 2)}</dd>
                  </div>
                  <div>
                    <dt>Outcome value</dt>
                    <dd>{(lineage.outcome_value * 100).toFixed(2)}%</dd>
                  </div>
                  <div>
                    <dt>Success</dt>
                    <dd>{lineage.success ? "yes" : "no"}</dd>
                  </div>
                </dl>
              ) : (
                <p className="research-gap-note">Outcome bar no longer available.</p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
