"""Tests for the WebSocket route in app/api/market_data_stream.py.

Uses FastAPI's TestClient.websocket_connect (starlette under the hood)
-- the same style of in-process testing test_market_data_api.py uses
for the REST route, just for a socket instead of a request/response.
The route's own hub is monkeypatched out for a fake one so no test
here ever opens a real connection to Alpaca or Massive.
"""

import asyncio

from fastapi.testclient import TestClient

import app.api.market_data_stream as market_data_stream_module
from app.main import app

client = TestClient(app)


class _FakeHub:
    """Replaces app.streaming.hub.hub for these tests: supports()
    mirrors the real hub's provider allowlist, subscribe() returns a
    queue pre-loaded with whatever frames the test wants sent, and
    unsubscribe() records that it was called so a test can confirm the
    route cleans up after itself."""

    def __init__(self, items: list[tuple], supported: set[str] = frozenset({"alpaca", "massive"})):
        self._items = items
        self._supported = supported
        self.unsubscribed: list[tuple[str, str, asyncio.Queue]] = []

    def supports(self, provider: str) -> bool:
        return provider in self._supported

    async def subscribe(self, provider: str, symbol: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        for item in self._items:
            await queue.put(item)
        return queue

    async def unsubscribe(self, provider: str, symbol: str, queue: asyncio.Queue) -> None:
        self.unsubscribed.append((provider, symbol, queue))


def test_status_and_quote_frames_are_relayed_to_the_client(monkeypatch):
    quote = {
        "symbol": "TSLA",
        "price": 340.2,
        "bid": 340.0,
        "ask": 340.5,
        "volume": 150,
        "timestamp": "2026-08-15T20:00:00+00:00",
        "provider": "alpaca",
    }

    class _Quote:
        def model_dump(self, mode="json"):
            return quote

    fake_hub = _FakeHub([("status", "connecting", None), ("status", "connected", None), ("quote", _Quote(), None)])
    monkeypatch.setattr(market_data_stream_module, "hub", fake_hub)

    with client.websocket_connect("/api/market-data/stream?symbol=TSLA&provider=alpaca") as ws:
        assert ws.receive_json() == {"type": "status", "status": "connecting", "detail": None}
        assert ws.receive_json() == {"type": "status", "status": "connected", "detail": None}
        assert ws.receive_json() == {"type": "quote", "quote": quote}

    assert fake_hub.unsubscribed and fake_hub.unsubscribed[0][:2] == ("alpaca", "TSLA")


def test_defaults_to_tsla_when_no_symbol_is_given(monkeypatch):
    seen = []

    class _RecordingHub(_FakeHub):
        async def subscribe(self, provider: str, symbol: str) -> asyncio.Queue:
            seen.append((provider, symbol))
            return await super().subscribe(provider, symbol)

    monkeypatch.setattr(market_data_stream_module, "hub", _RecordingHub([("status", "connecting", None)]))

    with client.websocket_connect("/api/market-data/stream?provider=alpaca") as ws:
        ws.receive_json()

    assert seen == [("alpaca", "TSLA")]


def test_massive_provider_is_relayed_the_same_way_as_alpaca(monkeypatch):
    quote = {
        "symbol": "TSLA",
        "price": 340.2,
        "bid": 340.0,
        "ask": 340.5,
        "volume": 150,
        "timestamp": "2026-08-15T20:00:00+00:00",
        "provider": "massive",
    }

    class _Quote:
        def model_dump(self, mode="json"):
            return quote

    fake_hub = _FakeHub([("status", "connected", None), ("quote", _Quote(), None)])
    monkeypatch.setattr(market_data_stream_module, "hub", fake_hub)

    with client.websocket_connect("/api/market-data/stream?symbol=TSLA&provider=massive") as ws:
        assert ws.receive_json() == {"type": "status", "status": "connected", "detail": None}
        assert ws.receive_json() == {"type": "quote", "quote": quote}

    assert fake_hub.unsubscribed and fake_hub.unsubscribed[0][:2] == ("massive", "TSLA")


def test_unsupported_provider_gets_an_error_frame_and_closes(monkeypatch):
    monkeypatch.setattr(market_data_stream_module, "hub", _FakeHub([], supported={"alpaca", "massive"}))

    with client.websocket_connect("/api/market-data/stream?symbol=TSLA&provider=schwab") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "status"
        assert msg["status"] == "error"
        assert "schwab" in msg["detail"]
