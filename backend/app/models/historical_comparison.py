"""Response shapes for POST /market-data/history/compare (v0.1.16) --
the "upload a CSV of bars, diff it against a live provider for the same
symbol/timeframe/period" test described in this feature's spec as the
single most important thing to verify. See
app/api/historical_comparison.py for the actual comparison logic; this
file is purely the HTTP response shape it returns.

Deliberately NOT a verdict -- there is no `is_match: bool` or `passed:
bool` field anywhere here. This app's rule (see app/ingestion/
value_parsing.py and README's "never silently substitute a value")
extends to this feature too: discrepancies are surfaced with numbers a
human can look at (row counts, timestamps, OHLC deltas, volume deltas),
never auto-resolved, auto-tolerance-checked, or labeled "OK"/"FAIL" by
this code. Deciding whether a given discrepancy matters (a rounding
difference vs. a genuine data-quality problem, a known IEX-vs-
consolidated-volume gap vs. something worth investigating) is the
reader's job, same as scripts/cross_validate_providers.py already does
for provider-vs-provider comparisons.
"""

from datetime import date, datetime

from pydantic import BaseModel


class CsvBarRowError(BaseModel):
    """One row of the uploaded CSV that couldn't be parsed into a bar --
    same shape/spirit as CsvRowError (app/models/option_chain.py), kept
    separate because it's a different parser (app/ingestion/ohlcv_csv.py)
    with different possible failure reasons."""

    row_number: int
    message: str


class HistoricalComparisonRow(BaseModel):
    """One timestamp's worth of comparison. Three cases, told apart by
    `in_csv`/`in_api` rather than three different row shapes, so a
    single table can render all of them: both True (the interesting
    case -- values are compared and *_diff is populated), only one True
    (a timestamp one side has and the other doesn't -- *_diff fields
    are None, since there's nothing to diff), never both False (such a
    row would never be constructed).
    """

    timestamp: datetime
    in_csv: bool
    in_api: bool

    csv_open: float | None = None
    csv_high: float | None = None
    csv_low: float | None = None
    csv_close: float | None = None
    csv_volume: int | None = None

    api_open: float | None = None
    api_high: float | None = None
    api_low: float | None = None
    api_close: float | None = None
    api_volume: int | None = None

    # None when the row isn't present on both sides (nothing to diff);
    # otherwise api_value - csv_value, signed (not absolute), so a
    # reader can see which side is higher, not just the magnitude.
    open_diff: float | None = None
    high_diff: float | None = None
    low_diff: float | None = None
    close_diff: float | None = None
    volume_diff: int | None = None


class HistoricalComparisonResponse(BaseModel):
    """The full comparison report. Echoes the request (symbol/provider/
    timeframe/start/end) so the response is self-describing, then the
    headline counts the spec asked for explicitly (row count
    difference, matched/unmatched timestamps, max OHLC/volume
    divergence) ahead of the row-by-row detail, so a reader gets the
    "is anything obviously wrong" signal before scrolling through rows.
    """

    symbol: str
    provider: str
    timeframe: str
    start: date
    end: date

    csv_row_count: int  # bars successfully parsed from the CSV (post symbol/date-range filtering)
    api_row_count: int
    row_count_diff: int  # csv_row_count - api_row_count; 0 means equal counts, not necessarily identical timestamps

    matched_count: int  # timestamps present on both sides
    csv_only_count: int  # timestamps only the CSV has
    api_only_count: int  # timestamps only the API has
    rows_with_value_diffs: int  # of the matched timestamps, how many have any O/H/L/C/volume difference at all

    max_open_diff: float | None = None
    max_high_diff: float | None = None
    max_low_diff: float | None = None
    max_close_diff: float | None = None
    max_volume_diff: int | None = None

    csv_total_rows: int  # every data row in the uploaded file, before parsing/filtering
    csv_row_errors: list[CsvBarRowError]

    truncated: bool  # true if `rows` below was capped -- see the route for the cap and why
    rows: list[HistoricalComparisonRow]

    notes: list[str]  # caveats worth reading before interpreting the numbers above (e.g. the UTC-assumption on naive CSV timestamps)
