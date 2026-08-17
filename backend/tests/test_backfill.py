"""Tests for app/ingestion/backfill.py (v0.1.22) -- the deep-range
historical backfill, as opposed to auto_ingest.py's small-trailing-
window freshness loop. Every test mocks
`historical_data_module.get_provider` (same idiom test_auto_ingest.py
already uses) so nothing here ever makes a real network call, and every
test gets an isolated, throwaway SQLite file via `isolated_db`.
"""

from datetime import date, timedelta

import httpx
import pytest

import app.api.historical_data as historical_data_module
from app.ingestion import backfill
from app.models.market_data import MarketBar, MarketTimestamp
from app.storage.historical_bar_repository import get_bars, get_quarantined_bars


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_backfill.db"))


def _market_bar(timestamp: str, close: float) -> MarketBar:
    return MarketBar(
        symbol="TSLA",
        timestamp=MarketTimestamp(value=timestamp, source="alpaca"),
        open=close - 1,
        high=close + 2,
        low=close - 3,
        close=close,
        volume=1_000_000,
    )


class TestDateChunks:
    def test_a_range_shorter_than_chunk_days_yields_one_chunk(self):
        start, end = date(2026, 1, 1), date(2026, 1, 10)
        chunks = list(backfill._date_chunks(start, end, chunk_days=365))
        assert chunks == [(start, end)]

    def test_a_range_exactly_two_years_with_365_day_chunks_yields_two_chunks(self):
        start = date(2024, 1, 1)
        end = start + timedelta(days=729)  # two 365-day windows, inclusive
        chunks = list(backfill._date_chunks(start, end, chunk_days=365))
        assert len(chunks) == 2
        assert chunks[0][0] == start
        # consecutive, non-overlapping: next chunk starts exactly one day after the previous ends
        assert chunks[1][0] == chunks[0][1] + timedelta(days=1)
        assert chunks[-1][1] == end

    def test_chunks_cover_the_full_range_with_no_gaps_or_overlaps(self):
        start, end = date(2023, 3, 5), date(2026, 8, 17)
        chunks = list(backfill._date_chunks(start, end, chunk_days=90))
        assert chunks[0][0] == start
        assert chunks[-1][1] == end
        for (s1, e1), (s2, _e2) in zip(chunks, chunks[1:]):
            assert s2 == e1 + timedelta(days=1)

    def test_single_day_range_yields_one_single_day_chunk(self):
        d = date(2026, 8, 17)
        assert list(backfill._date_chunks(d, d, chunk_days=365)) == [(d, d)]

    def test_zero_or_negative_chunk_days_raises(self):
        with pytest.raises(ValueError):
            list(backfill._date_chunks(date(2026, 1, 1), date(2026, 1, 2), chunk_days=0))

    def test_start_after_end_raises(self):
        with pytest.raises(ValueError):
            list(backfill._date_chunks(date(2026, 1, 2), date(2026, 1, 1), chunk_days=365))


