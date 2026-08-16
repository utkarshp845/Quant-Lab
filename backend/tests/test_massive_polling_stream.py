"""Tests for MassivePollingQuoteStream and MassiveStream
(app/streaming/massive_stream.py, v0.1.15) -- the free-tier REST
polling fallback used when Massive's real-time WebSocket isn't
entitled on the current plan, and the orchestrator that decides when
to fall back to it.

No real network call, no real Massive credentials, anywhere in this
file: MassivePollingQuoteStream takes an injectable `provider` (a fake
standing in for MassiveProvider, same pattern MassiveProvider's own
tests use for its HTTP client), and MassiveStream takes injectable
`ws_stream_factory`/`poll_stream_factory` (same idea as
app/streaming/hub.py's stream_factory param).
"""

import asyncio
from datetime import date, datetime, timezone

import pytest

from app.models.market_data import LiveQuote, MarketBar, MarketTimestamp
from app.streaming.base import StreamAuthRejected, StreamCredentialsMissing, StreamTransientError
from app.streaming.massive_stream import MassivePollingQuoteStream, MassiveStream


def _bar(close: float, volume: int, ts: str) -> MarketBar:
    return MarketBar(
        symbol="TSLA",
        timestamp=MarketTimestamp(value=ts, source="massive"),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
    )


class _FakeMassiveProvider:
    """Each call to get_historical_data() pops the next canned result
    (a list of MarketBar, or an Exception to raise) off `responses` --
    lets a test script exactly what MassivePollingQuoteStream's "today
    empty -> widen the window" fallback sees on each of its (up to two)
    calls per poll."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def get_historical_data(self, **kwargs) -> list[MarketBar]:
        self.calls.append(kwargs)
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _make_polling_stream(responses, **overrides):
    quotes: list[LiveQuote] = []
    statuses: list[tuple[str, str | None]] = []

    async def on_quote(q: LiveQuote) -> None:
        quotes.append(q)

    async def on_status(status: str, detail: str | None) -> None:
        statuses.append((status, detail))

    provider = _FakeMassiveProvider(responses)
    kwargs = dict(
        symbol="TSLA",
        on_quote=on_quote,
        on_status=on_status,
        provider=provider,
        poll_interval_seconds=0.01,  # keep tests fast
    )
    kwargs.update(overrides)
    return MassivePollingQuoteStream(**kwargs), provider, quotes, statuses


class TestLatestBarLookup:
    def test_bars_found_for_today_are_used_directly(self):
        bar = _bar(340.2, 1500, "2026-08-14T20:00:00Z")
        stream, provider, _, _ = _make_polling_stream([[bar]])
        found = stream._latest_bar()
        assert found is bar
        assert len(provider.calls) == 1
        # Never start=end=today() alone -- confirmed live to 403
        # outright on this plan (see the class docstring) -- always a
        # trailing window ending today instead.
        assert provider.calls[0]["end"] == date.today()
        assert provider.calls[0]["start"] < provider.calls[0]["end"]

    def test_no_bars_in_the_window_returns_none(self):
        stream, provider, _, _ = _make_polling_stream([[]])
        assert stream._latest_bar() is None
        assert len(provider.calls) == 1  # one query, no retry-with-a-narrower-range attempt


class TestQuoteNormalization:
    def test_a_bar_becomes_a_live_quote_with_no_bid_ask(self):
        bar = _bar(340.2, 1500, "2026-08-14T20:00:00Z")
        stream, _, quotes, statuses = _make_polling_stream([[bar], [bar]])

        async def run_briefly():
            task = asyncio.ensure_future(stream.run())
            await asyncio.sleep(0.05)
            stream.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_briefly())

        assert len(quotes) >= 1
        q = quotes[0]
        assert q.symbol == "TSLA"
        assert q.price == 340.2
        assert q.bid is None
        assert q.ask is None
        assert q.volume == 1500
        assert q.provider == "massive"
        assert ("connected", None) not in statuses  # always carries a detail message
        assert any(status == "connected" for status, _ in statuses)

    def test_an_unchanged_bar_is_not_re_emitted(self):
        bar = _bar(340.2, 1500, "2026-08-14T20:00:00Z")
        stream, _, quotes, _ = _make_polling_stream([[bar]] * 5)

        async def run_briefly():
            task = asyncio.ensure_future(stream.run())
            await asyncio.sleep(0.08)  # several poll cycles at a 0.01s interval
            stream.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_briefly())

        assert len(quotes) == 1  # same bar every time -- only the first emits


class TestFailureClassification:
    def test_credentials_missing_is_fatal(self):
        stream, _, quotes, statuses = _make_polling_stream(
            [RuntimeError("MassiveProvider requires a MASSIVE_API_KEY environment variable (or an explicit constructor argument).")]
        )
        asyncio.run(asyncio.wait_for(stream.run(), timeout=2))
        assert quotes == []
        assert statuses[-1][0] == "error"
        assert "MASSIVE_API_KEY" in statuses[-1][1]

    def test_entitlement_gap_is_also_fatal(self):
        """MassiveProvider._get() already turns a 403 NOT_AUTHORIZED
        into a RuntimeError (see massive_provider.py) -- same fatal
        treatment as missing credentials, since retrying an
        entitlement-denied endpoint on a schedule can't fix it either."""
        stream, _, quotes, statuses = _make_polling_stream(
            [RuntimeError("MassiveProvider: not entitled to /v2/aggs/... on your current Massive plan")]
        )
        asyncio.run(asyncio.wait_for(stream.run(), timeout=2))
        assert quotes == []
        assert statuses[-1][0] == "error"

    def test_a_transient_error_is_retried_not_treated_as_fatal(self):
        bar = _bar(340.2, 1500, "2026-08-14T20:00:00Z")
        stream, _, quotes, statuses = _make_polling_stream([TimeoutError("network blip"), [bar]])

        async def run_briefly():
            task = asyncio.ensure_future(stream.run())
            await asyncio.sleep(0.05)
            stream.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_briefly())

        assert not any(status == "error" for status, _ in statuses)
        assert any(status == "disconnected" for status, _ in statuses)
        assert len(quotes) == 1  # it recovered and emitted the bar on the next poll


