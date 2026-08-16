"""Tests for app/streaming/hub.py -- the fan-out/lifecycle logic, using
a fake stream factory so nothing here ever opens a real network
connection or needs real credentials.

Confirms the hub's actual reason for existing (see its module
docstring): exactly one upstream "connection" per (provider, symbol)
pair no matter how many clients subscribe, independence across
providers (v0.1.13, Alpaca and Massive streaming TSLA at once must not
interfere), replay of current state to a client that joins after the
stream is already up, and teardown once the last client leaves.
"""

import asyncio

import pytest

from app.models.market_data import LiveQuote
from app.streaming.hub import StreamHub


class FakeQuoteStream:
    """Stands in for a real *QuoteStream: on run(), reports
    "connecting" then "connected" and waits to be cancelled -- never
    touches a real socket. `instances` (class-level) lets a test assert
    how many times the hub actually constructed one of these, i.e. how
    many upstream connections it opened."""

    instances: list["FakeQuoteStream"] = []

    def __init__(self, *, symbol, on_quote, on_status):
        self.symbol = symbol
        self._on_quote = on_quote
        self._on_status = on_status
        self.stopped = False
        FakeQuoteStream.instances.append(self)

    async def run(self) -> None:
        await self._on_status("connecting", None)
        await self._on_status("connected", None)
        try:
            await asyncio.Event().wait()  # blocks until the hub cancels this task
        except asyncio.CancelledError:
            raise

    def stop(self) -> None:
        self.stopped = True

    async def emit_quote(self, price: float, provider: str = "alpaca") -> None:
        await self._on_quote(
            LiveQuote(symbol=self.symbol, price=price, bid=price - 0.05, ask=price + 0.05, volume=None, timestamp="2026-08-15T20:00:00Z", provider=provider)
        )


@pytest.fixture(autouse=True)
def _reset_instances():
    FakeQuoteStream.instances = []
    yield
    FakeQuoteStream.instances = []


def _make_hub(**factories: type) -> StreamHub:
    factories = factories or {"alpaca": FakeQuoteStream, "massive": FakeQuoteStream}
    return StreamHub(stream_factories=factories)


async def _drain_status_and_quote(queue: asyncio.Queue, count: int) -> list[tuple]:
    return [await asyncio.wait_for(queue.get(), timeout=1) for _ in range(count)]


class TestSupports:
    def test_supports_only_the_configured_providers(self):
        hub = _make_hub()
        assert hub.supports("alpaca")
        assert hub.supports("massive")
        assert not hub.supports("schwab")


class TestSingleUpstreamPerProviderAndSymbol:
    def test_two_subscribers_share_one_upstream_connection(self):
        async def scenario():
            hub = _make_hub()
            q1 = await hub.subscribe("alpaca", "TSLA")
            await _drain_status_and_quote(q1, 2)  # replay "connecting" + real "connecting"/"connected"

            q2 = await hub.subscribe("alpaca", "TSLA")
            assert len(FakeQuoteStream.instances) == 1  # no second upstream connection opened

            await hub.unsubscribe("alpaca", "TSLA", q1)
            await hub.unsubscribe("alpaca", "TSLA", q2)

        asyncio.run(scenario())

    def test_different_symbols_get_independent_upstream_connections(self):
        async def scenario():
            hub = _make_hub()
            q_tsla = await hub.subscribe("alpaca", "TSLA")
            q_aapl = await hub.subscribe("alpaca", "AAPL")
            assert len(FakeQuoteStream.instances) == 2
            await hub.unsubscribe("alpaca", "TSLA", q_tsla)
            await hub.unsubscribe("alpaca", "AAPL", q_aapl)

        asyncio.run(scenario())

    def test_different_providers_for_the_same_symbol_get_independent_connections(self):
        """The whole reason to key by (provider, symbol) and not just
        symbol (v0.1.13): TSLA over Alpaca and TSLA over Massive must
        not be treated as the same upstream connection."""

        async def scenario():
            hub = _make_hub()
            q_alpaca = await hub.subscribe("alpaca", "TSLA")
            q_massive = await hub.subscribe("massive", "TSLA")
            assert len(FakeQuoteStream.instances) == 2

            # each stays independent -- unsubscribing one must not
            # touch the other's connection
            await hub.unsubscribe("alpaca", "TSLA", q_alpaca)
            assert FakeQuoteStream.instances[0].stopped is True
            assert FakeQuoteStream.instances[1].stopped is False

            await hub.unsubscribe("massive", "TSLA", q_massive)

        asyncio.run(scenario())


class TestReplayForLateJoiners:
    def test_late_subscriber_immediately_sees_current_status_and_last_quote(self):
        async def scenario():
            hub = _make_hub()
            q1 = await hub.subscribe("alpaca", "TSLA")
            await _drain_status_and_quote(q1, 2)  # let the fake stream reach "connected"

            stream = FakeQuoteStream.instances[0]
            await stream.emit_quote(340.0)
            await q1.get()  # drain the quote off q1 so it doesn't interfere with this assertion

            q2 = await hub.subscribe("alpaca", "TSLA")
            first, second = await _drain_status_and_quote(q2, 2)
            assert first == ("status", "connected", None)
            assert second[0] == "quote"
            assert second[1].price == 340.0

            await hub.unsubscribe("alpaca", "TSLA", q1)
            await hub.unsubscribe("alpaca", "TSLA", q2)

        asyncio.run(scenario())


class TestTeardownOnLastUnsubscribe:
    def test_stream_is_stopped_and_cancelled_once_no_clients_remain(self):
        async def scenario():
            hub = _make_hub()
            q1 = await hub.subscribe("alpaca", "TSLA")
            await _drain_status_and_quote(q1, 2)
            stream = FakeQuoteStream.instances[0]

            await hub.unsubscribe("alpaca", "TSLA", q1)
            assert stream.stopped is True

        asyncio.run(scenario())

    def test_a_new_subscriber_after_full_teardown_opens_a_fresh_connection(self):
        async def scenario():
            hub = _make_hub()
            q1 = await hub.subscribe("alpaca", "TSLA")
            await _drain_status_and_quote(q1, 2)
            await hub.unsubscribe("alpaca", "TSLA", q1)

            q2 = await hub.subscribe("alpaca", "TSLA")
            await _drain_status_and_quote(q2, 2)
            assert len(FakeQuoteStream.instances) == 2  # first torn down, second freshly opened

            await hub.unsubscribe("alpaca", "TSLA", q2)

        asyncio.run(scenario())
