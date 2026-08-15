"""API route for CSV options-chain import.

This route does exactly one thing: turn an uploaded CSV into a list of
NormalizedOption contracts, via CSVProvider (app.providers.csv_provider)
-- the first implementation of the MarketDataProvider interface
(app.providers.base). It does NOT run any bear-put-spread math -- once
the user picks a long and short put from the imported chain, the
frontend builds a normal BearPutSpreadRequest from those two contracts
and posts it to the existing /api/bear-put-spread endpoint, so the
analysis is guaranteed to be identical to typing the same numbers in
by hand. See CalculatorPage.tsx / CsvImportWorkflow.tsx.

Why this route still returns CsvImportResponse rather than the
provider's NormalizedChainResult directly: this endpoint's contract is
"upload a CSV, get back exactly what was in it" -- detected_columns and
column_mapping are meaningful to a human who just uploaded a file and
want to see the API's response stay stable. A future live-provider
route (e.g. GET /api/market-data/{symbol}) would return
NormalizedChainResult as-is, since there's no upload to describe.
"""

from fastapi import APIRouter, HTTPException, UploadFile

from app.models.option_chain import CsvImportResponse
from app.providers.csv_provider import CsvFormatError
from app.providers.registry import get_provider

router = APIRouter()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB is generous for a CSV option chain


@router.post("/csv-import", response_model=CsvImportResponse)
async def import_csv(file: UploadFile) -> CsvImportResponse:
    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Please upload a .csv file.")

    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=422, detail="CSV file is too large (limit: 10 MB).")
    if not raw_bytes:
        raise HTTPException(status_code=422, detail="The uploaded file is empty.")

    try:
        text = raw_bytes.decode("utf-8-sig")  # tolerate a BOM from Excel/Windows exports
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Could not read the file as UTF-8 text.") from exc

    provider = get_provider("csv")
    try:
        result = provider.get_chain(csv_text=text)
    except CsvFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if result.imported_rows == 0:
        raise HTTPException(
            status_code=422,
            detail="No valid option rows were found in this file. "
            f"{len(result.row_errors)} row(s) had errors -- check that bid/ask/delta/strike/IV are all present and valid.",
        )

    return CsvImportResponse(
        detected_columns=result.metadata["detected_columns"],
        column_mapping=result.metadata["column_mapping"],
        contracts=result.contracts,
        symbols=result.symbols,
        expirations_by_symbol=result.expirations_by_symbol,
        row_errors=result.row_errors,
        total_rows=result.total_rows,
        imported_rows=result.imported_rows,
    )
