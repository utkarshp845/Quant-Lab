import type { HistogramBin } from "../types/monteCarlo";
import { fmtPercent, fmtUsd } from "../utils/format";

interface MonteCarloHistogramChartProps {
  histogram: HistogramBin[];
  breakeven: number;
  underlyingPrice: number;
}

const WIDTH = 760;
const HEIGHT = 320;
const MARGIN = { top: 20, right: 24, bottom: 40, left: 50 };
const PLOT_W = WIDTH - MARGIN.left - MARGIN.right;
const PLOT_H = HEIGHT - MARGIN.top - MARGIN.bottom;

/**
 * Frequency histogram of the actual simulated outcomes -- this is the
 * EMPIRICAL counterpart to Phase 2's closed-form bar chart. Bar height
 * is "how many of the N simulated draws landed here", not a
 * theoretical probability -- with enough simulations the two should
 * look nearly identical, which is the point.
 */
export function MonteCarloHistogramChart({
  histogram,
  breakeven,
  underlyingPrice,
}: MonteCarloHistogramChartProps) {
  if (histogram.length === 0) return null;

  const xMin = histogram[0].price_low;
  const xMax = histogram[histogram.length - 1].price_high;
  const xScale = (price: number) => MARGIN.left + ((price - xMin) / (xMax - xMin)) * PLOT_W;

  const maxFreq = Math.max(...histogram.map((b) => b.frequency));
  const yDomainMax = maxFreq * 1.15;
  const yScale = (f: number) => MARGIN.top + PLOT_H - (f / yDomainMax) * PLOT_H;

  const yTicks = [0, yDomainMax * 0.5, yDomainMax];

  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      className="payoff-chart"
      role="img"
      aria-label="Histogram of simulated expiration prices"
    >
      {histogram.map((b) => {
        const x1 = xScale(b.price_low);
        const x2 = xScale(b.price_high);
        const y = yScale(b.frequency);
        return (
          <rect
            key={b.price_low}
            x={x1 + 0.5}
            y={y}
            width={Math.max(x2 - x1 - 1, 0.5)}
            height={MARGIN.top + PLOT_H - y}
            className={b.is_profit ? "dist-bar-profit" : "dist-bar-loss"}
          />
        );
      })}

      <line
        x1={xScale(breakeven)}
        x2={xScale(breakeven)}
        y1={MARGIN.top}
        y2={HEIGHT - MARGIN.bottom}
        className="chart-ref-line"
      />
      <line
        x1={xScale(underlyingPrice)}
        x2={xScale(underlyingPrice)}
        y1={MARGIN.top}
        y2={HEIGHT - MARGIN.bottom}
        className="chart-ref-line-accent"
      />

      <line x1={MARGIN.left} x2={MARGIN.left} y1={MARGIN.top} y2={HEIGHT - MARGIN.bottom} className="chart-axis-line" />
      {yTicks.map((t) => (
        <text key={t} x={MARGIN.left - 8} y={yScale(t) + 3} textAnchor="end" className="chart-axis-sublabel">
          {fmtPercent(t, 1)}
        </text>
      ))}
      <text x={4} y={MARGIN.top - 6} className="chart-axis-label">
        Frequency
      </text>

      <text x={xScale(breakeven)} y={HEIGHT - MARGIN.bottom + 16} textAnchor="middle" className="chart-axis-label">
        Breakeven {fmtUsd(breakeven)}
      </text>
      <text x={WIDTH / 2} y={HEIGHT - 6} textAnchor="middle" className="chart-axis-label">
        Simulated Underlying Price at Expiration
      </text>
    </svg>
  );
}
