"""The normalized option-contract model, and CSV-import response shapes.

This model is deliberately kept separate from the CSV parser
(app/ingestion/) so that a future data source -- a different broker's
export format, or eventually a live API (see the README's Phase 4
roadmap) -- can produce the exact same shape and feed the exact same
analysis pipeline. Nothing downstream of this model needs to know
where a contract came from.
"""

from typing import Literal

from pydantic import BaseModel


class NormalizedOption(BaseModel):
    """One option contract, in a source-agnostic shape.

    Every field name and unit here matches the concepts already used
    by BearPutSpreadRequest (models/bear_put_spread.py) one-to-one, so
    turning a NormalizedOption into that request is a direct field
    mapping, not a translation.
    """

    symbol: str
    underlying_price: float
    expiration: str  # ISO date string, e.g. "2026-09-18"
    dte: int
    strike: float
    option_type: Literal["call", "put"]
    bid: float
    ask: float
    last: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    implied_volatility: float  # decimal, e.g. 0.44 for 44%
    delta: float
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None


class CsvRowError(BaseModel):
    row_number: int
    message: str


class CsvImportResponse(BaseModel):
    detected_columns: list[str]
    column_mapping: dict[str, str]
    contracts: list[NormalizedOption]
    symbols: list[str]
    expirations_by_symbol: dict[str, list[str]]
    row_errors: list[CsvRowError]
    total_rows: int
    imported_rows: int
