import { useMemo, useState } from "react";
import { ApiError, importCsv } from "../api/client";
import type { CsvImportResponse, NormalizedOption } from "../types/csvImport";
import type { BearPutSpreadFormState } from "../types/form";
import { OptionChainTable } from "./OptionChainTable";
import { fmtPercent, fmtUsd } from "../utils/format";

interface CsvImportWorkflowProps {
  onApply: (formState: BearPutSpreadFormState) => void;
}

function optionToFormState(long: NormalizedOption, short: NormalizedOption): BearPutSpreadFormState {
  return {
    underlying: {
      symbol: long.symbol,
      price: String(long.underlying_price),
      dte: String(long.dte),
    },
    longPut: {
      strike: String(long.strike),
      bid: String(long.bid),
      ask: String(long.ask),
      delta: String(long.delta),
      ivPercent: String(long.implied_volatility * 100),
    },
    shortPut: {
      strike: String(short.strike),
      bid: String(short.bid),
      ask: String(short.ask),
      delta: String(short.delta),
      ivPercent: String(short.implied_volatility * 100),
    },
  };
}

/**
 * Upload CSV -> select expiration -> select long put -> select short
 * put -> Analyze Spread. The end result of this whole workflow is a
 * single call to `onApply` with a normal BearPutSpreadFormState --
 * from that point on, CSV-derived and manually-typed inputs are
 * indistinguishable to the rest of the app (see CalculatorPage.tsx).
 */
