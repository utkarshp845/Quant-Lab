import { useMemo, useState } from "react";
import { ApiError, importCsv } from "../api/client";
import type { CsvImportResponse, NormalizedOption } from "../types/csvImport";
import type { BearPutSpreadFormState } from "../types/form";
import { OptionChainTable } from "./OptionChainTable";
import { SpreadBuilderPreview } from "./SpreadBuilderPreview";
import { SpreadScanner } from "./SpreadScanner";
import { optionToFormState } from "../utils/optionToFormState";
import { fmtUsd } from "../utils/format";

interface CsvImportWorkflowProps {
  onApply: (formState: BearPutSpreadFormState) => void;
}

/**
 * Upload CSV -> select expiration -> select long put -> select short
 * put -> Analyze Spread. The end result of this whole workflow is a
 * single call to `onApply` with a normal BearPutSpreadFormState --
 * from that point on, CSV-derived and manually-typed inputs are
 * indistinguishable to the rest of the app (see CalculatorPage.tsx).
 *
 * As soon as both legs are picked, a SpreadBuilderPreview appears
 * instantly (client-side, no network call) -- the "Spread Builder":
 * you're not entering a trade into a calculator, you're constructing
 * an instrument from the chain in front of you. "Analyze Spread"
 * still exists for the full transparent, formula-by-formula analysis.
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
  const [browseMode, setBrowseMode] = useState<"chain" | "scanner">("chain");

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

  // The scanner spans every expiration for the symbol, not just the
  // one picked above -- a bear put spread's legs must share an
  // expiration, but which expiration is exactly what a scan should be
  // free to search across.
  const contractsForSymbol = useMemo(() => {
    if (!importResult || !selectedSymbol) return [];
    return importResult.contracts.filter((c) => c.symbol === selectedSymbol);
  }, [importResult, selectedSymbol]);

  // Underlying price and DTE are the same across every row in one
  // symbol+expiration group, so the first row's values are the
  // group's values -- used for the "MCL $82.00 30 DTE" context header.
  const contextInfo = contractsForExpiration[0] ?? null;

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

          {selectedSymbol && (
            <div className="input-mode-toggle" role="tablist" aria-label="Browse mode">
              <button
                type="button"
                role="tab"
                aria-selected={browseMode === "chain"}
                className={browseMode === "chain" ? "mode-tab mode-tab-active" : "mode-tab"}
                onClick={() => setBrowseMode("chain")}
              >
                Browse Chain
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={browseMode === "scanner"}
                className={browseMode === "scanner" ? "mode-tab mode-tab-active" : "mode-tab"}
                onClick={() => setBrowseMode("scanner")}
              >
                Scan Combinations
              </button>
            </div>
          )}

          {browseMode === "chain" && contextInfo && (
            <div className="chain-context-header">
              <span className="chain-context-symbol">{contextInfo.symbol}</span>
              <span className="chain-context-price">{fmtUsd(contextInfo.underlying_price)}</span>
              <span className="chain-context-dte">{contextInfo.dte} DTE</span>
            </div>
          )}

          {browseMode === "chain" && contractsForExpiration.length > 0 && (
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
                {selectedLong && selectedShort && strikeOrderValid && (
                  <SpreadBuilderPreview long={selectedLong} short={selectedShort} />
                )}

                {selectedLong && selectedShort && !strikeOrderValid && (
                  <div className="error-banner">
                    Invalid strike ordering: the long put strike (${selectedLong.strike}) must be greater
                    than the short put strike (${selectedShort.strike}). Pick a higher strike for the long
                    put, or a lower strike for the short put.
                  </div>
                )}

                {!(selectedLong && selectedShort) && (
                  <p className="disclaimer-note">
                    {selectedLong
                      ? "Now click SELL on a lower strike to complete the spread."
                      : selectedShort
                        ? "Now click BUY on a higher strike to complete the spread."
                        : "Click BUY on one strike and SELL on a lower strike to build a bear put spread."}
                  </p>
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

          {browseMode === "scanner" && contractsForSymbol.length > 0 && (
            <SpreadScanner contracts={contractsForSymbol} onApply={onApply} />
          )}
        </>
      )}
    </div>
  );
}
