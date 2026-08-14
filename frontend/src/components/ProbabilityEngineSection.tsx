import type { BearPutSpreadResponse } from "../types/bearPutSpread";
import { DistributionChart } from "./DistributionChart";
import { DistributionTable } from "./DistributionTable";
import { FormulaBox } from "./FormulaBox";
import { Tooltip } from "./Tooltip";
import { fmtPercent, fmtSigned, fmtUsd } from "../utils/format";

interface ProbabilityEngineSectionProps {
  data: BearPutSpreadResponse;
  underlyingPrice: number;
}

/**
 * Phase 2: turns the single "probability below breakeven" number into
 * a full discretized probability distribution, weighted against the
 * payoff to produce an Expected Value. Same underlying assumption as
 * the Probability section above (simplified normal distribution
 * centered on the current price, standard deviation = expected move)
 * -- just applied bucket by bucket instead of at one point.
 */
export function ProbabilityEngineSection({ data, underlyingPrice }: ProbabilityEngineSectionProps) {
  const { distribution, risk_reward: rr } = data;
  const midBucket = distribution.buckets[Math.floor(distribution.buckets.length / 2)];

  return (
    <section className="section">
      <h2 className="section-title">10. Probability Engine</h2>
      <p className="section-subtitle">
        Instead of one probability number, this splits the range of possible expiration prices
        into buckets, assigns each a probability (area under the normal curve for that price
        range) and a P/L, and combines them into an Expected Value.
      </p>

      <div className="metric-block">
        <div className="metric-heading">
          Bucket Probability{" "}
          <Tooltip text="The exact area under the assumed normal curve between a bucket's two price edges -- not a density estimate at a single point." />
        </div>
        <FormulaBox
          formula={distribution.formula_bucket_probability}
          substitution={
            midBucket
              ? `Normal CDF(z for ${fmtUsd(midBucket.price_high ?? midBucket.representative_price)}) - Normal CDF(z for ${fmtUsd(
                  midBucket.price_low ?? midBucket.representative_price,
                )})`
              : undefined
          }
          result={midBucket ? fmtPercent(midBucket.probability, 2) : undefined}
        />
        <p className="disclaimer-note">
          The distribution is built from {distribution.buckets.length} price buckets spanning
          roughly ±3 standard deviations around the current price, plus two open-ended buckets
          for the extreme tails -- so the probabilities always sum to exactly{" "}
          {fmtPercent(distribution.total_probability, 0)}, not an approximation that happens to
          land near 100%.
        </p>
      </div>

      <DistributionChart
        buckets={distribution.buckets}
        payoffLine={data.payoff_chart_points}
        breakeven={rr.breakeven}
        underlyingPrice={underlyingPrice}
      />

      <div className="metric-block probability-callout">
        <div className="metric-heading">
          Expected Value{" "}
          <Tooltip text="The probability-weighted average P/L across every bucket. This is NOT a guaranteed return and is not the same as a real trading edge -- see the note below." />
        </div>
        <div className={`metric-big ${distribution.expected_value_per_contract >= 0 ? "profit-text" : "loss-text"}`}>
          {fmtSigned(distribution.expected_value_per_contract)}
        </div>
        <FormulaBox
          formula={distribution.formula_expected_value}
          result={`${fmtSigned(distribution.expected_value_per_share)} per share = ${fmtSigned(
            distribution.expected_value_per_contract,
          )} per contract`}
        />
        <div className="disclaimer-banner">
          <strong>This Expected Value is not a trading edge.</strong> It is computed under the
          same simplified assumption as the probability section above: prices are normally
          distributed around the <em>current</em> price (zero drift) with a standard deviation
          equal to the expected move from average IV. Real markets price options under a
          risk-neutral distribution, which typically has volatility skew, fat tails, and a drift
          term this simplified model ignores entirely. A positive or negative number here mostly
          reflects the shape of this simplified curve relative to the strikes -- not a genuine
          statistical advantage.
        </div>
      </div>

      <details className="distribution-table-details">
        <summary>Show full distribution table ({distribution.buckets.length} buckets)</summary>
        <DistributionTable buckets={distribution.buckets} />
      </details>
    </section>
  );
}
