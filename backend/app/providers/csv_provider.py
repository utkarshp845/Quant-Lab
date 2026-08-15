"""CSVProvider -- the first (and, for now, only) MarketDataProvider.

This is intentionally a thin wrapper, not a reimplementation. All the
actual parsing/validation logic already lives in
app.ingestion.csv_normalizer (column-alias matching, per-row
validation, error reporting) and stays there unchanged -- this file's
only job is translating that module's dict-shaped result into the
provider interface's NormalizedChainResult, the same way
BearPutSpreadRequest.debit_per_share is a single formula fed different
inputs rather than duplicated per caller (see README section 8). No
parsing rule is duplicated here.
"""

from app.ingestion.csv_normalizer import CsvFormatError, parse_and_normalize_csv
from app.models.option_chain import CsvRowError, NormalizedOption
from app.providers.base import MarketDataProvider, NormalizedChainResult

__all__ = ["CSVProvider", "CsvFormatError"]


class CSVProvider(MarketDataProvider):
    """Normalizes an already-uploaded CSV export into a NormalizedChainResult.

    Push, not pull: the caller hands over the full CSV text up front
    (there's no "symbol" to ask this provider for -- it only knows
    about whatever was in the file). See base.py's docstring for why
    that's fine: the interface's input is allowed to vary by provider,
    only the output shape is fixed.
    """

    name = "csv"

    def get_chain(self, *, csv_text: str, today=None) -> NormalizedChainResult:
        """Raises CsvFormatError if the file as a whole can't be read
        (e.g. a required column is missing) -- unchanged from
        parse_and_normalize_csv's existing behavior, just re-exported
        here so callers don't need to reach into app.ingestion directly.
        """
        result = parse_and_normalize_csv(csv_text, today=today)

        return NormalizedChainResult(
            contracts=[NormalizedOption(**c) for c in result["contracts"]],
            symbols=result["symbols"],
            expirations_by_symbol=result["expirations_by_symbol"],
            row_errors=[CsvRowError(**e) for e in result["row_errors"]],
            total_rows=result["total_rows"],
            imported_rows=result["imported_rows"],
            source=self.name,
            metadata={
                "detected_columns": result["detected_columns"],
                "column_mapping": result["column_mapping"],
            },
        )
