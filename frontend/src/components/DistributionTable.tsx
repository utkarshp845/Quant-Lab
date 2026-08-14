import type { DistributionBucket } from "../types/bearPutSpread";
import { fmtPercent, fmtSigned } from "../utils/format";

interface DistributionTableProps {
  buckets: DistributionBucket[];
}

export function DistributionTable({ buckets }: DistributionTableProps) {
  return (
    <div className="table-wrap">
      <table className="payoff-table">
        <thead>
          <tr>
            <th>Price Range</th>
            <th>Probability</th>
            <th>P/L per Contract</th>
          </tr>
        </thead>
        <tbody>
          {buckets.map((b) => (
            <tr key={b.label} className={b.is_profit ? "row-profit" : "row-loss"}>
              <td>{b.label}</td>
              <td>{fmtPercent(b.probability, 2)}</td>
              <td>{fmtSigned(b.pl_per_contract)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