export function CsvImportWorkflow({ onApply }: CsvImportWorkflowProps) {
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [importResult, setImportResult] = useState<CsvImportResponse | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);

  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [selectedExpiration, setSelectedExpiration] = useState<string | null>(null);
  const [optionType, setOptionType] = useState<"put" | "call">("put");
  const [selectedLong, setSelectedLong] = useState<NormalizedOption | null>(null);
  const [selectedShort, setSelectedShort] = useState<NormalizedOption | null>(null);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    setUploading(true);
    setUploadError(null);
    setImportResult(null);
    setSelectedSymbol(null);
    setSelectedExpiration(null);
    setSelectedLong(null);
    setSelectedShort(null);
    try {
      const result = await importCsv(file);
      setImportResult(result);
      const firstSymbol = result.symbols[0] ?? null;
      setSelectedSymbol(firstSymbol);
      setSelectedExpiration(firstSymbol ? result.expirations_by_symbol[firstSymbol]?.[0] ?? null : null);
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : "Could not reach the backend to import this file.");
    } finally {
      setUploading(false);
    }
    // Allow re-selecting the same file later.
    e.target.value = "";
  };

  const contractsForExpiration = useMemo(() => {
    if (!importResult || !selectedSymbol || !selectedExpiration) return [];
    return importResult.contracts.filter(
      (c) => c.symbol === selectedSymbol && c.expiration === selectedExpiration,
    );
  }, [importResult, selectedSymbol, selectedExpiration]);

  const strikeOrderValid =
    selectedLong && selectedShort ? selectedLong.strike > selectedShort.strike : null;

  const canAnalyze = !!(selectedLong && selectedShort && strikeOrderValid);

  return (
    <div className="csv-import-workflow">
      <div className="csv-upload-row">
        <label className="csv-upload-btn">
          {uploading ? "Uploading…" : "Choose CSV File"}
          <input type="file" accept=".csv" onChange={handleFileChange} disabled={uploading} hidden />
        </label>
        {fileName && <span className="csv-file-name">{fileName}</span>}
      </div>

      {uploadError && <div className="error-banner">{uploadError}</div>}

      {importResult && (
        <>
          <details className="distribution-table-details">
            <summary>
              Detected {importResult.detected_columns.length} column(s), imported{" "}
              {importResult.imported_rows} of {importResult.total_rows} row(s)
              {importResult.row_errors.length > 0 && ` (${importResult.row_errors.length} skipped)`}
            </summary>
            <div className="csv-detail-block">
              <div className="csv-detail-label">Detected columns</div>
              <div className="csv-columns-list">{importResult.detected_columns.join(", ")}</div>
            </div>
            <div className="csv-detail-block">
              <div className="csv-detail-label">Column mapping used</div>
              <ul className="csv-mapping-list">
                {Object.entries(importResult.column_mapping).map(([field, column]) => (
                  <li key={field}>
                    <code>{field}</code> ← "{column}"
                  </li>
                ))}
              </ul>
            </div>
            {importResult.row_errors.length > 0 && (
              <div className="csv-detail-block">
                <div className="csv-detail-label">Skipped rows</div>
                <ul className="csv-mapping-list">
                  {importResult.row_errors.map((e) => (
                    <li key={e.row_number}>
                      Row {e.row_number}: {e.message}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </details>

          <div className="csv-selectors-row">
            {importResult.symbols.length > 1 && (
              <label className="field">
                <span className="field-label">Symbol</span>
                <select
                  value={selectedSymbol ?? ""}
                  onChange={(e) => {
                    setSelectedSymbol(e.target.value);
                    setSelectedExpiration(importResult.expirations_by_symbol[e.target.value]?.[0] ?? null);
                    setSelectedLong(null);
                    setSelectedShort(null);
                  }}
                >
                  {importResult.symbols.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {selectedSymbol && (
              <label className="field">
                <span className="field-label">Expiration</span>
                <select
                  value={selectedExpiration ?? ""}
                  onChange={(e) => {
                    setSelectedExpiration(e.target.value);
                    setSelectedLong(null);
                    setSelectedShort(null);
                  }}
                >
                  {(importResult.expirations_by_symbol[selectedSymbol] ?? []).map((exp) => (
                    <option key={exp} value={exp}>
                      {exp}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>

          {contractsForExpiration.length > 0 && (
            <>
              <OptionChainTable
                contracts={contractsForExpiration}
                optionType={optionType}
                onOptionTypeChange={setOptionType}
                selectedLong={selectedLong}
                selectedShort={selectedShort}
                onSetLong={setSelectedLong}
                onSetShort={setSelectedShort}
              />

              <div className="csv-selection-summary">
                <div className="structure-row">
                  <div className="structure-leg accent-buy">
                    <span className="badge badge-buy">BUY</span>
                    <div>{selectedLong ? `Long Put, Strike $${selectedLong.strike}` : "No long put selected"}</div>
                    {selectedLong && (
                      <div className="structure-price">
                        Bid {fmtUsd(selectedLong.bid)} / Ask {fmtUsd(selectedLong.ask)} / Delta{" "}
                        {selectedLong.delta.toFixed(2)} / IV {fmtPercent(selectedLong.implied_volatility, 1)}
                      </div>
                    )}
                  </div>
                  <div className="structure-leg accent-sell">
                    <span className="badge badge-sell">SELL</span>
                    <div>{selectedShort ? `Short Put, Strike $${selectedShort.strike}` : "No short put selected"}</div>
                    {selectedShort && (
                      <div className="structure-price">
                        Bid {fmtUsd(selectedShort.bid)} / Ask {fmtUsd(selectedShort.ask)} / Delta{" "}
                        {selectedShort.delta.toFixed(2)} / IV {fmtPercent(selectedShort.implied_volatility, 1)}
                      </div>
                    )}
                  </div>
                </div>

                {selectedLong && selectedShort && !strikeOrderValid && (
                  <div className="error-banner">
                    Invalid strike ordering: the long put strike (${selectedLong.strike}) must be greater
                    than the short put strike (${selectedShort.strike}). Pick a higher strike for the long
                    put, or a lower strike for the short put.
                  </div>
                )}

                <button
                  type="button"
                  className="mc-run-btn"
                  disabled={!canAnalyze}
                  onClick={() => {
                    if (!selectedLong || !selectedShort) return;
                    onApply(optionToFormState(selectedLong, selectedShort));
                  }}
                >
                  Analyze Spread
                </button>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
