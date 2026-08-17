"""Tests for app/ingestion/auto_ingest.py (v0.1.18) -- the unattended
polling loop that pulls bars without a human clicking "Save to
Database". Every test mocks `historical_data_module.get_provider`
(same idiom TestExactWorkflow in test_historical_storage_api.py
already uses) so nothing here ever makes a real network call, and every
test gets an isolated, throwaway SQLite file via `isolated_db`, never a
developer's real database.
"""

import asyncio
from datetime import date, datetime, timezone

import pytest

import app.api.historical_data as historical_data_module
from app.ingestion import auto_ingest
from app.models.market_data import MarketBar, MarketTimestamp
from app.storage.historical_bar_repository import get_bars, get_quarantined_bars


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_auto_ingest.db"))


@pytest.fixture(autouse=True)
def auto_ingest_config(monkeypatch):
    """Every test gets a fixed, small config -- one symbol, one
    timeframe, a fake provider name -- so it exercises exactly the pair
    it sets up a stub provider for, and doesn't accidentally depend on
    app/config.py's real defaults (TSLA/NVDA daily)."""
    monkeypatch.setenv("AUTO_INGEST_SYMBOLS", "TSLA")
    monkeypatch.setenv("AUTO_INGEST_TIMEFRAMES", "1d")
    monkeypatch.setenv("AUTO_INGEST_PROVIDER", "alpaca")
    monkeypatch.setenv("AUTO_INGEST_LOOKBACK_DAYS", "5")


def _market_bar(timestamp: str, close: float) -> MarketBar:
    open_ = close - 1
    high = close + 2
    low = close - 3
    return MarketBar(
        symbol="TSLA",
        timestamp=MarketTimestamp(value=timestamp, source="alpaca"),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1_000_000,
    )


class TestRunIngestionCycle:
    def test_fetches_validates_and_saves_new_bars(self, monkeypatch):
        today = date.today()

        class StubProvider:
            def get_historical_data(self, **kwargs):
                return [
                    _market_bar(f"{today.isoformat()}T04:00:00Z", 250.0),
                ]

        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: StubProvider())

        cycle = auto_ingest.run_ingestion_cycle()

        assert cycle.provider == "alpaca"
        assert len(cycle.results) == 1
        result = cycle.results[0]
        assert result.symbol == "TSLA"
        assert result.timeframe == "1d"
        assert result.error is None
        assert result.fetched == 1
        assert result.inserted == 1
        assert result.rejected == 0

        stored = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=today, end=today)
        assert len(stored) == 1
        assert stored[0].close == 250.0

    def test_resaving_an_already_stored_bar_is_a_no_op_not_a_duplicate_row(self, monkeypatch):
        """The whole point of re-fetching an overlapping lookback window
        every cycle (see run_ingestion_cycle()'s docstring): running the
        cycle twice must not create two rows for the same bar."""
        today = date.today()

        class StubProvider:
            def get_historical_data(self, **kwargs):
                return [_market_bar(f"{today.isoformat()}T04:00:00Z", 250.0)]

        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: StubProvider())

        first = auto_ingest.run_ingestion_cycle()
        second = auto_ingest.run_ingestion_cycle()

        assert first.results[0].inserted == 1
        assert second.results[0].inserted == 0
        assert second.results[0].skipped_duplicates == 1

        stored = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=today, end=today)
        assert len(stored) == 1

    def test_impossible_ohlcv_bar_is_quarantined_not_stored(self, monkeypatch):
        today = date.today()

        class StubProvider:
            def get_historical_data(self, **kwargs):
                bad = _market_bar(f"{today.isoformat()}T04:00:00Z", 250.0)
                bad.high = 1.0
                bad.low = 999.0
                return [bad]

        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: StubProvider())

        cycle = auto_ingest.run_ingestion_cycle()

        result = cycle.results[0]
        assert result.inserted == 0
        assert result.rejected == 1

        stored = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=today, end=today)
        assert stored == []
        quarantined = get_quarantined_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=today, end=today)
        assert len(quarantined) == 1

    def test_a_provider_failure_is_isolated_and_reported_not_raised(self, monkeypatch):
        """One pair's provider call raising must not propagate out of
        run_ingestion_cycle() -- see the module docstring's "one
        failure is isolated" guarantee."""

        def broken_provider(name):
            raise RuntimeError("AlpacaProvider requires ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY ...")

        monkeypatch.setattr(historical_data_module, "get_provider", broken_provider)

        cycle = auto_ingest.run_ingestion_cycle()  # must not raise

        assert len(cycle.results) == 1
        result = cycle.results[0]
        assert result.error is not None
        assert "ALPACA_API_KEY_ID" in result.error
        assert result.inserted == 0

    def test_multiple_pairs_one_failing_does_not_stop_the_others(self, monkeypatch):
        today = date.today()

        class StubProvider:
            def get_historical_data(self, **kwargs):
                if kwargs.get("symbol") == "NVDA":
                    raise RuntimeError("simulated provider failure for NVDA")
                return [_market_bar(f"{today.isoformat()}T04:00:00Z", 250.0)]

        monkeypatch.setenv("AUTO_INGEST_SYMBOLS", "TSLA,NVDA")
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: StubProvider())

        cycle = auto_ingest.run_ingestion_cycle()

        by_symbol = {r.symbol: r for r in cycle.results}
        assert by_symbol["TSLA"].error is None
        assert by_symbol["TSLA"].inserted == 1
        assert by_symbol["NVDA"].error is not None
        assert by_symbol["NVDA"].inserted == 0


