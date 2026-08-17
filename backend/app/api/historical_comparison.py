"""API route for the CSV-vs-provider historical-data comparison test --
POST /market-data/history/compare (v0.1.16).

This is the "most important test" called for in this feature's spec:
take a CSV export of OHLCV bars (e.g. an existing MCL-style broker
export, or any other CSV of bars a user has on hand) for the same
symbol/timeframe/period as a live provider request, and surface exactly
where they agree and disagree -- row counts, which timestamps are
missing on which side, and the O/H/L/C/volume delta for every matched
timestamp. See app/models/historical_comparison.py's docstring for why
this deliberately never renders a pass/fail verdict.

One important caveat about this repo's *own* existing CSV fixture
(backend/tests/fixtures/sample_thinkorswim_chain.csv): it's an
options-CHAIN snapshot (strike/bid/ask/delta/IV, underlying symbol
"MCL", one point in time) -- not an OHLCV bar time series for TSLA/NVDA,
so it cannot literally be fed through this endpoint for a same-symbol
comparison. This route is generic: it accepts any CSV of OHLCV bars
(symbol/timestamp/open/high/low/close/volume columns, aliases handled
by app/ingestion/ohlcv_csv.py) for TSLA or NVDA. See this feature's
final report for how to exercise this with real data.

Reuses app.api.historical_data.fetch_normalized_bars for the API side
of the comparison -- the exact same validated path GET
/market-data/{symbol}/history uses -- rather than a second, parallel
way to ask a provider for bars. Exception mapping for that call is
therefore identical to that route's (see its docstring).

v0.1.19: this is also the one production entry point where a raw CSV
upload is available before parsing touches it, so it's where the raw-
storage stage persists the original CSV text -- see
app/storage/raw_ingestion_repository.py.
"""

from datetime import date, datetime, timezone

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.api.historical_data import fetch_normalized_bars
from app.ingestion.ohlcv_csv import OhlcvCsvFormatError, parse_ohlcv_csv
from app.models.historical_comparison import (
    CsvBarRowError,
    HistoricalComparisonResponse,
    HistoricalComparisonRow,
)
from app.storage.raw_ingestion_repository import persist_raw_ingestion_safely

router = APIRouter()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB -- matches /api/csv-import's limit

# Safety cap on how many comparison rows the response includes -- an
# intraday timeframe over a wide date range can produce thousands of
# matched timestamps, and there is no value in shipping all of them
# over the wire for a human to read. `truncated` in the response says
# whether this cap was hit; narrowing the date range is the way to see
# rows past it, not raising this constant indefinitely.
MAX_COMPARISON_ROWS = 2000

_NAIVE_TIMESTAMP_NOTE = (
    "CSV timestamps with no UTC offset in the source file are assumed to already be UTC. "
    "If this CSV's timestamps are actually in exchange-local time, every matched row will "
    "show a constant, fixed-hours timestamp offset from the provider's bars -- narrow rows "
    "to a lightly-traded time of day to check for this rather than assuming it away."
)


def _in_range(ts: datetime, start: date, end: date) -> bool:
    d = ts.astimezone(timezone.utc).date()
    return start <= d <= end


def _diff(api_value: float | None, csv_value: float | None) -> float | None:
    """api - csv, signed -- None if either side is missing (nothing to diff)."""
    if api_value is None or csv_value is None:
        return None
    return api_value - csv_value


def _wider(current: float | None, candidate: float | None) -> float | None:
    """Returns whichever of `current`/`candidate` has the larger magnitude,
    keeping the sign (so a reader can see which side ran higher at the
    most extreme divergence, not just how big the gap was)."""
    if candidate is None:
        return current
    if current is None or abs(candidate) > abs(current):
        return candidate
    return current


