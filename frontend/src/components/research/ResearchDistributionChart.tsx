import type { ExperimentEvent, ExperimentResults } from "../../types/research";

const WIDTH = 760;
const HEIGHT = 180;
const MARGIN = { top: 16, right: 24, bottom: 32, left: 24 };
const PLOT_W = WIDTH - MARGIN.left - MARGIN.right;
const PLOT_H = HEIGHT - MARGIN.top - MARGIN.bottom;
const LANES = 6; // deterministic vertical spread so overlapping points stay visible, not a real density estimate

/**
 * A raw dot/strip plot of each event's own `outcome_value` -- NOT a
 * histogram. A true histogram needs chosen bin edges and a per-bin
 * count, which is a real aggregation the backend does not expose
 * (ExperimentResults has no bins field, unlike Monte Carlo's
 * server-computed HistogramBin[] -- see src/components/
 * MonteCarloHistogramChart.tsx). Binning here would mean inventing a
 * statistic client-side, which this workspace's own constraints rule
 * out -- see the Research workspace's gap notice. Every dot plotted is
 * one already-returned `outcome_value`, positioned only; the reference
 * lines are the backend's own average/median/min/max, nothing derived
 * further.
 */
export function ResearchDistributionChart({ events, results }: { events: ExperimentEvent[]; results: ExperimentResults }) {
  if (events.length === 0 || results.min_outcome === null || results.max_outcome === null) {
    return <p className="research-chart-empty">No events to plot.</p>;
  }

  const domainMin = Math.min(results.min_outcome, 0);
  const domainMax = Math.max(results.max_outcome, 0);
  const span = domainMax - domainMin || 1;
  const xScale = (v: number) => MARGIN.left + ((v - domainMin) / span) * PLOT_W;
  const laneHeight = PLOT_H / LANES;
  const yForLane = (lane: number) => MARGIN.top + PLOT_H - laneHeight * lane - laneHeight / 2;

  const zeroX = xScale(0);

  return (
    <div>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="payoff-chart research-dist-chart" role="img" aria-label="Forward-return outcomes, one dot per signal">
        <line x1={MARGIN.left} y1={MARGIN.top + PLOT_H} x2={MARGIN.left + PLOT_W} y2={MARGIN.top + PLOT_H} className="chart-axis-line" />
        {domainMin < 0 && domainMax > 0 && (
          <line x1={zeroX} y1={MARGIN.top} x2={zeroX} y2={MARGIN.top + PLOT_H} className="chart-zero-line" />
        )}
        {results.average_outcome !== null && (
          <line x1={xScale(results.average_outcome)} y1={MARGIN.top} x2={xScale(results.average_outcome)} y2={MARGIN.top + PLOT_H} className="chart-ref-line-accent" />
        )}
        {results.median_outcome !== null && (
          <line x1={xScale(results.median_outcome)} y1={MARGIN.top} x2={xScale(results.median_outcome)} y2={MARGIN.top + PLOT_H} className="chart-ref-line" />
        )}
        {events.map((e, i) => (
          <circle
            key={`${e.signal_timestamp}-${i}`}
            cx={xScale(e.outcome_value)}
            cy={yForLane(i % LANES)}
            r={4}
            className={e.success ? "dist-bar-profit" : "dist-bar-loss"}
          />
        ))}
        <text x={MARGIN.left} y={HEIGHT - 6} className="chart-axis-label">
          {(domainMin * 100).toFixed(1)}%
        </text>
        <text x={MARGIN.left + PLOT_W} y={HEIGHT - 6} textAnchor="end" className="chart-axis-label">
          {(domainMax * 100).toFixed(1)}%
        </text>
      </svg>
      <div className="chart-legend">
        <span className="legend-item">
          <span className="legend-swatch" style={{ background: "var(--color-profit)", opacity: 0.55 }} />
          Outcome satisfied
        </span>
        <span className="legend-item">
          <span className="legend-swatch" style={{ background: "var(--color-loss)", opacity: 0.55 }} />
          Outcome not satisfied
        </span>
        <span className="legend-item">
          <span className="legend-swatch legend-line" style={{ background: "var(--color-accent)" }} />
          Mean
        </span>
        <span className="legend-item">
          <span className="legend-swatch legend-line" style={{ background: "var(--color-text)" }} />
          Median
        </span>
      </div>
    </div>
  );
}
