"""Massive real-time market-data stream -- backend-only WebSocket client
(v0.1.13).

Mirrors alpaca_stream.py's shape exactly (see that file for the "why
this shape" reasoning and ReconnectingQuoteStream in
app/streaming/base.py for the shared reconnect/backoff loop both
providers use) -- only the protocol details differ here: connection
URL, auth message, message field names, and timestamp units.

Connects to Massive's real-time stocks WebSocket, authenticates with
MASSIVE_API_KEY (read via app.config, exactly like MassiveProvider --
never reaches the frontend), subscribes to trade + quote updates for
one symbol, and normalizes each incoming message into the existing
LiveQuote model.

Protocol reference (confirmed, not guessed, against massive.com's own
docs and the official massive-com/client-python source -- specifically
massive/websocket/__init__.py and massive/websocket/models/common.py,
since the docs pages themselves are mostly JS-rendered and didn't
expose the wire format directly):
    wss://socket.massive.com/stocks   (real-time; wss://delayed.massive.com/stocks
                                        is the 15-minute-delayed alternative,
                                        not used here)
    <- server sends [{"ev": "status", "status": "connected", "message": "..."}]
    -> client sends {"action": "auth", "params": "<MASSIVE_API_KEY>"}
    <- server sends [{"ev": "status", "status": "auth_success", "message": "..."}]
       (or [{"ev": "status", "status": "auth_failed", "message": "..."}] if
       the key is wrong -- confirmed as the ONLY status literal the
       official client itself checks for failure; anything else is
       treated as the handshake proceeding)
    -> client sends {"action": "subscribe", "params": "T.TSLA,Q.TSLA"}
    <- server sends [{"ev": "status", "status": "success", "message": "subscribed to: ..."}]
    <- server then pushes one JSON array per frame, each element tagged
       by "ev":
         "Q" (quote): bp/ap bid/ask price, t timestamp (Unix MILLISECONDS)
         "T" (trade): p price, s size, t timestamp (Unix MILLISECONDS)
         "status": anything post-subscribe that isn't "success" is
                    reported (non-fatal) the same way Alpaca's post-auth
                    "error" messages are

Real-time WebSocket access is a paid-plan feature on Massive (confirmed:
massive.com/docs/websocket/overview states "WebSockets are available
through all paid subscriptions" -- and this app's own MassiveProvider
already found /v2/last/nbbo and /v2/last/trade, the REST equivalent of
real-time quotes, come back 403 NOT_AUTHORIZED on a free plan; see
massive_provider.py's module docstring). If the account isn't entitled,
Massive is expected to reject the auth (an "auth_failed" status, or a
connection close before one arrives) -- StreamAuthRejected either way,
surfaced honestly as the "error" status in the UI rather than silently
retrying forever against a plan limitation retrying can't fix.

Timestamp units, confirmed and easy to get backwards: this WebSocket's
"t" field is Unix MILLISECONDS on both quote and trade messages --
different from MassiveProvider's REST /v2/last/nbbo and /v2/last/trade,
whose "t" is Unix NANOSECONDS (see massive_provider.py's module
docstring for that endpoint's own units). Each is converted with its
own explicitly-named helper; nothing here reuses REST's `_from_unix_ns`
for a millisecond value.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import websockets

from app import config
from app.models.market_data import LiveQuote
from app.streaming.base import (
    QuoteCallback,
    ReconnectingQuoteStream,
    StatusCallback,
    StreamAuthRejected,
    StreamCredentialsMissing,
    StreamTransientError,
)

STREAM_URL = "wss://socket.massive.com/stocks"

_AUTH_MESSAGES_TO_READ = 5  # bounded so a malformed handshake can't hang forever


class MassiveQuoteStream(ReconnectingQuoteStream):
    """One upstream connection to Massive's streaming API for one
    symbol. See ReconnectingQuoteStream (app/streaming/base.py) for the
    reconnect/backoff loop and status-callback contract this class
    plugs into.
    """

    provider_name = "massive"

    def __init__(
        self,
        *,
        symbol: str,
        on_quote: QuoteCallback,
        on_status: StatusCallback,
        api_key: str | None = None,
    ):
        super().__init__(symbol=symbol, on_quote=on_quote, on_status=on_status)
        self._api_key = api_key or config.get_provider_credential("massive", "api_key")

        # Running state, filled in from whichever message type last
        # updated -- see alpaca_stream.py's module docstring for why
        # quote and trade are tracked separately and merged.
        self._bid: float | None = None
        self._ask: float | None = None
        self._price: float | None = None
        self._volume = 0

    async def _connect_once(self) -> None:
        if not self._api_key:
            raise StreamCredentialsMissing("MASSIVE_API_KEY must be set for live streaming.")

        async with websockets.connect(STREAM_URL, ping_interval=20, ping_timeout=20) as ws:
            await self._authenticate(ws)
            await self._subscribe(ws)
            await self._on_status("connected", None)

            async for raw in ws:
                if self._stopped:
                    return
                for msg in json.loads(raw):
                    await self._handle_message(msg)

    async def _authenticate(self, ws) -> None:
        await ws.send(json.dumps({"action": "auth", "params": self._api_key}))
        # Massive sends a "connected" status immediately on open, then a
        # separate "auth_success"/"auth_failed" status after the auth
        # message -- read until one of those appears. "auth_failed" is
        # the one literal the official client itself checks for
        # failure (see this module's docstring); treat any other
        # status as the handshake proceeding rather than guessing at
        # every possible success string Massive might send.
        for _ in range(_AUTH_MESSAGES_TO_READ):
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            for msg in json.loads(raw):
                if msg.get("ev") != "status":
                    continue
                status = msg.get("status")
                if status == "auth_failed":
                    raise StreamAuthRejected(f"Massive rejected the API key: {msg.get('message')}")
                if status == "auth_success":
                    return
        raise StreamTransientError("Massive did not confirm authentication within the expected number of messages.")

    async def _subscribe(self, ws) -> None:
        await ws.send(json.dumps({"action": "subscribe", "params": f"T.{self.symbol},Q.{self.symbol}"}))

    async def _handle_message(self, msg: dict) -> None:
        event = msg.get("ev")
        timestamp = msg.get("t")

        if event == "Q":
            self._bid = msg.get("bp", self._bid)
            self._ask = msg.get("ap", self._ask)
        elif event == "T":
            self._price = msg.get("p", self._price)
            self._volume += msg.get("s", 0)
        elif event == "status":
            if msg.get("status") not in (None, "success"):
                # Non-fatal here: e.g. a rejected subscription, a plan
                # entitlement notice. The connection itself stays open
                # (Massive doesn't close it for this); report it but
                # keep going, same as Alpaca's post-auth "error" messages.
                await self._on_status("error", f"Massive stream status: {msg.get('message')}")
            return
        else:
            return  # an event type this app doesn't consume yet

        if self._bid is None or self._ask is None:
            return  # not enough data yet to publish a quote

        await self._on_quote(
            LiveQuote(
                symbol=self.symbol,
                price=self._price,
                bid=self._bid,
                ask=self._ask,
                volume=self._volume or None,
                timestamp=_from_unix_ms(timestamp),
                provider="massive",
            )
        )


def _from_unix_ms(t_ms: int | None) -> datetime:
    """This WebSocket's "t" is Unix MILLISECONDS (confirmed -- see this
    module's docstring); never raises -- a missing or malformed
    timestamp falls back to "now" rather than dropping an otherwise-
    good quote update, same convention as alpaca_stream's timestamp
    parser."""
    if isinstance(t_ms, (int, float)):
        return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc)
    return datetime.now(timezone.utc)
