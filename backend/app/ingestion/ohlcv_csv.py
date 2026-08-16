"""Parses raw CSV text into MarketBar-shaped dicts (symbol/timestamp/OHLCV).

Separate from csv_normalizer.py on purpose: that module's column
aliases and row-validation rules (strike, bid/ask, delta, IV) are
specific to an options-chain export and don't apply here -- a bar
export has an entirely different, much smaller set of columns. Reusing
csv_normalizer.py would mean either stretching its REQUIRED_FIELDS/
COLUMN_ALIASES to cover two unrelated shapes, or silently accepting
whichever fields happen to be optional in one but required in the
other. Same "own alias list per data shape" reasoning column_mapping.py
already documents for CSV formats.

This exists for exactly one caller today: the historical-data CSV
comparison route (app/api/historical_comparison.py) -- see its
docstring for why "compare a CSV export against a live provider" is
this app's most important test of the whole historical-data feature.
Nothing here fabricates a value for a missing/unparseable field; an
unparseable row is skipped and recorded in row_errors, exactly like
csv_normalizer.py's convention.
"""

import csv
import io
from datetime import datetime, timezone

from . import value_parsing as vp

# Case-insensitive, whitespace-tolerant aliases for each normalized
# field -- same matching convention as column_mapping.py, but this
# module's own list: a bar export's column names have nothing in
# common with an option chain's.
COLUMN_ALIASES: dict[str, list[str]] = {
    "symbol": ["symbol", "ticker", "root symbol"],
    "timestamp": ["timestamp", "datetime", "date/time", "date", "time", "bar time"],
    "open": ["open", "o"],
    "high": ["high", "h"],
    "low": ["low", "l"],
    "close": ["close", "c"],
    "volume": ["volume", "vol", "v"],
}

# "symbol" is deliberately NOT required at the column level: a
# single-symbol bar export (the common case) often has no symbol
# column at all -- parse_ohlcv_csv's `default_symbol` fills that in
# per-row when the column is absent. Every other field is required to
# build a MarketBar at all.
REQUIRED_FIELDS: list[str] = ["timestamp", "open", "high", "low", "close", "volume"]

# Tried in order; first one that parses wins. Covers a bare date (a
# daily-bar export) and common date+time combinations (an intraday
# export) -- both ISO and US-style, since a broker/charting tool export
# could plausibly use either.
_TIMESTAMP_FORMATS = [
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
]


class OhlcvCsvFormatError(ValueError):
    """The file as a whole cannot be parsed (e.g. a required column is missing)."""


def _normalize_header(header: str) -> str:
    return " ".join(header.strip().lower().replace("%", "").split())


def detect_column_mapping(headers: list[str]) -> dict[str, str]:
    normalized_to_actual = {_normalize_header(h): h for h in headers}
    mapping: dict[str, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized_to_actual:
                mapping[field] = normalized_to_actual[alias]
                break
    return mapping


def missing_required_columns(mapping: dict[str, str]) -> list[str]:
    return [field for field in REQUIRED_FIELDS if field not in mapping]


def parse_timestamp_utc(raw: str | None) -> datetime | None:
    """Returns a UTC-aware datetime, or None if missing/unparseable.

    Assumption, stated explicitly rather than left implicit: a
    timestamp with no UTC offset in the source file (the common case
    for a plain "2026-08-01 09:30:00"-style export) is treated as
    already being UTC. This is a documented assumption, not a
    guarantee -- if the CSV's timestamps are actually in exchange-local
    time (e.g. America/New_York), every comparison row will show a
    constant, fixed-hours timestamp offset from the provider's bars.
    That's exactly the kind of thing this feature's comparison report
    is built to surface, not silently correct for -- see
    app/api/historical_comparison.py's module docstring.
    """
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    for fmt in _TIMESTAMP_FORMATS:
        try:
            parsed = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def parse_ohlcv_csv(csv_text: str, *, default_symbol: str) -> dict:
    """Returns {"detected_columns", "column_mapping", "bars", "symbols",
    "row_errors", "total_rows", "imported_rows"}. Each bar in `bars` is a
    dict with symbol/timestamp/open/high/low/close/volume/provider="csv"
    -- already shaped like HistoricalBar (app/models/market_data.py), so
    the comparison route can build one directly with **bar rather than
    a second translation step.

    `default_symbol` fills in the symbol for every row when the file has
    no symbol column at all (the common case for a single-symbol bar
    export) -- rows are never left symbol-less.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    headers = reader.fieldnames or []
    if not headers:
        raise OhlcvCsvFormatError("The CSV file has no header row, or is empty.")

    mapping = detect_column_mapping(headers)
    missing = missing_required_columns(mapping)
    if missing:
        raise OhlcvCsvFormatError(
            "Missing required column(s): " + ", ".join(missing) + ". Detected columns: " + ", ".join(headers)
        )

    bars: list[dict] = []
    row_errors: list[dict] = []
    total_rows = 0

    for row_number, raw_row in enumerate(reader, start=2):  # header row is line 1
        total_rows += 1
        result = _normalize_row(raw_row, mapping, default_symbol)
        if isinstance(result, str):
            row_errors.append({"row_number": row_number, "message": result})
        else:
            bars.append(result)

    symbols = sorted({b["symbol"] for b in bars})

    return {
        "detected_columns": headers,
        "column_mapping": mapping,
        "bars": bars,
        "symbols": symbols,
        "row_errors": row_errors,
        "total_rows": total_rows,
        "imported_rows": len(bars),
    }


def _normalize_row(raw_row: dict, mapping: dict[str, str], default_symbol: str) -> dict | str:
    def get(field: str) -> str | None:
        col = mapping.get(field)
        return raw_row.get(col) if col is not None else None

    symbol_col = get("symbol")
    symbol = (symbol_col or "").strip().upper() or default_symbol.upper()

    ts = parse_timestamp_utc(get("timestamp"))
    if ts is None:
        return f"Missing or unparseable timestamp (raw value: {get('timestamp')!r})."

    open_ = vp.parse_float(get("open"))
    if open_ is None:
        return "Missing or unparseable open."

    high = vp.parse_float(get("high"))
    if high is None:
        return "Missing or unparseable high."

    low = vp.parse_float(get("low"))
    if low is None:
        return "Missing or unparseable low."

    close = vp.parse_float(get("close"))
    if close is None:
        return "Missing or unparseable close."

    volume = vp.parse_int(get("volume"))
    if volume is None:
        return "Missing or unparseable volume."

    return {
        "symbol": symbol,
        "timestamp": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "provider": "csv",
    }
