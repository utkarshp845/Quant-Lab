import { useEffect, useState } from "react";
import { previewConditions } from "../../api/client";
import type { Experiment } from "../../types/research";
import { describeFeatureCondition } from "../../utils/researchFormat";

/**
 * DETECT stage's condition funnel (spec section 14): "Total sessions ->
 * Condition 1 -> Condition 2 -> Condition 3 -> Qualifying events",
 * counts AND percentages, so it's obvious how much data survives each
 * condition. Built from POST /research/conditions/preview
 * (backend/app/research/design_preview.py) called once per growing
 * PREFIX of the experiment's own conditions -- e.g. [C1], [C1,C2],
 * [C1,C2,C3] -- the same sample-size-only, no-outcome endpoint the
 * Design stage uses, applied here to an already-defined experiment
 * instead of a not-yet-chosen candidate.
 */
export function ConditionFunnel({ experiment }: { experiment: Experiment }) {
  const [counts, setCounts] = useState<(number | null)[] | null>(null);
  const [totalBars, setTotalBars] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      const results: (number | null)[] = [];
      let total = 0;
      for (let i = 1; i <= experiment.conditions.length; i++) {
        try {
          const r = await previewConditions({
            symbol: experiment.symbol,
            start_date: experiment.start_date,
            end_date: experiment.end_date,
            timeframe: experiment.timeframe,
            provider: experiment.provider,
            conditions: experiment.conditions.slice(0, i),
          });
          results.push(r.matching_signal_count);
          total = r.total_feature_records; // invariant across every call above -- same range, just captured once
        } catch {
          results.push(null);
        }
      }
      if (!cancelled) {
        setCounts(results);
        setTotalBars(total);
      }
    }
    run();
    return () => {
      cancelled = true;
    };
  }, [experiment]);

  const pct = (n: number | null) => (n === null || totalBars === 0 ? "—" : `${((n / totalBars) * 100).toFixed(1)}%`);

  return (
    <div className="condition-funnel">
      <div className="condition-funnel-row">
        <span className="condition-funnel-step">Total bars with features</span>
        <span className="condition-funnel-count">{totalBars}</span>
        <span className="condition-funnel-pct">100%</span>
      </div>
      {experiment.conditions.map((c, i) => (
        <div className="condition-funnel-row" key={i}>
          <span className="condition-funnel-step">
            + <code>{describeFeatureCondition(c)}</code>
          </span>
          <span className="condition-funnel-count">{counts ? (counts[i] ?? "—") : "…"}</span>
          <span className="condition-funnel-pct">{counts ? pct(counts[i]) : "…"}</span>
        </div>
      ))}
      {counts && counts.length > 0 && typeof counts[counts.length - 1] === "number" && (counts[counts.length - 1] as number) < 30 && (
        <p className="research-warning research-warning-warning">
          Only {counts[counts.length - 1]} bar(s) satisfy every condition -- sample may be underpowered
          once forward-return measurability further reduces it.
        </p>
      )}
    </div>
  );
}
