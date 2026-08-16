"""Tests for app/storage/historical_bar_repository.py (v0.1.17) --
the only module that writes SQL for historical bars. Every test uses an
explicit db_path pointing at a pytest tmp_path file, never the real
config.get_database_path() default, so these never touch or depend on
any developer's real local database.
"""

from datetime import date, datetime, timezone

from app.models.market_data import HistoricalBar
from app.storage.historical_bar_repository import get_bars, save_bars


def _bar(
    symbol="TSLA",
    timestamp="2026-08-10T04:00:00Z",
    provider="alpaca",
    timeframe="1d",
    open_=245.10,
    high=248.75,
    low=243.20,
    close=247.55,
    volume=98_400_000,
) -> HistoricalBar:
    return HistoricalBar(
        symbol=symbol,
        timestamp=timestamp,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        provider=provider,
        timeframe=timeframe,
    )


class TestSaveBars:
    def test_successful_insertion(self, tmp_path):
        db_path = tmp_path / "bars.db"
        result = save_bars([_bar()], db_path=db_path)

        assert result.total == 1
        assert result.inserted == 1
        assert result.skipped_duplicates == 0

    def test_empty_list_is_a_no_op_not_an_error(self, tmp_path):
        db_path = tmp_path / "bars.db"
        result = save_bars([], db_path=db_path)

        assert result == type(result)(total=0, inserted=0, skipped_duplicates=0)

    def test_duplicate_bars_are_not_inserted_twice(self, tmp_path):
        """The natural identity of a bar: same provider+symbol+timeframe
        +timestamp. Saving the exact same bar twice must not create a
        second row -- this is the uniqueness constraint's whole job."""
        db_path = tmp_path / "bars.db"
        bar = _bar()

        first = save_bars([bar], db_path=db_path)
        second = save_bars([bar], db_path=db_path)

        assert first.inserted == 1
        assert second.inserted == 0
        assert second.skipped_duplicates == 1

        stored = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=date(2026, 8, 1), end=date(2026, 8, 20), db_path=db_path)
        assert len(stored) == 1  # not 2

    def test_a_mixed_batch_reports_both_inserted_and_skipped_counts(self, tmp_path):
        db_path = tmp_path / "bars.db"
        bar_a = _bar(timestamp="2026-08-10T04:00:00Z")
        bar_b = _bar(timestamp="2026-08-11T04:00:00Z")
        save_bars([bar_a], db_path=db_path)

        result = save_bars([bar_a, bar_b], db_path=db_path)  # bar_a is now a dup, bar_b is new

        assert result.total == 2
        assert result.inserted == 1
        assert result.skipped_duplicates == 1

    def test_same_timestamp_different_provider_is_not_a_duplicate(self, tmp_path):
        """Provider is part of the identity key on purpose -- Alpaca and
        Massive can genuinely disagree on OHLCV for "the same" bar (see
        scripts/cross_validate_providers.py), so both must be kept."""
        db_path = tmp_path / "bars.db"
        alpaca_bar = _bar(provider="alpaca", close=247.55)
        massive_bar = _bar(provider="massive", close=247.60)

        save_bars([alpaca_bar], db_path=db_path)
        result = save_bars([massive_bar], db_path=db_path)

        assert result.inserted == 1  # not treated as a duplicate of the alpaca row

    def test_same_timestamp_different_timeframe_is_not_a_duplicate(self, tmp_path):
        db_path = tmp_path / "bars.db"
        daily = _bar(timeframe="1d")
        hourly = _bar(timeframe="1h")

        save_bars([daily], db_path=db_path)
        result = save_bars([hourly], db_path=db_path)

        assert result.inserted == 1

    def test_created_at_is_not_exposed_but_does_not_crash_a_repeat_save(self, tmp_path):
        """Re-saving an existing bar (INSERT OR IGNORE) must not error
        even though a real created_at already exists for that row."""
        db_path = tmp_path / "bars.db"
        bar = _bar()
        save_bars([bar], db_path=db_path)

        result = save_bars([bar], db_path=db_path)  # must not raise

        assert result.inserted == 0


