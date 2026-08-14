"""Parses raw CSV text into a list of NormalizedOption-shaped dicts.

This is the one place that turns "a spreadsheet full of strings" into
"data the rest of the app can trust". Two kinds of problems are
handled differently, on purpose (see spec item 7 / "never silently
substitute missing values"):

  - A REQUIRED COLUMN missing from the header row entirely is a
    whole-file problem -- we cannot proceed at all, so this raises
    CsvFormatError, which the API layer turns into a 422 naming
    exactly which column(s) are missing.
  - A single ROW with a missing/invalid value in an otherwise-present
    column is a row-level problem -- that one row is skipped and
    recorded in `row_errors` with a human-readable reason, but the
    rest of the file still imports normally.

Nothing here ever fabricates a value for a missing field.
"""

import csv
import io
from datetime import date

from . import value_parsing as vp
from .column_mapping import detect_column_mapping, missing_required_columns


class CsvFormatError(ValueError):
    """The file as a whole cannot be imported (e.g. a required column is missing)."""


def parse_and_normalize_csv(csv_text: str, today: date | None = None) -> dict:
    """Returns a dict matching CsvImportResponse's fields (see models/option_chain.py)."""
    today = today or date.today()

    reader = csv.DictReader(io.StringIO(csv_text))
    headers = reader.fieldnames or []
    if not headers:
        raise CsvFormatError("The CSV file has no header row, or is empty.")

    mapping = detect_column_mapping(headers)
    missing = missing_required_columns(mapping)
    if missing:
        raise CsvFormatError(
            "Missing required column(s): "
            + ", ".join(missing)
            + ". Detected columns: "
            + ", ".join(headers)
        )

    contracts: list[dict] = []
    row_errors: list[dict] = []
    total_rows = 0

    for row_number, raw_row in enumerate(reader, start=2):  # header row is line 1
        total_rows += 1
        result = _normalize_row(raw_row, mapping, today)
        if isinstance(result, str):
            row_errors.append({"row_number": row_number, "message": result})
        else:
            contracts.append(result)

    symbols = sorted({c["symbol"] for c in contracts})
    expirations_by_symbol_sets: dict[str, set[str]] = {}
    for c in contracts:
        expirations_by_symbol_sets.setdefault(c["symbol"], set()).add(c["expiration"])
    expirations_by_symbol = {s: sorted(exps) for s, exps in expirations_by_symbol_sets.items()}

    return {
        "detected_columns": headers,
        "column_mapping": mapping,
        "contracts": contracts,
        "symbols": symbols,
        "expirations_by_symbol": expirations_by_symbol,
        "row_errors": row_errors,
        "total_rows": total_rows,
        "imported_rows": len(contracts),
    }


def _normalize_row(raw_row: dict, mapping: dict[str, str], today: date) -> dict | str:
    """Returns a NormalizedOption-shaped dict, or an error message string."""

    def get(field: str) -> str | None:
        col = mapping.get(field)
        return raw_row.get(col) if col is not None else None

    symbol = (get("symbol") or "").strip()
    if not symbol:
        return "Missing symbol."

    underlying_price = vp.parse_float(get("underlying_price"))
    if underlying_price is None:
        return "Missing or unparseable underlying price."
    if underlying_price <= 0:
        return f"Underlying price must be greater than 0 (got {underlying_price})."

    expiration_date = vp.parse_expiration_date(get("expiration"))
    if expiration_date is None:
        return f"Missing or unparseable expiration date (raw value: {get('expiration')!r})."

    strike = vp.parse_float(get("strike"))
    if strike is None:
        return "Missing or unparseable strike."
    if strike <= 0:
        return f"Strike must be greater than 0 (got {strike})."

    option_type = vp.parse_option_type(get("option_type"))
    if option_type is None:
        return f"Missing or unrecognized option type (raw value: {get('option_type')!r})."

    bid = vp.parse_float(get("bid"))
    if bid is None:
        return "Missing or unparseable bid."
    if bid < 0:
        return f"Bid must be >= 0 (got {bid})."

    ask = vp.parse_float(get("ask"))
    if ask is None:
        return "Missing or unparseable ask."
    if ask < 0:
        return f"Ask must be >= 0 (got {ask})."
    if ask < bid:
        return f"Ask ({ask}) must be >= Bid ({bid})."

    delta = vp.parse_float(get("delta"))
    if delta is None:
        return "Missing or unparseable delta."
    if option_type == "put" and not (-1 <= delta <= 0):
        return f"Put delta must be between -1 and 0 (got {delta})."
    if option_type == "call" and not (0 <= delta <= 1):
        return f"Call delta must be between 0 and 1 (got {delta})."

    iv = vp.parse_iv(get("implied_volatility"))
    if iv is None:
        return "Missing or unparseable implied volatility."
    if iv < 0:
        return f"Implied volatility must be >= 0 (got {iv})."

    dte = (expiration_date - today).days

    return {
        "symbol": symbol,
        "underlying_price": underlying_price,
        "expiration": expiration_date.isoformat(),
        "dte": dte,
        "strike": strike,
        "option_type": option_type,
        "bid": bid,
        "ask": ask,
        "last": vp.parse_float(get("last")),
        "volume": vp.parse_int(get("volume")),
        "open_interest": vp.parse_int(get("open_interest")),
        "implied_volatility": iv,
        "delta": delta,
        "gamma": vp.parse_float(get("gamma")),
        "theta": vp.parse_float(get("theta")),
        "vega": vp.parse_float(get("vega")),
    }