@router.post("/market-data/history/compare", response_model=HistoricalComparisonResponse)
async def compare_historical_csv(
    file: UploadFile = File(...),
    symbol: str = Form(...),
    start: date = Form(...),
    end: date = Form(...),
    timeframe: str = Form("1d"),
    provider: str = Form(...),
) -> HistoricalComparisonResponse:
    symbol = symbol.upper()

    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Please upload a .csv file.")
    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=422, detail="CSV file is too large (limit: 10 MB).")
    if not raw_bytes:
        raise HTTPException(status_code=422, detail="The uploaded file is empty.")
    try:
        csv_text = raw_bytes.decode("utf-8-sig")  # tolerate a BOM from Excel/Windows exports
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Could not read the file as UTF-8 text.") from exc

    # v0.1.19: the ORIGINAL uploaded CSV text, persisted before parsing
    # touches it -- see app/storage/raw_ingestion_repository.py. Placed
    # here specifically (before parse_ohlcv_csv, not after) so the raw
    # content is preserved even when parsing itself fails below -- see
    # that module's docstring for why "what did the file literally
    # contain when this failed" must not depend on parsing succeeding.
    # persist_raw_ingestion_safely() never raises, so a storage problem
    # here can't turn a working comparison request into a failure.
    persist_raw_ingestion_safely(
        source="csv",
        symbol=symbol,
        timeframe=timeframe,
        source_start=start,
        source_end=end,
        raw_payload=csv_text,
        content_type="csv",
        metadata={"filename": file.filename},
    )

    try:
        csv_result = parse_ohlcv_csv(csv_text, default_symbol=symbol)
    except OhlcvCsvFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Same validation + provider call GET /market-data/{symbol}/history
    # uses -- see historical_data.py's docstring for the exception
    # mapping this mirrors.
    try:
        api_bars = fetch_normalized_bars(symbol=symbol, start=start, end=end, timeframe=timeframe, provider=provider)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message.startswith("Unknown market data provider") else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail=f"{provider} rate limit exceeded: {exc.response.text or exc.response.reason_phrase}",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail=f"{provider} API request failed: {exc.response.status_code} {exc.response.reason_phrase}",
        ) from exc

    csv_bars = [
        b
        for b in csv_result["bars"]
        if b["symbol"] == symbol and _in_range(b["timestamp"], start, end)
    ]

    csv_by_ts = {b["timestamp"]: b for b in csv_bars}
    api_by_ts = {b.timestamp: b for b in api_bars}
    all_timestamps = sorted(set(csv_by_ts) | set(api_by_ts))

    rows: list[HistoricalComparisonRow] = []
    rows_with_value_diffs = 0
    max_open_diff = max_high_diff = max_low_diff = max_close_diff = None
    max_volume_diff: int | None = None

    for ts in all_timestamps[:MAX_COMPARISON_ROWS]:
        c = csv_by_ts.get(ts)
        a = api_by_ts.get(ts)

        open_diff = _diff(a.open if a else None, c["open"] if c else None)
        high_diff = _diff(a.high if a else None, c["high"] if c else None)
        low_diff = _diff(a.low if a else None, c["low"] if c else None)
        close_diff = _diff(a.close if a else None, c["close"] if c else None)
        volume_diff = _diff(a.volume if a else None, c["volume"] if c else None)
        volume_diff_int = int(volume_diff) if volume_diff is not None else None

        if any(d not in (None, 0) for d in (open_diff, high_diff, low_diff, close_diff, volume_diff)):
            rows_with_value_diffs += 1

        max_open_diff = _wider(max_open_diff, open_diff)
        max_high_diff = _wider(max_high_diff, high_diff)
        max_low_diff = _wider(max_low_diff, low_diff)
        max_close_diff = _wider(max_close_diff, close_diff)
        max_volume_diff = _wider(max_volume_diff, volume_diff_int)

        rows.append(
            HistoricalComparisonRow(
                timestamp=ts,
                in_csv=c is not None,
                in_api=a is not None,
                csv_open=c["open"] if c else None,
                csv_high=c["high"] if c else None,
                csv_low=c["low"] if c else None,
                csv_close=c["close"] if c else None,
                csv_volume=c["volume"] if c else None,
                api_open=a.open if a else None,
                api_high=a.high if a else None,
                api_low=a.low if a else None,
                api_close=a.close if a else None,
                api_volume=a.volume if a else None,
                open_diff=open_diff,
                high_diff=high_diff,
                low_diff=low_diff,
                close_diff=close_diff,
                volume_diff=volume_diff_int,
            )
        )

    matched_count = len(set(csv_by_ts) & set(api_by_ts))
    csv_only_count = len(set(csv_by_ts) - set(api_by_ts))
    api_only_count = len(set(api_by_ts) - set(csv_by_ts))

    notes = [_NAIVE_TIMESTAMP_NOTE]
    other_csv_symbols = sorted(s for s in csv_result["symbols"] if s != symbol)
    if other_csv_symbols:
        notes.append(
            f"The uploaded CSV also contained row(s) for {', '.join(other_csv_symbols)} -- "
            f"excluded from this comparison, which is scoped to {symbol} only."
        )

    return HistoricalComparisonResponse(
        symbol=symbol,
        provider=provider,
        timeframe=timeframe,
        start=start,
        end=end,
        csv_row_count=len(csv_bars),
        api_row_count=len(api_bars),
        row_count_diff=len(csv_bars) - len(api_bars),
        matched_count=matched_count,
        csv_only_count=csv_only_count,
        api_only_count=api_only_count,
        rows_with_value_diffs=rows_with_value_diffs,
        max_open_diff=max_open_diff,
        max_high_diff=max_high_diff,
        max_low_diff=max_low_diff,
        max_close_diff=max_close_diff,
        max_volume_diff=max_volume_diff,
        csv_total_rows=csv_result["total_rows"],
        csv_row_errors=[CsvBarRowError(**e) for e in csv_result["row_errors"]],
        truncated=len(all_timestamps) > MAX_COMPARISON_ROWS,
        rows=rows,
        notes=notes,
    )