class TestRunBackfill:
    def test_fetches_validates_and_saves_bars_across_chunks(self, monkeypatch):
        calls = []

        class StubProvider:
            def get_historical_data(self, **kwargs):
                calls.append((kwargs["start"], kwargs["end"]))
                return [_market_bar(f"{kwargs['start'].isoformat()}T14:30:00Z", 250.0)]

        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: StubProvider())

        start = date(2024, 1, 1)
        end = start + timedelta(days=729)  # -> two 365-day chunks
        result = backfill.run_backfill(
            symbols=["TSLA"], timeframes=["1d"], start=start, end=end, provider="alpaca",
            chunk_days=365, sleep_seconds=0,
        )

        assert len(calls) == 2  # one provider call per chunk
        assert result.total_fetched == 2
        assert result.total_inserted == 2
        assert result.failed_chunks == []

        stored = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=start, end=end)
        assert len(stored) == 2

    def test_covers_every_symbol_timeframe_pair(self, monkeypatch):
        calls = []

        class StubProvider:
            def get_historical_data(self, **kwargs):
                calls.append((kwargs["symbol"], kwargs["timeframe"]))
                return [_market_bar(f"{kwargs['start'].isoformat()}T14:30:00Z", 100.0)]

        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: StubProvider())

        start = end = date(2026, 8, 17)
        backfill.run_backfill(
            symbols=["TSLA", "NVDA"], timeframes=["1d", "5m"], start=start, end=end,
            provider="alpaca", sleep_seconds=0,
        )

        assert set(calls) == {("TSLA", "1Day"), ("TSLA", "5Min"), ("NVDA", "1Day"), ("NVDA", "5Min")}

    def test_rerunning_the_same_range_is_all_duplicates_not_new_rows(self, monkeypatch):
        class StubProvider:
            def get_historical_data(self, **kwargs):
                return [_market_bar(f"{kwargs['start'].isoformat()}T14:30:00Z", 250.0)]

        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: StubProvider())

        start = end = date(2026, 8, 17)
        first = backfill.run_backfill(symbols=["TSLA"], timeframes=["1d"], start=start, end=end, provider="alpaca", sleep_seconds=0)
        second = backfill.run_backfill(symbols=["TSLA"], timeframes=["1d"], start=start, end=end, provider="alpaca", sleep_seconds=0)

        assert first.total_inserted == 1
        assert second.total_inserted == 0
        assert second.total_skipped_duplicates == 1
        stored = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=start, end=end)
        assert len(stored) == 1

    def test_impossible_ohlcv_bar_is_quarantined_not_stored(self, monkeypatch):
        class StubProvider:
            def get_historical_data(self, **kwargs):
                bad = _market_bar(f"{kwargs['start'].isoformat()}T14:30:00Z", 250.0)
                bad.high = 1.0
                bad.low = 999.0
                return [bad]

        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: StubProvider())

        start = end = date(2026, 8, 17)
        result = backfill.run_backfill(symbols=["TSLA"], timeframes=["1d"], start=start, end=end, provider="alpaca", sleep_seconds=0)

        assert result.total_inserted == 0
        assert result.total_rejected == 1
        stored = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=start, end=end)
        assert stored == []
        quarantined = get_quarantined_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=start, end=end)
        assert len(quarantined) == 1

    def test_one_chunk_failing_does_not_abort_the_rest_of_the_run(self, monkeypatch):
        start = date(2024, 1, 1)
        end = start + timedelta(days=729)  # two chunks

        class StubProvider:
            def get_historical_data(self, **kwargs):
                if kwargs["start"] == start:
                    raise RuntimeError("simulated provider failure")
                return [_market_bar(f"{kwargs['start'].isoformat()}T14:30:00Z", 250.0)]

        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: StubProvider())

        result = backfill.run_backfill(
            symbols=["TSLA"], timeframes=["1d"], start=start, end=end, provider="alpaca",
            chunk_days=365, sleep_seconds=0,
        )

        assert len(result.results) == 2
        assert len(result.failed_chunks) == 1
        assert result.failed_chunks[0].start == start
        assert result.total_inserted == 1  # the second chunk still saved

    def test_a_429_is_retried_with_backoff_then_succeeds(self, monkeypatch):
        attempts = {"n": 0}
        sleeps = []

        class StubProvider:
            def get_historical_data(self, **kwargs):
                attempts["n"] += 1
                if attempts["n"] < 3:
                    request = httpx.Request("GET", "https://data.alpaca.markets/v2/stocks/TSLA/bars")
                    response = httpx.Response(429, request=request)
                    raise httpx.HTTPStatusError("rate limited", request=request, response=response)
                return [_market_bar(f"{kwargs['start'].isoformat()}T14:30:00Z", 250.0)]

        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: StubProvider())

        start = end = date(2026, 8, 17)
        result = backfill.run_backfill(
            symbols=["TSLA"], timeframes=["1d"], start=start, end=end, provider="alpaca",
            sleep_seconds=1, max_retries=3, _sleep=sleeps.append,
        )

        assert attempts["n"] == 3  # failed twice, succeeded on the third
        assert result.total_inserted == 1
        assert result.failed_chunks == []
        assert len(sleeps) >= 2  # at least the two retry backoffs were recorded

    def test_a_429_that_never_recovers_gives_up_after_max_retries(self, monkeypatch):
        class StubProvider:
            def get_historical_data(self, **kwargs):
                request = httpx.Request("GET", "https://data.alpaca.markets/v2/stocks/TSLA/bars")
                response = httpx.Response(429, request=request)
                raise httpx.HTTPStatusError("rate limited", request=request, response=response)

        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: StubProvider())

        start = end = date(2026, 8, 17)
        result = backfill.run_backfill(
            symbols=["TSLA"], timeframes=["1d"], start=start, end=end, provider="alpaca",
            sleep_seconds=0, max_retries=2, _sleep=lambda *_: None,
        )

        assert len(result.failed_chunks) == 1
        assert "429" in result.failed_chunks[0].error or "rate limited" in result.failed_chunks[0].error

    def test_on_chunk_complete_callback_fires_once_per_chunk_in_order(self, monkeypatch):
        class StubProvider:
            def get_historical_data(self, **kwargs):
                return [_market_bar(f"{kwargs['start'].isoformat()}T14:30:00Z", 250.0)]

        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: StubProvider())

        start = date(2024, 1, 1)
        end = start + timedelta(days=729)  # two chunks
        seen = []
        backfill.run_backfill(
            symbols=["TSLA"], timeframes=["1d"], start=start, end=end, provider="alpaca",
            chunk_days=365, sleep_seconds=0, on_chunk_complete=seen.append,
        )

        assert len(seen) == 2
        assert seen[0].start == start
        assert seen[1].start == seen[0].end + timedelta(days=1)
