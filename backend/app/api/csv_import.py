"""API route for CSV options-chain import.

This route does exactly one thing: turn an uploaded CSV into a list of
NormalizedOption contracts (app.ingestion.csv_normalizer does the
actual work). It does NOT run any bear-put-spread math -- once the
user picks a long and short put from the imported chain, the frontend
builds a normal BearPutSpreadRequest from those two contracts and
posts it to the existing /api/bear-put-spread endpoint, so the
analysis is guaranteed to be identical to typing the same numbers in
by hand. See CalculatorPage.tsx / CsvImportWorkflow.tsx.
"""

from fastapi import APIRouter, HTTPException, UploadFile

from app.ingestion.csv_normalizer import CsvFormatError, parse_and_normalize_csv
from app.models.option_chain import CsvImportResponse

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

    try:
        result = parse_and_normalize_csv(text)
    except CsvFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if result["imported_rows"] == 0:
        raise HTTPException(
            status_code=422,
            detail="No valid option rows were found in this file. "
            f"{len(result['row_errors'])} row(s) had errors -- check that bid/ask/delta/strike/IV are all present and valid.",
        )

    return CsvImportResponse(**result)
