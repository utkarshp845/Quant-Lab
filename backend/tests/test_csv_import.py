"""Unit tests for the CSV ingestion layer (app/ingestion/).

Covers: column-alias detection ("Thinkorswim-style data maps
correctly"), a valid file importing correctly, and invalid/missing
fields producing row-level or whole-file errors instead of silently
substituted values.
"""

from datetime import date, timedelta

import pytest

from app.ingestion.column_mapping import detect_column_mapping, missing_required_columns
from app.ingestion.csv_normalizer import CsvFormatError, parse_and_normalize_csv
from app.ingestion.value_parsing import (
    parse_expiration_date,
    parse_float,
    parse_int,
    parse_iv,
    parse_option_type,
)

FIXED_TODAY = date(2026, 8, 19)


def _csv_with_expiration(expiration_str: str, rows_extra: str = "") -> str:
    header = "Symbol,Underlying Price,Exp Date,Strike,Type,Bid,Ask,Last,Volume,Open Int,Impl Vol,Delta,Gamma,Theta,Vega"
    long_row = f"MCL,82.00,{expiration_str},85,PUT,5.00,5.10,5.05,120,340,44.00%,-0.58,0.05,-0.03,0.12"
    short_row = f"MCL,82.00,{expiration_str},77,PUT,0.71,0.85,0.78,95,210,42.00%,-0.29,0.04,-0.02,0.08"
    return "\n".join([header, long_row, short_row, rows_extra]).strip() + "\n"


class TestColumnMapping:
    def test_detects_thinkorswim_style_headers(self):
        headers = ["Symbol", "Underlying Price", "Exp Date", "Strike", "Type", "Bid", "Ask", "Delta", "Impl Vol"]
        mapping = detect_column_mapping(headers)
        assert mapping["symbol"] == "Symbol"
        assert mapping["underlying_price"] == "Underlying Price"
        assert mapping["expiration"] == "Exp Date"
        assert mapping["option_type"] == "Type"
        assert mapping["implied_volatility"] == "Impl Vol"

    def test_case_and_whitespace_insensitive(self):
        headers = ["  symbol ", "IMPL VOL", "delta", "  Strike"]
        mapping = detect_column_mapping(headers)
        assert mapping["symbol"] == "  symbol "
        assert mapping["implied_volatility"] == "IMPL VOL"

    def test_recognizes_alternate_aliases(self):
        headers = ["Ticker", "Spot", "Expiry", "Strike Price", "P/C", "Bid", "Ask", "Delta", "IV"]
        mapping = detect_column_mapping(headers)
        assert mapping["symbol"] == "Ticker"
        assert mapping["underlying_price"] == "Spot"
        assert mapping["expiration"] == "Expiry"
        assert mapping["option_type"] == "P/C"
        assert mapping["implied_volatility"] == "IV"

    def test_missing_required_columns_lists_them(self):
        headers = ["Symbol", "Strike", "Bid", "Ask"]  # no delta, IV, expiration, etc.
        mapping = detect_column_mapping(headers)
        missing = missing_required_columns(mapping)
        assert "delta" in missing
        assert "implied_volatility" in missing
        assert "expiration" in missing
        assert "underlying_price" in missing
        assert "option_type" in missing


class TestValueParsing:
    def test_parse_float_handles_currency_and_commas(self):
        assert parse_float("$5.10") == pytest.approx(5.10)
        assert parse_float("1,234.50") == pytest.approx(1234.50)

    def test_parse_float_blank_tokens_are_none(self):
        for token in ["", "N/A", "n/a", "--", "-", None]:
            assert parse_float(token) is None

    def test_parse_int_truncates(self):
        assert parse_int("120") == 120
        assert parse_int("") is None

    def test_parse_iv_with_percent_sign(self):
        assert parse_iv("44.00%") == pytest.approx(0.44)

    def test_parse_iv_without_percent_sign_above_threshold(self):
        assert parse_iv("44.00") == pytest.approx(0.44)

    def test_parse_iv_already_decimal(self):
        assert parse_iv("0.44") == pytest.approx(0.44)

    def test_parse_option_type_variants(self):
        assert parse_option_type("PUT") == "put"
        assert parse_option_type("p") == "put"
        assert parse_option_type("Call") == "call"
        assert parse_option_type("C") == "call"
        assert parse_option_type("X") is None

    def test_parse_expiration_common_formats(self):
        assert parse_expiration_date("2026-09-18") == date(2026, 9, 18)
        assert parse_expiration_date("09/18/2026") == date(2026, 9, 18)
        assert parse_expiration_date("18-Sep-2026") == date(2026, 9, 18)

    def test_parse_expiration_strips_thinkorswim_dte_suffix(self):
        # Thinkorswim-style: "18 SEP 26 (35)" bundles the DTE in parens.
        assert parse_expiration_date("18 Sep 26 (35)") == date(2026, 9, 18)

    def test_parse_expiration_unparseable_is_none(self):
        assert parse_expiration_date("not a date") is None