class TestGetBars:
    def test_successful_retrieval_matches_what_was_saved(self, tmp_path):
        db_path = tmp_path / "bars.db"
        bar = _bar()
        save_bars([bar], db_path=db_path)

        stored = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=date(2026, 8, 1), end=date(2026, 8, 20), db_path=db_path)

        assert len(stored) == 1
        got = stored[0]
        assert got.symbol == bar.symbol
        assert got.timestamp == bar.timestamp
        assert got.open == bar.open
        assert got.high == bar.high
        assert got.low == bar.low
        assert got.close == bar.close
        assert got.volume == bar.volume
        assert got.provider == bar.provider
        assert got.timeframe == bar.timeframe

    def test_returns_empty_list_not_an_error_when_nothing_stored(self, tmp_path):
        db_path = tmp_path / "bars.db"
        stored = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=date(2026, 8, 1), end=date(2026, 8, 20), db_path=db_path)
        assert stored == []

    def test_multiple_symbols_do_not_bleed_into_each_others_results(self, tmp_path):
        db_path = tmp_path / "bars.db"
        save_bars([_bar(symbol="TSLA"), _bar(symbol="NVDA")], db_path=db_path)

        tsla = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=date(2026, 8, 1), end=date(2026, 8, 20), db_path=db_path)
        nvda = get_bars(symbol="NVDA", timeframe="1d", provider="alpaca", start=date(2026, 8, 1), end=date(2026, 8, 20), db_path=db_path)

        assert len(tsla) == 1 and tsla[0].symbol == "TSLA"
        assert len(nvda) == 1 and nvda[0].symbol == "NVDA"

    def test_multiple_timeframes_do_not_bleed_into_each_others_results(self, tmp_path):
        db_path = tmp_path / "bars.db"
        save_bars([_bar(timeframe="1d"), _bar(timeframe="1h")], db_path=db_path)

        daily = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=date(2026, 8, 1), end=date(2026, 8, 20), db_path=db_path)
        hourly = get_bars(symbol="TSLA", timeframe="1h", provider="alpaca", start=date(2026, 8, 1), end=date(2026, 8, 20), db_path=db_path)

        assert len(daily) == 1 and daily[0].timeframe == "1d"
        assert len(hourly) == 1 and hourly[0].timeframe == "1h"

    def test_multiple_providers_do_not_bleed_into_each_others_results(self, tmp_path):
        db_path = tmp_path / "bars.db"
        save_bars([_bar(provider="alpaca", close=247.55), _bar(provider="massive", close=247.60)], db_path=db_path)

        alpaca = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=date(2026, 8, 1), end=date(2026, 8, 20), db_path=db_path)
        massive = get_bars(symbol="TSLA", timeframe="1d", provider="massive", start=date(2026, 8, 1), end=date(2026, 8, 20), db_path=db_path)

        assert len(alpaca) == 1 and alpaca[0].close == 247.55
        assert len(massive) == 1 and massive[0].close == 247.60

    def test_date_range_retrieval_excludes_bars_outside_the_window(self, tmp_path):
        db_path = tmp_path / "bars.db"
        save_bars(
            [
                _bar(timestamp="2026-08-05T04:00:00Z"),
                _bar(timestamp="2026-08-10T04:00:00Z"),
                _bar(timestamp="2026-08-15T04:00:00Z"),
                _bar(timestamp="2026-08-20T04:00:00Z"),
            ],
            db_path=db_path,
        )

        stored = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=date(2026, 8, 8), end=date(2026, 8, 16), db_path=db_path)

        assert [b.timestamp.date().isoformat() for b in stored] == ["2026-08-10", "2026-08-15"]

    def test_start_and_end_dates_are_both_inclusive(self, tmp_path):
        db_path = tmp_path / "bars.db"
        save_bars(
            [_bar(timestamp="2026-08-10T04:00:00Z"), _bar(timestamp="2026-08-15T04:00:00Z")],
            db_path=db_path,
        )

        stored = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=date(2026, 8, 10), end=date(2026, 8, 15), db_path=db_path)

        assert len(stored) == 2

    def test_results_are_ordered_oldest_first(self, tmp_path):
        db_path = tmp_path / "bars.db"
        save_bars(
            [
                _bar(timestamp="2026-08-15T04:00:00Z"),
                _bar(timestamp="2026-08-05T04:00:00Z"),
                _bar(timestamp="2026-08-10T04:00:00Z"),
            ],
            db_path=db_path,
        )

        stored = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=date(2026, 8, 1), end=date(2026, 8, 20), db_path=db_path)

        assert [b.timestamp.day for b in stored] == [5, 10, 15]

    def test_symbol_lookup_is_case_insensitive(self, tmp_path):
        db_path = tmp_path / "bars.db"
        save_bars([_bar(symbol="TSLA")], db_path=db_path)

        stored = get_bars(symbol="tsla", timeframe="1d", provider="alpaca", start=date(2026, 8, 1), end=date(2026, 8, 20), db_path=db_path)

        assert len(stored) == 1


class TestProviderIndependence:
    """Confirms the storage layer never re-derives anything from a
    provider or cares which one produced a bar beyond the plain string
    already on HistoricalBar -- it's a pure HistoricalBar in, HistoricalBar
    out boundary, whether that bar originated from AlpacaProvider,
    MassiveProvider, or (hypothetically) any future provider."""

    def test_repository_never_imports_a_provider_module(self):
        """Crude but exact, same idiom as
        test_providers.py::TestEngineHasNoCsvDependency: checking source
        text for an import is enough to catch "does this module import
        that one" without extra tooling."""
        from pathlib import Path

        source = Path("app/storage/historical_bar_repository.py").read_text()
        assert "app.providers" not in source
        assert "app.ingestion" not in source

    def test_a_bar_round_trips_identically_regardless_of_which_provider_string_it_carries(self, tmp_path):
        db_path = tmp_path / "bars.db"
        for provider in ("alpaca", "massive", "csv", "some_future_provider"):
            save_bars([_bar(provider=provider, timestamp="2026-08-10T04:00:00Z")], db_path=db_path)
            stored = get_bars(symbol="TSLA", timeframe="1d", provider=provider, start=date(2026, 8, 1), end=date(2026, 8, 20), db_path=db_path)
            assert len(stored) == 1
            assert stored[0].provider == provider
