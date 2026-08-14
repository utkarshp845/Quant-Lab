import type { PayoffScenario } from "../types/bearPutSpread";
import { fmtSigned, fmtUsd } from "../utils/format";

interface PayoffTableProps {
  scenarios: PayoffScenario[];
}

export function PayoffTable({ scenarios }: PayoffTableProps) {
  return (
    <section className="section">
      <h2 className="section-title">8. Payoff Table</h2>
      <p className="section-subtitle">
        Scenario prices at expiration, including both strikes, breakeven, and the current
        underlying price.
      </p>
      <div className="table-wrap">
        <table className="payoff-table">
          <thead>
            <tr>
              <th>Expiration Price</th>
              <th>Spread Value</th>
              <th>P/L per Contract</th>
            </tr>
          </thead>
          <tbody>
            {scenarios.map((row) => (
              <tr key={row.expiration_price} className={row.is_profit ? "row-profit" : "row-loss"}>
                <td>
                  {fmtUsd(row.expiration_price)}
                  {row.label && <span className="row-label">{row.label}</span>}
                </td>
                <td>{fmtUsd(row.spread_value)}</td>
                <td>{fmtSigned(row.pl_per_contract)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