class TestParseAndNormalizeCsv:
    def test_valid_csv_imports_correctly(self):
        expiration = (FIXED_TODAY + timedelta(days=30)).strftime("%m/%d/%Y")
        csv_text = _csv_with_expiration(expiration)
        result = parse_and_normalize_csv(csv_text, today=FIXED_TODAY)

        assert result["total_rows"] == 2
        assert result["imported_rows"] == 2
        assert result["row_errors"] == []
        assert result["symbols"] == ["MCL"]

        long_contract = next(c for c in result["contracts"] if c["strike"] == 85)
        assert long_contract["option_type"] == "put"
        assert long_contract["bid"] == pytest.approx(5.00)
        assert long_contract["ask"] == pytest.approx(5.10)
        assert long_contract["delta"] == pytest.approx(-0.58)
        assert long_contract["implied_volatility"] == pytest.approx(0.44)
        assert long_contract["dte"] == 30

    def test_thinkorswim_style_data_maps_correctly(self):
        # Different header names/order than the "canonical" fixture,
        # to prove the mapping layer -- not column position -- drives
        # normalization.
        expiration = (FIXED_TODAY + timedelta(days=14)).strftime("%m/%d/%Y")
        csv_text = (
            "Ticker,Strike Price,P/C,Bid,Ask,Delta,IV,Expiry,Spot\n"
            f"XYZ,50,PUT,1.00,1.10,-0.30,35.00%,{expiration},60.00\n"
        )
        result = parse_and_normalize_csv(csv_text, today=FIXED_TODAY)
        assert result["imported_rows"] == 1
        c = result["contracts"][0]
        assert c["symbol"] == "XYZ"
        assert c["strike"] == 50
        assert c["option_type"] == "put"
        assert c["implied_volatility"] == pytest.approx(0.35)
        assert c["underlying_price"] == pytest.approx(60.00)
        assert c["dte"] == 14

    def test_missing_required_column_raises_csv_format_error(self):
        csv_text = "Symbol,Strike,Bid,Ask\nMCL,85,5.00,5.10\n"
        with pytest.raises(CsvFormatError, match="Missing required column"):
            parse_and_normalize_csv(csv_text, today=FIXED_TODAY)

    def test_empty_file_raises_csv_format_error(self):
        with pytest.raises(CsvFormatError):
            parse_and_normalize_csv("", today=FIXED_TODAY)

    def test_row_with_missing_bid_is_skipped_with_error_not_substituted(self):
        expiration = (FIXED_TODAY + timedelta(days=30)).strftime("%m/%d/%Y")
        header = "Symbol,Underlying Price,Exp Date,Strike,Type,Bid,Ask,Delta,Impl Vol"
        good_row = f"MCL,82.00,{expiration},85,PUT,5.00,5.10,-0.58,44.00%"
        bad_row = f"MCL,82.00,{expiration},77,PUT,,0.85,-0.29,42.00%"  # blank bid
        csv_text = f"{header}\n{good_row}\n{bad_row}\n"

        result = parse_and_normalize_csv(csv_text, today=FIXED_TODAY)
        assert result["total_rows"] == 2
        assert result["imported_rows"] == 1  # bad row skipped, not defaulted to 0 or anything else
        assert len(result["row_errors"]) == 1
        assert result["row_errors"][0]["row_number"] == 3  # header is row 1
        assert "bid" in result["row_errors"][0]["message"].lower()

    def test_row_with_ask_below_bid_is_flagged(self):
        expiration = (FIXED_TODAY + timedelta(days=30)).strftime("%m/%d/%Y")
        header = "Symbol,Underlying Price,Exp Date,Strike,Type,Bid,Ask,Delta,Impl Vol"
        bad_row = f"MCL,82.00,{expiration},85,PUT,5.10,5.00,-0.58,44.00%"  # ask < bid
        csv_text = f"{header}\n{bad_row}\n"

        result = parse_and_normalize_csv(csv_text, today=FIXED_TODAY)
        assert result["imported_rows"] == 0
        assert "ask" in result["row_errors"][0]["message"].lower()

    def test_row_with_out_of_range_put_delta_is_flagged(self):
        expiration = (FIXED_TODAY + timedelta(days=30)).strftime("%m/%d/%Y")
        header = "Symbol,Underlying Price,Exp Date,Strike,Type,Bid,Ask,Delta,Impl Vol"
        bad_row = f"MCL,82.00,{expiration},85,PUT,5.00,5.10,0.58,44.00%"  # positive delta for a put
        csv_text = f"{header}\n{bad_row}\n"

        result = parse_and_normalize_csv(csv_text, today=FIXED_TODAY)
        assert result["imported_rows"] == 0
        assert "delta" in result["row_errors"][0]["message"].lower()

    def test_row_with_unparseable_expiration_is_flagged(self):
        header = "Symbol,Underlying Price,Exp Date,Strike,Type,Bid,Ask,Delta,Impl Vol"
        bad_row = "MCL,82.00,not-a-date,85,PUT,5.00,5.10,-0.58,44.00%"
        csv_text = f"{header}\n{bad_row}\n"

        result = parse_and_normalize_csv(csv_text, today=FIXED_TODAY)
        assert result["imported_rows"] == 0
        assert "expiration" in result["row_errors"][0]["message"].lower()

    def test_calls_and_puts_both_normalize_with_type_specific_delta_ranges(self):
        expiration = (FIXED_TODAY + timedelta(days=30)).strftime("%m/%d/%Y")
        header = "Symbol,Underlying Price,Exp Date,Strike,Type,Bid,Ask,Delta,Impl Vol"
        put_row = f"MCL,82.00,{expiration},85,PUT,5.00,5.10,-0.58,44.00%"
        call_row = f"MCL,82.00,{expiration},85,CALL,1.90,2.00,0.42,44.00%"
        csv_text = f"{header}\n{put_row}\n{call_row}\n"

        result = parse_and_normalize_csv(csv_text, today=FIXED_TODAY)
        assert result["imported_rows"] == 2
        types = {c["option_type"] for c in result["contracts"]}
        assert types == {"put", "call"}

    def test_optional_fields_default_to_none_when_columns_absent(self):
        expiration = (FIXED_TODAY + timedelta(days=30)).strftime("%m/%d/%Y")
        header = "Symbol,Underlying Price,Exp Date,Strike,Type,Bid,Ask,Delta,Impl Vol"
        row = f"MCL,82.00,{expiration},85,PUT,5.00,5.10,-0.58,44.00%"
        csv_text = f"{header}\n{row}\n"

        result = parse_and_normalize_csv(csv_text, today=FIXED_TODAY)
        c = result["contracts"][0]
        assert c["volume"] is None
        assert c["open_interest"] is None
        assert c["gamma"] is None
        assert c["theta"] is None
        assert c["vega"] is None
        assert c["last"] is None

    def test_expirations_grouped_by_symbol(self):
        header = "Symbol,Underlying Price,Exp Date,Strike,Type,Bid,Ask,Delta,Impl Vol"
        exp1 = (FIXED_TODAY + timedelta(days=14)).strftime("%m/%d/%Y")
        exp2 = (FIXED_TODAY + timedelta(days=30)).strftime("%m/%d/%Y")
        rows = [
            f"MCL,82.00,{exp1},85,PUT,5.00,5.10,-0.58,44.00%",
            f"MCL,82.00,{exp2},85,PUT,5.20,5.30,-0.60,45.00%",
            f"XYZ,100.00,{exp1},95,PUT,2.00,2.10,-0.40,30.00%",
        ]
        csv_text = header + "\n" + "\n".join(rows) + "\n"

        result = parse_and_normalize_csv(csv_text, today=FIXED_TODAY)
        assert set(result["symbols"]) == {"MCL", "XYZ"}
        assert len(result["expirations_by_symbol"]["MCL"]) == 2
        assert len(result["expirations_by_symbol"]["XYZ"]) == 1