class TestRunIngestionLoop:
    def test_runs_a_cycle_immediately_then_stops_cleanly_when_asked(self, monkeypatch):
        """A long interval (so the test would hang if the loop waited
        for it instead of running immediately) combined with the
        stop_event being set right away proves both halves of the
        contract: an immediate first cycle, and a prompt, clean exit."""
        today = date.today()
        call_count = 0

        class StubProvider:
            def get_historical_data(self, **kwargs):
                nonlocal call_count
                call_count += 1
                return [_market_bar(f"{today.isoformat()}T04:00:00Z", 250.0)]

        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: StubProvider())
        monkeypatch.setenv("AUTO_INGEST_INTERVAL_SECONDS", "3600")  # would hang the test if actually awaited

        async def run():
            stop_event = asyncio.Event()
            task = asyncio.create_task(auto_ingest.run_ingestion_loop(stop_event=stop_event))
            await asyncio.sleep(0.05)  # let the immediate first cycle run
            stop_event.set()
            await asyncio.wait_for(task, timeout=5)

        asyncio.run(run())

        assert call_count == 1
        stored = get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=today, end=today)
        assert len(stored) == 1


class TestPairFailureTracker:
    """Direct unit tests of the tracker (v0.1.23), independent of the
    async loop -- feed it PairResult objects by hand and assert on the
    log records it produces via caplog, the same "one small piece of
    real logic, tested on its own" split the rest of this module uses."""

    def _result(self, *, error: str | None) -> "auto_ingest.PairResult":
        return auto_ingest.PairResult(symbol="TSLA", timeframe="1d", fetched=1, inserted=1, error=error)

    def test_a_single_failure_below_threshold_does_not_escalate(self, caplog):
        tracker = auto_ingest.PairFailureTracker(alert_threshold=3)
        with caplog.at_level("WARNING", logger="app.ingestion.auto_ingest"):
            tracker.record(self._result(error="boom"))
        assert not any(r.levelname == "ERROR" for r in caplog.records)

    def test_reaching_the_threshold_logs_exactly_one_error(self, caplog):
        tracker = auto_ingest.PairFailureTracker(alert_threshold=3)
        with caplog.at_level("WARNING", logger="app.ingestion.auto_ingest"):
            tracker.record(self._result(error="boom"))
            tracker.record(self._result(error="boom"))
            tracker.record(self._result(error="boom"))
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(errors) == 1
        assert "TSLA/1d" in errors[0].message
        assert "3 consecutive" in errors[0].message

    def test_continuing_to_fail_past_the_threshold_logs_another_error_each_cycle(self, caplog):
        tracker = auto_ingest.PairFailureTracker(alert_threshold=2)
        with caplog.at_level("WARNING", logger="app.ingestion.auto_ingest"):
            for _ in range(4):
                tracker.record(self._result(error="boom"))
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(errors) == 3  # cycles 2, 3, 4 -- every cycle once the threshold is crossed

    def test_a_success_before_reaching_threshold_resets_the_count_silently(self, caplog):
        tracker = auto_ingest.PairFailureTracker(alert_threshold=3)
        with caplog.at_level("INFO", logger="app.ingestion.auto_ingest"):
            tracker.record(self._result(error="boom"))
            tracker.record(self._result(error="boom"))
            tracker.record(self._result(error=None))  # recovers before hitting 3
            tracker.record(self._result(error="boom"))
            tracker.record(self._result(error="boom"))
        # never reached 3 consecutive failures in a row, and never escalated -- no recovery line either
        assert not any(r.levelname in ("ERROR", "INFO") for r in caplog.records)

    def test_recovering_after_escalation_logs_one_info_recovery_line(self, caplog):
        tracker = auto_ingest.PairFailureTracker(alert_threshold=2)
        with caplog.at_level("INFO", logger="app.ingestion.auto_ingest"):
            tracker.record(self._result(error="boom"))
            tracker.record(self._result(error="boom"))  # escalates here
            tracker.record(self._result(error=None))  # recovers
        recoveries = [r for r in caplog.records if r.levelname == "INFO" and "recovered" in r.message]
        assert len(recoveries) == 1
        assert "TSLA/1d" in recoveries[0].message

    def test_recovering_without_ever_escalating_logs_nothing(self, caplog):
        tracker = auto_ingest.PairFailureTracker(alert_threshold=5)
        with caplog.at_level("INFO", logger="app.ingestion.auto_ingest"):
            tracker.record(self._result(error="boom"))
            tracker.record(self._result(error=None))
        assert not any(r.levelname == "INFO" and "recovered" in r.message for r in caplog.records)

    def test_different_pairs_are_tracked_independently(self, caplog):
        tracker = auto_ingest.PairFailureTracker(alert_threshold=2)
        tsla = auto_ingest.PairResult(symbol="TSLA", timeframe="1d", error="boom")
        nvda = auto_ingest.PairResult(symbol="NVDA", timeframe="1d", error=None)
        with caplog.at_level("WARNING", logger="app.ingestion.auto_ingest"):
            tracker.record(tsla)
            tracker.record(nvda)
            tracker.record(tsla)  # TSLA's second consecutive failure -- escalates
            tracker.record(nvda)  # NVDA never failed at all -- no effect
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(errors) == 1
        assert "TSLA/1d" in errors[0].message


class TestRunIngestionLoopFailureEscalation:
    def test_a_pair_failing_past_the_threshold_across_real_cycles_escalates(self, monkeypatch, caplog):
        """End-to-end through the actual async loop (not the tracker in
        isolation): a provider that always raises, polled on a fast
        interval, must produce an ERROR log line once
        AUTO_INGEST_FAILURE_ALERT_THRESHOLD consecutive cycles have
        failed."""

        def broken_provider(name):
            raise RuntimeError("simulated persistent credential failure")

        monkeypatch.setattr(historical_data_module, "get_provider", broken_provider)
        monkeypatch.setenv("AUTO_INGEST_INTERVAL_SECONDS", "0")
        monkeypatch.setenv("AUTO_INGEST_FAILURE_ALERT_THRESHOLD", "3")

        async def run():
            stop_event = asyncio.Event()
            task = asyncio.create_task(auto_ingest.run_ingestion_loop(stop_event=stop_event))
            await asyncio.sleep(0.3)  # several fast cycles
            stop_event.set()
            await asyncio.wait_for(task, timeout=5)

        with caplog.at_level("WARNING", logger="app.ingestion.auto_ingest"):
            asyncio.run(run())

        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(errors) >= 1
        assert "TSLA/1d" in errors[0].message
