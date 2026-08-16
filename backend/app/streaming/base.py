"""Shared reconnect/backoff loop for real-time quote streams.

Extracted from AlpacaQuoteStream (v0.1.12) when MassiveQuoteStream
(v0.1.13) needed the exact same "connect, authenticate, subscribe,
read messages, reconnect with backoff, tell fatal errors from
retryable ones apart" shape -- the only thing that differs between
providers is protocol detail (auth message format, message field
names, timestamp units), all of which lives in `_connect_once()`.
Duplicating the loop itself per provider would have meant the same
backoff bug (see the Alpaca "connection limit exceeded" fix, v0.1.12 ->
README section 14) needing to be found and fixed twice.

Every provider raises the same three exceptions below to tell this
loop what happened -- they are intentionally provider-agnostic (not
"AlpacaAuthRejected" / "MassiveAuthRejected") since the loop's handling
of them doesn't depend on which provider raised them:

    StreamCredentialsMissing -- fatal. No API key/secret configured at
        all; retrying can't fix a missing environment variable.
    StreamAuthRejected       -- fatal. The provider itself confirmed
        the credentials are wrong (not "try again," genuinely wrong).
    StreamTransientError     -- retryable. Anything else that kept the
        connection from completing -- a connection-limit collision, a
        login timeout, a slow-client disconnect, an entitlement check
        that might succeed differently next time. See each provider's
        module docstring for what it specifically maps to this.

Anything else (a raw WebSocketException, OSError, or a genuinely
unexpected exception) is also treated as retryable -- a stream should
never go silently dead from a transient network hiccup.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Protocol, runtime_checkable

from app.models.market_data import LiveQuote
from websockets.exceptions import WebSocketException

logger = logging.getLogger(__name__)

_MIN_BACKOFF_SECONDS = 1
_MAX_BACKOFF_SECONDS = 30

StatusCallback = Callable[[str, str | None], Awaitable[None]]
QuoteCallback = Callable[[LiveQuote], Awaitable[None]]


class StreamCredentialsMissing(RuntimeError):
    """Fatal, non-retryable: no API credentials configured for this
    provider at all."""


class StreamAuthRejected(RuntimeError):
    """Fatal, non-retryable: the provider itself confirmed the
    credentials are wrong."""


class StreamTransientError(RuntimeError):
    """Retryable: the connection attempt failed for a reason that
    isn't "these credentials are wrong" -- see the raising provider's
    module docstring for what specifically maps here and why."""


@runtime_checkable
class QuoteStream(Protocol):
    """Structural contract every stream app/streaming/hub.py's
    STREAM_FACTORIES registers must satisfy: async run() (loops until
    stop() is called), stop() (signals it to end), and a provider_name
    attribute. Deliberately NOT "must inherit ReconnectingQuoteStream"
    -- MassiveStream (app/streaming/massive_stream.py) satisfies this
    by composition instead, since its WebSocket-then-polling-fallback
    shape isn't itself WebSocket-shaped the way ReconnectingQuoteStream
    assumes. Every ReconnectingQuoteStream subclass already satisfies
    this Protocol for free (run/stop/provider_name are all present).
    """

    provider_name: str

    async def run(self) -> None: ...

    def stop(self) -> None: ...


class ReconnectingQuoteStream(ABC):
    """One upstream connection to a provider's streaming API for one
    symbol, with reconnect/backoff already handled. Subclasses
    implement `_connect_once()`: connect, authenticate, subscribe,
    read messages until the connection drops, calling `self._on_quote`
    for each normalized update and raising one of this module's three
    exceptions (or letting a network error propagate) to report why the
    attempt ended.

    run() loops until stop() is called. Callers get status changes via
    on_status("connecting" | "connected" | "disconnected" | "error",
    detail) -- "error" only for a fatal (non-retried) exit; every
    retried failure is reported as "disconnected" with a "retrying in
    Ns" detail, exactly like a plain network drop, since from a caller's
    perspective both look the same: not connected right now, but this
    isn't the end.
    """

    def __init__(self, *, symbol: str, on_quote: QuoteCallback, on_status: StatusCallback):
        self.symbol = symbol.upper()
        self._on_quote = on_quote
        self._on_status = on_status
        self._stopped = False

    async def run(self) -> None:
        backoff = _MIN_BACKOFF_SECONDS
        while not self._stopped:
            await self._on_status("connecting", None)
            try:
                await self._connect_once()
                backoff = _MIN_BACKOFF_SECONDS  # reset after any clean session
            except (StreamCredentialsMissing, StreamAuthRejected) as exc:
                await self._on_status("error", str(exc))
                return  # fatal -- retrying would just fail again
            except (StreamTransientError, WebSocketException, OSError) as exc:
                if self._stopped:
                    return
                await self._on_status("disconnected", f"{exc}; retrying in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
            except Exception as exc:  # never let an unexpected error kill the hub's task
                if self._stopped:
                    return
                logger.exception("Unexpected error in %s stream for %s", self.provider_name, self.symbol)
                await self._on_status("disconnected", f"{exc}; retrying in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

    def stop(self) -> None:
        """Signals run() to stop reconnecting. Does not by itself
        interrupt a blocked recv() -- callers that need that (the hub
        does) also cancel the asyncio.Task running run()."""
        self._stopped = True

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Lowercase provider name, e.g. "alpaca" -- used in log
        messages and as LiveQuote.provider."""

    @abstractmethod
    async def _connect_once(self) -> None:
        """One connection attempt: connect, authenticate, subscribe,
        read messages until the connection ends. Raise
        StreamCredentialsMissing/StreamAuthRejected for a fatal
        failure, StreamTransientError for a retryable one, or let a
        network exception propagate (also retryable)."""
