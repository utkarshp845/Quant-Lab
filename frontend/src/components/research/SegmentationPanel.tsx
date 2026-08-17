import { useState } from "react";
import { FEATURE_METRIC_LABELS } from "../../types/features";

const EXAMPLE_BUCKETS = "<1, 1–1.5, 1.5–2, 2–3, >3";

/**
 * Segmentation is fully specced (group/bucket results by a feature,
 * e.g. RVOL <1 / 1-1.5 / 1.5-2 / 2-3 / >3) but has no backend behind
 * it: computing per-bucket sample counts and statistics is a real
 * research calculation (bucketing + aggregation), and the Research
 * Engine does not expose an endpoint for it today -- see this
 * workspace's own gap notice. This panel shows the intended control
 * shape (so the vision stays visible) with the action disabled, rather
 * than faking a client-side bucketing/aggregation that would duplicate
 * a backend calculation.
 */
export function SegmentationPanel() {
  const [metric, setMetric] = useState(Object.keys(FEATURE_METRIC_LABELS)[0]);

  return (
    <section className="section segmentation-panel">
      <h2 className="section-title">Segmentation</h2>
      <p className="section-subtitle">
        Group these results by a feature value (e.g. Relative Volume buckets: {EXAMPLE_BUCKETS}) to look for a
        relationship. <strong>Not available yet</strong> -- the Research Engine does not expose a segmented-statistics
        endpoint, and computing bucket means/counts in this frontend would duplicate a backend calculation this
        workspace's own rules disallow.
      </p>
      <div className="condition-row">
        <span className="condition-row-label">Segment by</span>
        <select value={metric} onChange={(e) => setMetric(e.target.value)} disabled>
          {Object.entries(FEATURE_METRIC_LABELS).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        <button type="button" disabled title="Requires a backend segmentation endpoint">
          Run segmentation
        </button>
      </div>
    </section>
  );
}
