import { useMemo, useState } from "react";
import {
  EMPTY_FILTERS,
  filterCombinations,
  generateSpreadCombinations,
  type ScannerFilters,
  type SpreadCombination,
} from "../utils/spreadCombinations";
import { optionToFormState } from "../utils/optionToFormState";
import type { NormalizedOption } from "../types/csvImport";
import type { BearPutSpreadFormState } from "../types/form";
import { fmtNumber, fmtUsd } from "../utils/format";

interface SpreadScannerProps {
  contracts: NormalizedOption[];
  onApply: (formState: BearPutSpreadFormState) => void;
}

type SortKey = "debit" | "maxLoss" | "maxProfit" | "breakeven" | "delta" | "dte";

function parseFilterInput(raw: string): number | null {
  if (raw.trim() === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

const SORTERS: Record<SortKey, (c: SpreadCombination) => number> = {
  debit: (c) => c.debitPerContract,
  maxLoss: (c) => c.maxLossPerContract,
  maxProfit: (c) => c.maxProfitPerContract,
  breakeven: (c) => c.breakeven,
  delta: (c) => c.netDelta,
  dte: (c) => c.dte,
};

/**
 * The "first tiny scanner": examines every possible long-put + short-put
 * combination within the already-uploaded CSV chain (not a market-wide
 * scan -- see README's Phase 5 roadmap for that). Every combination's
 * metrics are computed with the exact same client-side formula mirror
 * SpreadBuilderPreview uses; filtering and sorting are pure client-side
 * table operations over that already-computed list. This is the first
 * point in the app where the computer is doing something you could not
 * reasonably do by hand -- but it is still only searching data you
 * already loaded, and it never tells you which row to pick.
 */
export function SpreadScanner({ contracts, onApply }: SpreadScannerProps) {
  const [filterInputs, setFilterInputs] = useState({
    dteMin: "",
    dteMax: "",
    longDeltaMin: "",
    longDeltaMax: "",
    shortDeltaMin: "",
    shortDeltaMax: "",
    maxLossLimit: "",
  });
  const [sortKey, setSortKey] = useState<SortKey>("maxProfit");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const allCombinations = useMemo(() => generateSpreadCombinations(contracts), [contracts]);

  const filters: ScannerFilters = useMemo(
    () => ({
      ...EMPTY_FILTERS,
      dteMin: parseFilterInput(filterInputs.dteMin),
      dteMax: parseFilterInput(filterInputs.dteMax),
      longDeltaMin: parseFilterInput(filterInputs.longDeltaMin),
      longDeltaMax: parseFilterInput(filterInputs.longDeltaMax),
      shortDeltaMin: parseFilterInput(filterInputs.shortDeltaMin),
      shortDeltaMax: parseFilterInput(filterInputs.shortDeltaMax),
      maxLossLimit: parseFilterInput(filterInputs.maxLossLimit),
    }),
    [filterInputs],
  );

  const filtered = useMemo(() => filterCombinations(allCombinations, filters), [allCombinations, filters]);

  const sorted = useMemo(() => {
    const getValue = SORTERS[sortKey];
    const copy = [...filtered];
    copy.sort((a, b) => (getValue(a) - getValue(b)) * (sortDir === "asc" ? 1 : -1));
    return copy;
  }, [filtered, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const setFilter = (field: keyof typeof filterInputs, value: string) => {
    setFilterInputs((prev) => ({ ...prev, [field]: value }));
  };

  const sortIndicator = (key: SortKey) => (key === sortKey ? (sortDir === "asc" ? " ▲" : " ▼") : "");

  return (
    <div className="scanner">
      <p className="section-subtitle">
        Every possible long/short put combination from this chain, computed at once. Narrow the
        list with a hypothesis below, then click Analyze on any row for the full breakdown --
        this table never tells you which row to pick.
      </p>

      <div className="scanner-filters">
        <label className="field">
          <span className="field-label">DTE min</span>
          <input type="number" value={filterInputs.dteMin} onChange={(e) => setFilter("dteMin", e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">DTE max</span>
          <input type="number" value={filterInputs.dteMax} onChange={(e) => setFilter("dteMax", e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Long delta from</span>
          <input
            type="number"
            step="0.01"
            value={filterInputs.longDeltaMin}
            onChange={(e) => setFilter("longDeltaMin", e.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">Long delta to</span>
          <input
            type="number"
            step="0.01"
            value={filterInputs.longDeltaMax}
            onChange={(e) => setFilter("longDeltaMax", e.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">Short delta from</span>
          <input
            type="number"
            step="0.01"
            value={filterInputs.shortDeltaMin}
            onChange={(e) => setFilter("shortDeltaMin", e.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">Short delta to</span>
          <input
            type="number"
            step="0.01"
            value={filterInputs.shortDeltaMax}
            onChange={(e) => setFilter("shortDeltaMax", e.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">Max loss ≤ ($)</span>
          <input
            type="number"
            value={filterInputs.maxLossLimit}
            onChange={(e) => setFilter("maxLossLimit", e.target.value)}
          />
        </label>
        <button
          type="button"
          className="chain-select-btn"
          onClick={() =>
            setFilterInputs({
              dteMin: "",
              dteMax: "",
              longDeltaMin: "",
              longDeltaMax: "",
              shortDeltaMin: "",
              shortDeltaMax: "",
              maxLossLimit: "",
            })
          }
        >
          Clear filters
        </button>
      </div>

      <p className="disclaimer-note">
        {sorted.length} of {allCombinations.length} combinations match your filters.
      </p>

      <div className="table-wrap">
        <table className="payoff-table scanner-table">
          <thead>
            <tr>
              <th>Long</th>
              <th>Short</th>
              <th onClick={() => toggleSort("dte")} className="scanner-sortable">
                DTE{sortIndicator("dte")}
              </th>
              <th onClick={() => toggleSort("debit")} className="scanner-sortable">
                Debit{sortIndicator("debit")}
              </th>
              <th onClick={() => toggleSort("maxLoss")} className="scanner-sortable">
                Max Loss{sortIndicator("maxLoss")}
              </th>
              <th onClick={() => toggleSort("maxProfit")} className="scanner-sortable">
                Max Profit{sortIndicator("maxProfit")}
              </th>
              <th onClick={() => toggleSort("breakeven")} className="scanner-sortable">
                Breakeven{sortIndicator("breakeven")}
              </th>
              <th onClick={() => toggleSort("delta")} className="scanner-sortable">
                Delta{sortIndicator("delta")}
              </th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((c) => (
              <tr key={`${c.expiration}-${c.long.strike}-${c.short.strike}`}>
                <td>{c.long.strike}</td>
                <td>{c.short.strike}</td>
                <td>{c.dte}</td>
                <td>{fmtUsd(c.debitPerContract)}</td>
                <td className="loss-text">{fmtUsd(c.maxLossPerContract)}</td>
                <td className="profit-text">{fmtUsd(c.maxProfitPerContract)}</td>
                <td>{fmtUsd(c.breakeven)}</td>
                <td>{fmtNumber(c.netDelta)}</td>
                <td>
                  <button
                    type="button"
                    className="chain-select-btn"
                    onClick={() => onApply(optionToFormState(c.long, c.short))}
                  >
                    Analyze
                  </button>
                </td>
              </tr>
            ))}
            {sorted.length === 0 && (
              <tr>
                <td colSpan={9} className="chain-empty-row">
                  No combinations match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
