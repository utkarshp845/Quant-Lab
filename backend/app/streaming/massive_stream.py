"""Massive real-time market-data stream -- backend-only WebSocket client
(v0.1.13), with a free-tier polling fallback (v0.1.15).

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

This file holds three classes, in the order a connection actually
tries them:
  MassiveQuoteStream          -- the real-time WebSocket (above).
  MassivePollingQuoteStream   -- polls Massive's free-tier minute-bar
                                 REST endpoint on a timer instead (see
                                 its own docstring below for why this
                                 exists and what it honestly can't give
                                 you that the WebSocket would).
  MassiveStream                -- what the hub actually registers:
                                 tries the WebSocket first, and only
                                 falls back to polling if the WebSocket
                                 itself is unavailable (confirmed live
                                 against a real account: "connection
                                 limit exceeded"-style transient errors
                                 keep retrying the WebSocket, same as
                                 Alpaca; only a fatal rejection --
                                 wrong key, or this Massive plan not
                                 including websocket access -- triggers
                                 the fallback).

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
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import websockets

from app import config
from app.models.market_data import LiveQuote, MarketBar
from app.providers.massive_provider import MassiveProvider
from app.streaming.base import (
    QuoteCallback,
    QuoteStream,
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


_POLL_INTERVAL_SECONDS = 30
# Mirrors app/api/market_data.py's _VOLUME_LOOKBACK_DAYS in spirit, but
# for a different reason here: querying start=end=today() alone 403s
# outright on this plan (confirmed live -- see
# MassivePollingQuoteStream's docstring), so every poll queries a
# trailing window ending today and takes the last bar in it, the same
# "widen so a weekend/holiday still resolves to the last known
# session's data" shape the REST route's volume enrichment already uses.
_POLL_LOOKBACK_DAYS = 5


class MassivePollingQuoteStream:
    """Polls Massive's free-tier minute-bar REST endpoint
    (`get_historical_data`, the same method `MassiveProvider`'s
    already-confirmed-free bars use -- see massive_provider.py) on a
    timer, instead of a real WebSocket connection. Duck-types the same
    run()/stop()/provider_name interface `ReconnectingQuoteStream`
    provides, but does NOT extend it: there's no persistent connection
    to reconnect, no handshake to authenticate, no message loop to read
    from -- "poll a REST endpoint, emit an update, sleep, repeat" is a
    different enough shape that forcing it into the WebSocket-shaped
    base class would obscure more than it'd share.

    Two things confirmed live against a real account, not assumed --
    one convenient, one a correction to an earlier assumption:

    - The query MUST span more than just today. `start=end=today()`
      returns a flat 403 ("Your plan doesn't include this data
      timeframe") on this plan regardless of whether any bars exist for
      today -- confirmed by hand: querying Sunday 2026-08-16 alone 403'd,
      but 2026-08-14 (the prior Friday) alone returned 200 with real
      data, and a *range* from 2026-08-14 through 2026-08-16 also
      returned 200. So `_latest_bar()` below always queries a trailing
      multi-day window and never a single "just today" day, even though
      that's a slightly heavier request every poll.
    - That successful multi-day response came back with a top-level
      `"status": "DELAYED"` once the range reached into the current/
      recent timeframe (a same-day-only-in-the-past query, like the
      2026-08-14 one, came back `"status": "OK"` instead). This wasn't
      caught until after an earlier version of this docstring claimed
      "no artificial delay" -- that claim was only ever tested against
      a date whose trading session had already fully ended, which
      proves nothing about freshness *during* a live session. Massive's
      own status field says the current-period portion of a response is
      delayed on this plan; this code does not currently know or expose
      by how much (untested outside market hours), and does not claim
      otherwise anywhere in this app.

    Honestly NOT a substitute for a real quote: a bar has open/high/low/
    close/volume, never a bid or ask. LiveQuote.bid/.ask are left None
    here (see LiveQuote's docstring) rather than a bar's close standing
    in for both, which would silently misrepresent the bid-ask spread
    as zero -- exactly the kind of fabricated-looking number this app
    doesn't produce anywhere else.
    """

    provider_name = "massive"

    def __init__(
        self,
        *,
        symbol: str,
        on_quote: QuoteCallback,
        on_status: StatusCallback,
        api_key: str | None = None,
        provider: MassiveProvider | None = None,
        poll_interval_seconds: float = _POLL_INTERVAL_SECONDS,
    ):
        self.symbol = symbol.upper()
        self._on_quote = on_quote
        self._on_status = on_status
        # Injectable, same reason every provider's HTTP client is (see
        # massive_provider.py's module docstring): tests supply a fake
        # provider instead of a real network call.
        self._provider = provider or MassiveProvider(api_key=api_key)
        self._poll_interval = poll_interval_seconds
        self._stopped = False
        self._last_bar_timestamp: datetime | None = None

    async def run(self) -> None:
        await self._on_status("connecting", None)
        announced_connected = False
        while not self._stopped:
            try:
                bar = await asyncio.to_thread(self._latest_bar)
            except RuntimeError as exc:
                # MassiveProvider raises RuntimeError for exactly two
                # reasons (see its _require_credentials/_get): missing
                # credentials, or a 403 entitlement gap -- both are
                # "retrying won't fix this," the same fatal treatment
                # every other stream gives a StreamCredentialsMissing/
                # StreamAuthRejected.
                await self._on_status("error", str(exc))
                return
            except Exception as exc:  # network hiccup, a transient HTTP error, ... -- keep polling
                await self._on_status("disconnected", f"{exc}; retrying in {self._poll_interval:.0f}s")
                await asyncio.sleep(self._poll_interval)
                continue

            if not announced_connected:
                await self._on_status(
                    "connected",
                    f"Polling Massive's free-tier minute bars every {self._poll_interval:.0f}s "
                    "-- no live bid/ask on this plan.",
                )
                announced_connected = True

            if bar is not None and bar.timestamp.value != self._last_bar_timestamp:
                # Only emit when the bar actually changed -- re-sending
                # an identical bar every poll would make "Last update"
                # look like it's refreshing when nothing new actually
                # happened (most bars close once a minute; half of any
                # 30s polls will legitimately see the same latest bar).
                self._last_bar_timestamp = bar.timestamp.value
                await self._on_quote(
                    LiveQuote(
                        symbol=self.symbol,
                        price=bar.close,
                        bid=None,
                        ask=None,
                        volume=bar.volume,
                        timestamp=bar.timestamp.value,
                        provider="massive",
                    )
                )

            await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        self._stopped = True

    def _latest_bar(self) -> MarketBar | None:
        """Runs on a worker thread (see run()'s asyncio.to_thread call)
        -- MassiveProvider's HTTP client is synchronous (httpx.Client,
        shared with the REST routes), and a slow request here must
        never block the event loop the whole hub (all symbols, all
        providers, every connected browser tab) runs on.

        Always queries a trailing multi-day window, never `start=end=
        today()` alone -- see this class's docstring for why that
        specific query 403s outright on this plan, confirmed live."""
        end = date.today()
        start = end - timedelta(days=_POLL_LOOKBACK_DAYS)
        bars = self._provider.get_historical_data(
            symbol=self.symbol, start=start, end=end, multiplier=1, timespan="minute"
        )
        return bars[-1] if bars else None


class MassiveStream:
    """What app/streaming/hub.py actually registers for "massive" (see
    hub.py's STREAM_FACTORIES). Tries the real WebSocket
    (MassiveQuoteStream) first -- including its normal retry/backoff on
    a transient failure, e.g. the same "connection limit exceeded"
    collision Alpaca can hit -- and only switches to polling
    (MassivePollingQuoteStream) once the WebSocket attempt ends with a
    FATAL error (wrong credentials, or this plan not including
    websocket access). A fresh MassiveStream (a new hub entry, e.g.
    after every client disconnects and a new one subscribes later)
    always tries the WebSocket again first, so upgrading the Massive
    plan starts working again with no code change and no manual reset
    -- it just needs a new connection attempt, which happens naturally.

    The WebSocket attempt's own "error" status is intercepted rather
    than relayed to the caller directly: MassiveQuoteStream.run()
    (inherited from ReconnectingQuoteStream) reports "error" itself
    right before returning on a fatal exception, and relaying that
    as-is would flash a misleading "Error" in the UI for one tick before
    flipping to polling's "Connected" -- this class holds onto that
    detail instead and folds it into the "falling back to polling"
    status message that follows.

    Known, accepted tradeoff: MassiveQuoteStream can also report a
    non-fatal "error" while the WebSocket connection stays open (e.g. a
    rejected subscription -- see its _handle_message()). This wrapper
    intercepts that too, since it can't distinguish "fatal, about to
    return" from "non-fatal, still running" at the moment the status
    arrives -- so that specific in-connection error notice doesn't
    reach the UI when going through MassiveStream (it did when using
    MassiveQuoteStream directly). Accepted rather than engineered
    around because it's a rare edge case with no observed real-account
    trigger, versus real complexity to fix cleanly.
    """

    provider_name = "massive"

    def __init__(
        self,
        *,
        symbol: str,
        on_quote: QuoteCallback,
        on_status: StatusCallback,
        api_key: str | None = None,
        ws_stream_factory: Callable[..., QuoteStream] = MassiveQuoteStream,
        poll_stream_factory: Callable[..., QuoteStream] = MassivePollingQuoteStream,
    ):
        self.symbol = symbol.upper()
        self._on_status = on_status
        self._stopped = False
        self._last_ws_error: str | None = None

        async def _ws_on_status(status: str, detail: str | None) -> None:
            if status == "error":
                self._last_ws_error = detail
                return
            await self._on_status(status, detail)

        # Both factories injectable -- same idea as hub.py's own
        # stream_factory param -- so tests can drive the WS-then-
        # polling-fallback decision with fakes instead of a real
        # network connection or real Massive credentials.
        self._ws_stream = ws_stream_factory(symbol=symbol, on_quote=on_quote, on_status=_ws_on_status, api_key=api_key)
        self._poll_stream = poll_stream_factory(symbol=symbol, on_quote=on_quote, on_status=on_status, api_key=api_key)

    async def run(self) -> None:
        await self._ws_stream.run()
        if self._stopped:
            return
        # _ws_stream.run() only returns without self._stopped being
        # true when the WebSocket hit a fatal error -- see this class's
        # docstring.
        await self._on_status(
            "connecting",
            f"Real-time WebSocket unavailable ({self._last_ws_error}) -- "
            "falling back to polling Massive's free-tier minute bars.",
        )
        await self._poll_stream.run()

    def stop(self) -> None:
        self._stopped = True
        self._ws_stream.stop()
        self._poll_stream.stop()
