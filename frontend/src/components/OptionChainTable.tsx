import type { NormalizedOption } from "../types/csvImport";
import { fmtPercent, fmtUsd } from "../utils/format";

interface OptionChainTableProps {
  contracts: NormalizedOption[];
  optionType: "put" | "call";
  onOptionTypeChange: (type: "put" | "call") => void;
  selectedLong: NormalizedOption | null;
  selectedShort: NormalizedOption | null;
  onSetLong: (contract: NormalizedOption) => void;
  onSetShort: (contract: NormalizedOption) => void;
}

function contractKey(c: NormalizedOption): string {
  return `${c.symbol}-${c.expiration}-${c.option_type}-${c.strike}`;
}

/**
 * Displays one expiration's option chain with Call/Put filtering.
 * Selection ("Set Long" / "Set Short") is only available for puts --
 * v0.1.1 focuses on bear put spreads -- but calls remain visible so
 * the imported chain isn't silently hidden.
 */
export function OptionChainTable({
  contracts,
  optionType,
  onOptionTypeChange,
  selectedLong,
  selectedShort,
  onSetLong,
  onSetShort,
}: OptionChainTableProps) {
  const filtered = contracts
    .filter((c) => c.option_type === optionType)
    .sort((a, b) => a.strike - b.strike);

  return (
    <div>
      <div className="chain-type-toggle" role="group" aria-label="Call or Put">
        <button
          type="button"
          className={optionType === "put" ? "mc-count-btn mc-count-btn-active" : "mc-count-btn"}
          onClick={() => onOptionTypeChange("put")}
        >
          Puts
        </button>
        <button
          type="button"
          className={optionType === "call" ? "mc-count-btn mc-count-btn-active" : "mc-count-btn"}
          onClick={() => onOptionTypeChange("call")}
        >
          Calls
        </button>
      </div>

      <div className="table-wrap">
        <table className="payoff-table chain-table">
          <thead>
            <tr>
              <th>Strike</th>
              <th>Bid</th>
              <th>Ask</th>
              <th>Delta</th>
              <th>IV</th>
              <th>Volume</th>
              <th>Open Int.</th>
              <th>Select</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((c) => {
              const key = contractKey(c);
              const isLong = selectedLong && contractKey(selectedLong) === key;
              const isShort = selectedShort && contractKey(selectedShort) === key;
              return (
                <tr key={key} className={isLong ? "row-selected-long" : isShort ? "row-selected-short" : undefined}>
                  <td>{c.strike}</td>
                  <td>{fmtUsd(c.bid)}</td>
                  <td>{fmtUsd(c.ask)}</td>
                  <td>{c.delta.toFixed(2)}</td>
                  <td>{fmtPercent(c.implied_volatility, 1)}</td>
                  <td>{c.volume ?? "—"}</td>
                  <td>{c.open_interest ?? "—"}</td>
                  <td className="chain-select-cell">
                    <button
                      type="button"
                      className={`chain-select-btn ${isLong ? "chain-select-btn-active-buy" : ""}`}
                      disabled={optionType !== "put"}
                      onClick={() => onSetLong(c)}
                      title={optionType !== "put" ? "Only puts can be selected in v0.1.1" : "Buy this put (long leg)"}
                    >
                      {isLong ? "BUY ✓" : "BUY"}
                    </button>
                    <button
                      type="button"
                      className={`chain-select-btn ${isShort ? "chain-select-btn-active-sell" : ""}`}
                      disabled={optionType !== "put"}
                      onClick={() => onSetShort(c)}
                      title={optionType !== "put" ? "Only puts can be selected in v0.1.1" : "Sell this put (short leg)"}
                    >
                      {isShort ? "SELL ✓" : "SELL"}
                    </button>
                  </td>
                </tr>
              );
            })}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={8} className="chain-empty-row">
                  No {optionType}s in this expiration.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
