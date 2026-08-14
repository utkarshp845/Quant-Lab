import type { PayoffScenario } from "../types/bearPutSpread";
import { fmtSigned, fmtUsd } from "../utils/format";

interface PayoffChartProps {
  chartPoints: PayoffScenario[];
  breakeven: number;
  longStrike: number;
  shortStrike: number;
  underlyingPrice: number;
  maxLoss: number;
  maxProfit: number;
}

const WIDTH = 720;
const HEIGHT = 400;
const MARGIN = { top: 20, right: 24, bottom: 64, left: 64 };
const PLOT_W = WIDTH - MARGIN.left - MARGIN.right;
const PLOT_H = HEIGHT - MARGIN.top - MARGIN.bottom;

/**
 * Renders the bear put spread payoff diagram as a plain inline SVG
 * polyline. The underlying payoff function is piecewise linear with
 * exactly two breakpoints (the strikes) -- see
 * backend/app/calculations/payoff_scenarios.py -- so `chartPoints`
 * only needs 4 values to draw the exact shape; nothing here
 * interpolates or smooths anything.
 */
export function PayoffChart({
  chartPoints,
  breakeven,
  longStrike,
  shortStrike,
  underlyingPrice,
  maxLoss,
  maxProfit,
}: PayoffChartProps) {
  if (chartPoints.length === 0) return null;

  const prices = chartPoints.map((p) => p.expiration_price);
  const pls = chartPoints.map((p) => p.pl_per_contract);

  const xMin = Math.min(...prices);
  const xMax = Math.max(...prices);
  const yMinRaw = Math.min(...pls, 0);
  const yMaxRaw = Math.max(...pls, 0);
  const yPad = Math.max((yMaxRaw - yMinRaw) * 0.15, 10);
  const yMin = yMinRaw - yPad;
  const yMax = yMaxRaw + yPad;

  const xScale = (price: number) => MARGIN.left + ((price - xMin) / (xMax - xMin)) * PLOT_W;
  const yScale = (pl: number) => MARGIN.top + PLOT_H - ((pl - yMin) / (yMax - yMin)) * PLOT_H;

  const linePoints = chartPoints
    .map((p) => `${xScale(p.expiration_price)},${yScale(p.pl_per_contract)}`)
    .join(" ");

  const zeroY = yScale(0);

  const profitAreaPoints = [
    `${xScale(xMin)},${zeroY}`,
    ...chartPoints.map((p) => `${xScale(p.expiration_price)},${yScale(p.pl_per_contract)}`),
    `${xScale(xMax)},${zeroY}`,
  ].join(" ");

  const markers: { x: number; label: string; color: string }[] = [
    { x: shortStrike, label: "Short Strike", color: "var(--color-sell)" },
    { x: longStrike, label: "Long Strike", color: "var(--color-buy)" },
    { x: breakeven, label: "Breakeven", color: "var(--color-text)" },
    { x: underlyingPrice, label: "Current", color: "var(--color-accent)" },
  ];

  // Markers whose labels would land close together on the x-axis are
  // staggered onto a second row so the text doesn't overlap (e.g. when
  // breakeven and the current price are only a dollar or two apart).
  const sortedMarkers = [...markers].sort((a, b) => xScale(a.x) - xScale(b.x));
  let lastLabelX = -Infinity;
  let currentRow = 0;
  const positionedMarkers = sortedMarkers.map((m) => {
    const px = xScale(m.x);
    currentRow = px - lastLabelX < 62 ? (currentRow === 0 ? 1 : 0) : 0;
    lastLabelX = px;
    return { ...m, row: currentRow };
  });

  return (
    <section className="section">
      <h2 className="section-title">9. Payoff Chart</h2>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="payoff-chart" role="img" aria-label="Bear put spread payoff diagram">
        {/* Zero-profit line */}
        <line
          x1={MARGIN.left}
          x2={WIDTH - MARGIN.right}
          y1={zeroY}
          y2={zeroY}
          className="chart-zero-line"
        />

        {/* Shaded area between payoff line and zero line */}
        <polygon points={profitAreaPoints} className="chart-fill" />

        {/* Reference vertical lines */}
        {positionedMarkers.map((m) => (
          <g key={m.label}>
            <line
              x1={xScale(m.x)}
              x2={xScale(m.x)}
              y1={MARGIN.top}
              y2={HEIGHT - MARGIN.bottom}
              stroke={m.color}
              strokeDasharray="4 3"
              strokeWidth={1}
              opacity={0.6}
            />
            <text
              x={xScale(m.x)}
              y={HEIGHT - MARGIN.bottom + 16 + m.row * 28}
              textAnchor="middle"
              className="chart-axis-label"
            >
              {m.label}
            </text>
            <text
              x={xScale(m.x)}
              y={HEIGHT - MARGIN.bottom + 30 + m.row * 28}
              textAnchor="middle"
              className="chart-axis-sublabel"
            >
              {fmtUsd(m.x)}
            </text>
          </g>
        ))}

        {/* Payoff line */}
        <polyline points={linePoints} className="chart-line" />

        {/* Max loss / max profit horizontal reference labels */}
        <text x={MARGIN.left} y={yScale(maxLoss) - 6} className="chart-hint-label loss-text">
          Max Loss {fmtSigned(maxLoss)}
        </text>
        <text x={MARGIN.left} y={yScale(maxProfit) - 6} className="chart-hint-label profit-text">
          Max Profit {fmtSigned(maxProfit)}
        </text>

        {/* Y axis */}
        <line
          x1={MARGIN.left}
          x2={MARGIN.left}
          y1={MARGIN.top}
          y2={HEIGHT - MARGIN.bottom}
          className="chart-axis-line"
        />
        <text x={12} y={MARGIN.top + 10} className="chart-axis-label">
          P/L
        </text>
        <text x={WIDTH - MARGIN.right} y={HEIGHT - 6} textAnchor="end" className="chart-axis-label">
          Underlying Price at Expiration
        </text>
      </svg>
    </section>
  );
}