class _FakeStream:
    """Stands in for MassiveQuoteStream or MassivePollingQuoteStream in
    MassiveStream tests -- run() does exactly what the test configures
    (raise via on_status("error", ...) then return, or block until
    cancelled) without any real network or timing dependency."""

    instances: list["_FakeStream"] = []

    def __init__(self, *, symbol, on_quote, on_status, api_key=None, behavior="block"):
        self.symbol = symbol
        self._on_quote = on_quote
        self._on_status = on_status
        self._behavior = behavior
        self.stopped = False
        _FakeStream.instances.append(self)

    async def run(self) -> None:
        if self._behavior == "fatal":
            await self._on_status("error", "Massive rejected the API key: doesn't include websocket access")
            return
        await self._on_status("connected", None)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture(autouse=True)
def _reset_fake_stream_instances():
    _FakeStream.instances = []
    yield
    _FakeStream.instances = []


def _fatal_ws_factory(**kwargs):
    return _FakeStream(behavior="fatal", **kwargs)


def _blocking_factory(**kwargs):
    return _FakeStream(behavior="block", **kwargs)


class TestMassiveStreamFallback:
    def test_a_fatal_websocket_falls_back_to_polling(self):
        statuses: list[tuple[str, str | None]] = []

        async def on_status(status, detail):
            statuses.append((status, detail))

        async def on_quote(q):
            pass

        stream = MassiveStream(
            symbol="TSLA",
            on_quote=on_quote,
            on_status=on_status,
            ws_stream_factory=_fatal_ws_factory,
            poll_stream_factory=_blocking_factory,
        )

        async def run_briefly():
            task = asyncio.ensure_future(stream.run())
            await asyncio.sleep(0.05)
            stream.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_briefly())

        # the WS stream's own "error" must never reach the outer caller directly
        assert not any(status == "error" for status, _ in statuses)
        # instead: a "connecting" status explaining the fallback, then
        # the polling fake's "connected"
        assert any(
            status == "connecting" and detail and "falling back to polling" in detail for status, detail in statuses
        )
        assert any(status == "connected" for status, _ in statuses)

    def test_a_working_websocket_never_touches_polling(self):
        statuses: list[tuple[str, str | None]] = []

        async def on_status(status, detail):
            statuses.append((status, detail))

        async def on_quote(q):
            pass

        stream = MassiveStream(
            symbol="TSLA",
            on_quote=on_quote,
            on_status=on_status,
            ws_stream_factory=_blocking_factory,
            poll_stream_factory=_fatal_ws_factory,  # would show up immediately if ever reached
        )

        async def run_briefly():
            task = asyncio.ensure_future(stream.run())
            await asyncio.sleep(0.05)
            stream.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_briefly())

        assert statuses == [("connected", None)]  # only ever the WS fake's status

    def test_stop_propagates_to_both_inner_streams(self):
        async def on_status(status, detail):
            pass

        async def on_quote(q):
            pass

        stream = MassiveStream(
            symbol="TSLA",
            on_quote=on_quote,
            on_status=on_status,
            ws_stream_factory=_blocking_factory,
            poll_stream_factory=_blocking_factory,
        )

        async def run_briefly():
            task = asyncio.ensure_future(stream.run())
            await asyncio.sleep(0.02)
            stream.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_briefly())

        ws_fake, poll_fake = _FakeStream.instances
        assert ws_fake.stopped is True
        assert poll_fake.stopped is True  # even though it was never reached (WS is still "blocking")
