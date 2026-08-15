"""Tests for the MarketDataProvider interface (app/providers/).

Covers three things the rest of the test suite doesn't:
  1. CSVProvider actually satisfies the MarketDataProvider contract
     and returns a NormalizedChainResult with the fields every future
     provider must also fill in correctly.
  2. The registry resolves a known provider name and rejects an
     unknown one, instead of silently returning nothing.
  3. CSVProvider's output is equivalent to calling the underlying
     ingestion module directly -- proving this is a wrapper, not a
     reimplementation (see csv_provider.py's docstring).
"""

from datetime import date

import pytest

from app.ingestion.csv_normalizer import CsvFormatError, parse_and_normalize_csv
from app.providers.base import MarketDataProvider, NormalizedChainResult
from app.providers.csv_provider import CSVProvider
from app.providers.registry import PROVIDERS, get_provider

FIXED_TODAY = date(2026, 8, 19)

VALID_CSV = (
    "Symbol,Underlying Price,Exp Date,Strike,Type,Bid,Ask,Last,Volume,Open Int,Impl Vol,Delta,Gamma,Theta,Vega\n"
    "MCL,82.00,09/18/2026,85,PUT,5.00,5.10,5.05,120,340,44.00%,-0.58,0.05,-0.03,0.12\n"
    "MCL,82.00,09/18/2026,77,PUT,0.71,0.85,0.78,95,210,42.00%,-0.29,0.04,-0.02,0.08\n"
)

MISSING_COLUMN_CSV = "Symbol,Strike,Type,Bid,Ask,Delta\nMCL,85,PUT,5.00,5.10,-0.58\n"


class TestCSVProviderIsAMarketDataProvider:
    def test_is_an_instance_of_the_interface(self):
        assert isinstance(CSVProvider(), MarketDataProvider)

    def test_has_a_name(self):
        assert CSVProvider.name == "csv"

    def test_cannot_instantiate_the_abstract_base_directly(self):
        with pytest.raises(TypeError):
            MarketDataProvider()  # abstract -- get_chain has no implementation


class TestCSVProviderGetChain:
    def test_returns_a_normalized_chain_result(self):
        result = CSVProvider().get_chain(csv_text=VALID_CSV, today=FIXED_TODAY)

        assert isinstance(result, NormalizedChainResult)
        assert result.source == "csv"
        assert result.imported_rows == 2
        assert result.total_rows == 2
        assert result.symbols == ["MCL"]
        assert len(result.contracts) == 2
        assert result.row_errors == []

    def test_metadata_carries_csv_specific_detail(self):
        result = CSVProvider().get_chain(csv_text=VALID_CSV, today=FIXED_TODAY)

        assert "detected_columns" in result.metadata
        assert "column_mapping" in result.metadata
        assert result.metadata["column_mapping"]["symbol"] == "Symbol"

    def test_missing_required_column_raises_csv_format_error(self):
        with pytest.raises(CsvFormatError):
            CSVProvider().get_chain(csv_text=MISSING_COLUMN_CSV, today=FIXED_TODAY)

    def test_output_matches_calling_the_ingestion_module_directly(self):
        """CSVProvider must not reimplement or diverge from
        parse_and_normalize_csv -- it's a wrapper (see csv_provider.py
        docstring), so the two must agree on every contract field."""
        direct = parse_and_normalize_csv(VALID_CSV, today=FIXED_TODAY)
        via_provider = CSVProvider().get_chain(csv_text=VALID_CSV, today=FIXED_TODAY)

        assert via_provider.imported_rows == direct["imported_rows"]
        assert via_provider.symbols == direct["symbols"]
        assert via_provider.expirations_by_symbol == direct["expirations_by_symbol"]
        assert [c.model_dump() for c in via_provider.contracts] == direct["contracts"]


class TestProviderRegistry:
    def test_csv_is_registered(self):
        assert "csv" in PROVIDERS

    def test_get_provider_returns_a_csv_provider_instance(self):
        provider = get_provider("csv")
        assert isinstance(provider, CSVProvider)

    def test_get_provider_rejects_an_unknown_name(self):
        with pytest.raises(ValueError, match="Unknown market data provider"):
            get_provider("schwab")
