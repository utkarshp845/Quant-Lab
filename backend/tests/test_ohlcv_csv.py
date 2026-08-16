"""Tests for app/ingestion/ohlcv_csv.py -- the OHLCV bar CSV parser
backing the historical-data comparison endpoint (v0.1.16)."""

from datetime import datetime, timezone

import pytest

from app.ingestion.ohlcv_csv import OhlcvCsvFormatError, parse_ohlcv_csv, parse_timestamp_utc


class TestParseTimestampUtc:
    def test_iso_date_only(self):
        assert parse_timestamp_utc("2026-08-10") == datetime(2026, 8, 10, tzinfo=timezone.utc)

    def test_iso_datetime_with_z(self):
        assert parse_timestamp_utc("2026-08-10T13:30:00Z") == datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc)

    def test_space_separated_datetime(self):
        assert parse_timestamp_utc("2026-08-10 13:30:00") == datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc)

    def test_us_style_date(self):
        assert parse_timestamp_utc("08/10/2026") == datetime(2026, 8, 10, tzinfo=timezone.utc)

    def test_a_timestamp_with_an_explicit_offset_is_preserved_not_reassumed_utc(self):
        parsed = parse_timestamp_utc("2026-08-10T13:30:00+05:00")
        assert parsed is not None
        assert parsed.utcoffset().total_seconds() == 5 * 3600

    def test_none_and_empty_and_garbage_all_return_none(self):
        assert parse_timestamp_utc(None) is None
        assert parse_timestamp_utc("") is None
        assert parse_timestamp_utc("not a date") is None


class TestParseOhlcvCsv:
    def test_parses_a_minimal_daily_export(self):
        csv_text = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-08-10,245.10,248.75,243.20,247.55,98400000\n"
            "2026-08-11,247.60,250.00,246.00,249.30,87200000\n"
        )
        result = parse_ohlcv_csv(csv_text, default_symbol="TSLA")

        assert result["imported_rows"] == 2
        assert result["total_rows"] == 2
        assert result["row_errors"] == []
        assert result["symbols"] == ["TSLA"]
        bar = result["bars"][0]
        assert bar["symbol"] == "TSLA"
        assert bar["timestamp"] == datetime(2026, 8, 10, tzinfo=timezone.utc)
        assert bar["open"] == 245.10
        assert bar["volume"] == 98_400_000
        assert bar["provider"] == "csv"

    def test_recognizes_a_symbol_column_when_present(self):
        csv_text = "Symbol,Date,Open,High,Low,Close,Volume\nNVDA,2026-08-10,118.40,120.10,117.90,119.85,210000000\n"
        result = parse_ohlcv_csv(csv_text, default_symbol="TSLA")

        assert result["symbols"] == ["NVDA"]
        assert result["bars"][0]["symbol"] == "NVDA"

    def test_column_aliases_are_case_insensitive_and_flexible(self):
        csv_text = "timestamp,o,h,l,c,vol\n2026-08-10 09:30:00,1,2,0.5,1.5,100\n"
        result = parse_ohlcv_csv(csv_text, default_symbol="TSLA")

        assert result["imported_rows"] == 1
        assert result["bars"][0]["open"] == 1.0
        assert result["bars"][0]["volume"] == 100

    def test_missing_required_column_raises_a_whole_file_error(self):
        csv_text = "Date,Open,High,Low,Close\n2026-08-10,245.10,248.75,243.20,247.55\n"  # no Volume column

        with pytest.raises(OhlcvCsvFormatError, match="volume"):
            parse_ohlcv_csv(csv_text, default_symbol="TSLA")

    def test_empty_file_raises_a_whole_file_error(self):
        with pytest.raises(OhlcvCsvFormatError):
            parse_ohlcv_csv("", default_symbol="TSLA")

    def test_a_row_with_an_unparseable_value_is_skipped_and_reported_not_fatal(self):
        csv_text = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-08-10,245.10,248.75,243.20,247.55,98400000\n"
            "not-a-date,247.60,250.00,246.00,249.30,87200000\n"
            "2026-08-12,,250.00,246.00,249.30,87200000\n"  # blank open
        )
        result = parse_ohlcv_csv(csv_text, default_symbol="TSLA")

        assert result["total_rows"] == 3
        assert result["imported_rows"] == 1
        assert len(result["row_errors"]) == 2
        assert result["row_errors"][0]["row_number"] == 3  # header is line 1
        assert "timestamp" in result["row_errors"][0]["message"].lower()
        assert result["row_errors"][1]["row_number"] == 4
        assert "open" in result["row_errors"][1]["message"].lower()

    def test_no_symbol_column_defaults_every_row_to_default_symbol(self):
        csv_text = "Date,Open,High,Low,Close,Volume\n2026-08-10,1,2,0.5,1.5,100\n"
        result = parse_ohlcv_csv(csv_text, default_symbol="nvda")  # lowercase on purpose

        assert result["bars"][0]["symbol"] == "NVDA"  # normalized to uppercase
