import type { DistributionBucket, PayoffScenario } from "../types/bearPutSpread";
import { fmtPercent, fmtUsd } from "../utils/format";

interface DistributionChartProps {
  buckets: DistributionBucket[];
  payoffLine: PayoffScenario[];
  breakeven: number;
  underlyingPrice: number;
}

const WIDTH = 760;
const HEIGHT = 420;
const MARGIN = { top: 20, right: 56, bottom: 40, left: 56 };
const PLOT_W = WIDTH - MARGIN.left - MARGIN.right;
const PLOT_H = HEIGHT - MARGIN.top - MARGIN.bottom;

/**
 * Combines the probability distribution (bars, left axis) with the
 * payoff line (line, right axis) on one chart, sharing the same price
 * x-axis. This is the "probability distribution versus payoff
 * distribution, combined" view: bar height is how likely a price is,
 * bar color is whether that price is profitable, and the purple line
 * is the same piecewise-linear payoff shown in the payoff chart above.
 */
export function DistributionChart({
  buckets,
  payoffLine,
  breakeven,
  underlyingPrice,
}: DistributionChartProps) {
  if (buckets.length === 0) return null;

  // Infer the bucket step from an interior bucket so the two
  // open-ended tail bars can be drawn at the same visual width as
  // every other bar (their true price range is infinite).
  const interior = buckets.find((b) => b.price_low !== null && b.price_high !== null);
  const step = interior ? interior.price_high! - interior.price_low! : 2;

  const barRanges = buckets.map((b) => {
    const lo = b.price_low ?? (b.price_high as number) - step;
    const hi = b.price_high ?? (b.price_low as number) + step;
    return { ...b, lo, hi };
  });

  const xMin = barRanges[0].lo;
  const xMax = barRanges[barRanges.length - 1].hi;
  const xScale = (price: number) => MARGIN.left + ((price - xMin) / (xMax - xMin)) * PLOT_W;

  const maxProb = Math.max(...buckets.map((b) => b.probability));
  const probDomainMax = maxProb * 1.15;
  const yProbScale = (p: number) => MARGIN.top + PLOT_H - (p / probDomainMax) * PLOT_H;

  const pls = payoffLine.map((p) => p.pl_per_contract);
  const plMinRaw = Math.min(...pls, 0);
  const plMaxRaw = Math.max(...pls, 0);
  const plPad = Math.max((plMaxRaw - plMinRaw) * 0.15, 10);
  const plMin = plMinRaw - plPad;
  const plMax = plMaxRaw + plPad;
  const yPlScale = (pl: number) => MARGIN.top + PLOT_H - ((pl - plMin) / (plMax - plMin)) * PLOT_H;

  const linePoints = payoffLine
    .map((p) => `${xScale(p.expiration_price)},${yPlScale(p.pl_per_contract)}`)
    .join(" ");

  const probTicks = [0, probDomainMax * 0.5, probDomainMax].map((v) => v);
  const plTicks = [plMin + plPad * 0.3, 0, plMax - plPad * 0.3];

  return (
    <div>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="payoff-chart"
        role="img"
        aria-label="Probability distribution combined with payoff diagram"
      >
        {/* Probability bars */}
        {barRanges.map((b) => {
          const x1 = xScale(b.lo);
          const x2 = xScale(b.hi);
          const y = yProbScale(b.probability);
          return (
            <rect
              key={b.label}
              x={x1 + 0.5}
              y={y}
              width={Math.max(x2 - x1 - 1, 0.5)}
              height={MARGIN.top + PLOT_H - y}
              className={b.is_profit ? "dist-bar-profit" : "dist-bar-loss"}
            />
          );
        })}

        {/* Breakeven / current-price reference lines */}
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

        {/* Zero P/L line (right axis) */}
        <line
          x1={MARGIN.left}
          x2={WIDTH - MARGIN.right}
          y1={yPlScale(0)}
          y2={yPlScale(0)}
          className="chart-zero-line"
        />

        {/* Payoff line (right axis) */}
        <polyline points={linePoints} className="chart-line" />

        {/* Left axis: probability */}
        <line x1={MARGIN.left} x2={MARGIN.left} y1={MARGIN.top} y2={HEIGHT - MARGIN.bottom} className="chart-axis-line" />
        {probTicks.map((t) => (
          <text key={t} x={MARGIN.left - 8} y={yProbScale(t) + 3} textAnchor="end" className="chart-axis-sublabel">
            {fmtPercent(t, 0)}
          </text>
        ))}
        <text x={4} y={MARGIN.top - 6} className="chart-axis-label">
          Probability
        </text>

        {/* Right axis: P/L */}
        <line
          x1={WIDTH - MARGIN.right}
          x2={WIDTH - MARGIN.right}
          y1={MARGIN.top}
          y2={HEIGHT - MARGIN.bottom}
          className="chart-axis-line"
        />
        {plTicks.map((t) => (
          <text key={t} x={WIDTH - MARGIN.right + 8} y={yPlScale(t) + 3} textAnchor="start" className="chart-axis-sublabel">
            {fmtUsd(t, 0)}
          </text>
        ))}
        <text x={WIDTH - MARGIN.right - 4} y={MARGIN.top - 6} textAnchor="end" className="chart-axis-label">
          P/L per contract
        </text>

        <text x={WIDTH / 2} y={HEIGHT - 6} textAnchor="middle" className="chart-axis-label">
          Underlying Price at Expiration
        </text>
      </svg>
      <div className="chart-legend">
        <span className="legend-item">
          <span className="legend-swatch dist-bar-profit" /> Probability, profitable price
        </span>
        <span className="legend-item">
          <span className="legend-swatch dist-bar-loss" /> Probability, losing price
        </span>
        <span className="legend-item">
          <span className="legend-swatch legend-line" /> Payoff (right axis)
        </span>
      </div>
    </div>
  );
}
