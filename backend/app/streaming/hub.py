"""Fan-out hub between one upstream Alpaca stream and any number of
connected frontend WebSocket clients.

Why a hub at all, given this feature is scoped to one provider and (for
now) effectively one symbol: Alpaca's streaming API allows only ONE
live connection per API key at a time -- a second concurrent connection
gets rejected or disconnects the first, per Alpaca's published
streaming docs. If every browser tab's WebSocket connection opened its
own upstream AlpacaQuoteStream, two tabs open at once would fight over
that single allowed connection. This hub keeps exactly one upstream
connection alive per symbol no matter how many frontend clients are
watching it, starting it lazily on the first subscriber and tearing it
down once the last one disconnects.

Each frontend client gets its own asyncio.Queue of (status/quote)
events, fed by the shared upstream connection -- see
app/api/market_data_stream.py for the WebSocket route that drains one
of these queues onto the wire.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from app.models.market_data import LiveQuote
from app.streaming.alpaca_stream import AlpacaQuoteStream

StreamFactory = Callable[..., AlpacaQuoteStream]

# One item put on a client queue: either ("status", status, detail) or
# ("quote", quote, None) -- a single 3-tuple shape so the route's
# consumer loop doesn't need to branch on tuple length.
QueueItem = tuple[str, object, object]


class _SymbolHub:
    """Owns the single upstream AlpacaQuoteStream for one symbol."""

    def __init__(self, symbol: str, stream_factory: StreamFactory):
        self.symbol = symbol
        self._stream_factory = stream_factory
        self._clients: set[asyncio.Queue[QueueItem]] = set()
        self._stream: AlpacaQuoteStream | None = None
        self._task: asyncio.Task | None = None
        self._last_status: tuple[str, str | None] = ("connecting", None)
        self._last_quote: LiveQuote | None = None
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[QueueItem]:
        queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        async with self._lock:
            self._clients.add(queue)
            if self._task is None:
                self._start()
        # Replay current state immediately so a newly-connected client
        # sees something without waiting for the next upstream tick --
        # matters most for the second+ tab, joining after the stream is
        # already connected.
        status, detail = self._last_status
        await queue.put(("status", status, detail))
        if self._last_quote is not None:
            await queue.put(("quote", self._last_quote, None))
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[QueueItem]) -> None:
        async with self._lock:
            self._clients.discard(queue)
            if self._clients or self._task is None:
                return
            stream, task = self._stream, self._task
            self._stream = self._task = None
        stream.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _start(self) -> None:
        self._stream = self._stream_factory(
            symbol=self.symbol,
            on_quote=self._on_quote,
            on_status=self._on_status,
        )
        self._task = asyncio.ensure_future(self._stream.run())

    async def _on_status(self, status: str, detail: str | None) -> None:
        self._last_status = (status, detail)
        await self._broadcast(("status", status, detail))

    async def _on_quote(self, quote: LiveQuote) -> None:
        self._last_quote = quote
        await self._broadcast(("quote", quote, None))

    async def _broadcast(self, item: QueueItem) -> None:
        for queue in list(self._clients):
            await queue.put(item)


class AlpacaStreamHub:
    """Lazily creates one _SymbolHub per symbol. A process-wide
    singleton (see `hub` below) -- there is exactly one of these per
    running backend, same as there is exactly one real connection to
    Alpaca per symbol at a time."""

    def __init__(self, stream_factory: StreamFactory = AlpacaQuoteStream):
        self._stream_factory = stream_factory
        self._hubs: dict[str, _SymbolHub] = {}

    def _hub_for(self, symbol: str) -> _SymbolHub:
        symbol = symbol.upper()
        if symbol not in self._hubs:
            self._hubs[symbol] = _SymbolHub(symbol, self._stream_factory)
        return self._hubs[symbol]

    async def subscribe(self, symbol: str) -> asyncio.Queue[QueueItem]:
        return await self._hub_for(symbol).subscribe()

    async def unsubscribe(self, symbol: str, queue: asyncio.Queue[QueueItem]) -> None:
        await self._hub_for(symbol).unsubscribe(queue)


hub = AlpacaStreamHub()
