"""Alpaca real-time market-data stream -- backend-only WebSocket client.

Connects to Alpaca's Market Data v2 streaming API on the same "iex"
free-tier feed AlpacaProvider's REST calls already use (see
alpaca_provider.py's DEFAULT_FEED), authenticates with
ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY (read via app.config, exactly
like AlpacaProvider -- these credentials never reach the frontend or
any HTTP/WebSocket response), subscribes to trade + quote updates for
one symbol, and normalizes each incoming message into the existing
LiveQuote model (app/models/market_data.py) via an async callback.

The reconnect/backoff loop itself lives in ReconnectingQuoteStream
(app/streaming/base.py), shared with MassiveQuoteStream (v0.1.13) --
this class only implements the Alpaca-specific handshake and message
shapes.

Protocol reference (Alpaca's published streaming docs, not guessed):
    wss://stream.data.alpaca.markets/v2/{feed}
    -> client sends {"action": "auth", "key": ..., "secret": ...}
    <- server sends [{"T": "success", "msg": "connected"}]
    <- server sends [{"T": "success", "msg": "authenticated"}]
       (or [{"T": "error", "code": ..., "msg": ...}] if the credentials
       are wrong)
    -> client sends {"action": "subscribe", "trades": [symbol], "quotes": [symbol]}
    <- server sends [{"T": "subscription", "trades": [...], "quotes": [...]}]
    <- server then pushes one JSON array per frame, each element tagged
       by "T":
         "q" (quote): bp/ap bid/ask price, t timestamp
         "t" (trade): p price, s size, t timestamp
         "error": something went wrong after auth (e.g. a bad symbol)

Quote vs. trade: Alpaca reports bid/ask and last-trade-price as two
separate message types, the same split AlpacaProvider's REST
get_latest_quote() already combines from two separate endpoints. This
module keeps running bid/ask/price state per connection and emits a
LiveQuote on every update to either half, once both a bid and an ask
have been seen at least once.

Volume caveat (deliberately not hidden): the value put in LiveQuote.volume
here is the sum of trade sizes seen SINCE THIS CONNECTION STARTED, not
the exchange's cumulative session volume -- Alpaca's real-time trade
messages don't carry a running daily total, only each individual
trade's own size. This is honestly a different (and initially much
smaller) number than the REST route's LiveQuote.volume, which does a
best-effort historical-bar lookup for the true session total (see
app/api/market_data.py's _best_effort_latest_volume). Both are labeled
the same field name on purpose (same model), but a caller comparing the
two should not expect them to agree, especially right after connecting.

Fatal-vs-retryable classification (v0.1.12, confirmed against a real
account -- see README section 14): only Alpaca error code 402 ("auth
failed", the key/secret itself is wrong) is StreamAuthRejected (fatal).
Every other auth-stage error -- 406 connection limit, 404 login
timeout, 407 slow client -- is StreamTransientError (retried with
backoff), because restarting this backend doesn't send Alpaca a clean
WebSocket close, so Alpaca can keep treating the previous connection as
live for a short window and reject the new one with "connection limit
exceeded" even though the credentials are completely valid.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

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

STREAM_URL_TEMPLATE = "wss://stream.data.alpaca.markets/v2/{feed}"
DEFAULT_FEED = "iex"  # matches alpaca_provider.py's free-tier default

_AUTH_MESSAGES_TO_READ = 5  # bounded so a malformed handshake can't hang forever


class AlpacaQuoteStream(ReconnectingQuoteStream):
    """One upstream connection to Alpaca's streaming API for one symbol.
    See ReconnectingQuoteStream (app/streaming/base.py) for the
    reconnect/backoff loop and status-callback contract this class
    plugs into.
    """

    provider_name = "alpaca"

    def __init__(
        self,
        *,
        symbol: str,
        on_quote: QuoteCallback,
        on_status: StatusCallback,
        api_key_id: str | None = None,
        api_secret_key: str | None = None,
        feed: str = DEFAULT_FEED,
    ):
        super().__init__(symbol=symbol, on_quote=on_quote, on_status=on_status)
        self._api_key_id = api_key_id or config.get_provider_credential("alpaca", "api_key_id")
        self._api_secret_key = api_secret_key or config.get_provider_credential("alpaca", "api_secret_key")
        self._feed = feed

        # Running state, filled in from whichever message type last
        # updated -- see this module's docstring.
        self._bid: float | None = None
        self._ask: float | None = None
        self._price: float | None = None
        self._volume = 0

    async def _connect_once(self) -> None:
        if not self._api_key_id or not self._api_secret_key:
            raise StreamCredentialsMissing(
                "ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY must be set for live streaming."
            )

        url = STREAM_URL_TEMPLATE.format(feed=self._feed)
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            await self._authenticate(ws)
            await self._subscribe(ws)
            await self._on_status("connected", None)

            async for raw in ws:
                if self._stopped:
                    return
                for msg in json.loads(raw):
                    await self._handle_message(msg)

    async def _authenticate(self, ws) -> None:
        await ws.send(json.dumps({"action": "auth", "key": self._api_key_id, "secret": self._api_secret_key}))
        # Alpaca sends a "connected" success message immediately on
        # open, then a separate "authenticated" success (or "error")
        # after the auth message -- read until one of those appears.
        for _ in range(_AUTH_MESSAGES_TO_READ):
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            for msg in json.loads(raw):
                if msg.get("T") == "success" and msg.get("msg") == "authenticated":
                    return
                if msg.get("T") == "error":
                    code = msg.get("code")
                    detail = msg.get("msg")
                    if code == 402:  # "auth failed" -- the key/secret itself is wrong
                        raise StreamAuthRejected(f"Alpaca rejected the API credentials: {detail}")
                    # Any other auth-stage error (406 connection limit,
                    # 404 login timeout, 407 slow client, ...) is
                    # Alpaca's side of the handshake, not a bad key --
                    # see this module's docstring for how this was
                    # confirmed against a real account.
                    raise StreamTransientError(f"Alpaca auth handshake failed (code {code}): {detail}")
        raise StreamTransientError("Alpaca did not confirm authentication within the expected number of messages.")

    async def _subscribe(self, ws) -> None:
        await ws.send(json.dumps({"action": "subscribe", "trades": [self.symbol], "quotes": [self.symbol]}))

    async def _handle_message(self, msg: dict) -> None:
        msg_type = msg.get("T")
        timestamp = msg.get("t")

        if msg_type == "q":
            self._bid = msg.get("bp", self._bid)
            self._ask = msg.get("ap", self._ask)
        elif msg_type == "t":
            self._price = msg.get("p", self._price)
            self._volume += msg.get("s", 0)
        elif msg_type == "error":
            # Non-fatal here: e.g. a rejected subscription request.
            # The connection itself stays open; report it but keep going.
            await self._on_status("error", f"Alpaca stream error: {msg.get('msg')}")
            return
        else:
            return  # subscription confirmations etc. -- nothing to normalize

        if self._bid is None or self._ask is None:
            return  # not enough data yet to publish a quote

        await self._on_quote(
            LiveQuote(
                symbol=self.symbol,
                price=self._price,
                bid=self._bid,
                ask=self._ask,
                volume=self._volume or None,
                timestamp=_parse_alpaca_timestamp(timestamp),
                provider="alpaca",
            )
        )


_TIMESTAMP_RE = re.compile(
    r"^(?P<base>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<frac>\d+))?"
    r"(?P<offset>Z|[+-]\d{2}:\d{2})?$"
)


def _parse_alpaca_timestamp(value: str | None) -> datetime:
    """Alpaca sends RFC-3339 timestamps with nanosecond precision (9
    fractional digits), which datetime.fromisoformat cannot parse
    directly (it wants at most 6, i.e. microseconds). Never raises --
    an unparseable or missing timestamp falls back to "now" rather
    than dropping an otherwise-good quote update."""
    if value:
        match = _TIMESTAMP_RE.match(value)
        if match:
            micros = int((match.group("frac") or "0")[:6].ljust(6, "0"))
            dt = datetime.fromisoformat(match.group("base")).replace(microsecond=micros)
            offset = match.group("offset")
            if offset in (None, "Z"):
                return dt.replace(tzinfo=timezone.utc)
            sign = 1 if offset[0] == "+" else -1
            hours, minutes = offset[1:].split(":")
            return dt.replace(tzinfo=timezone(sign * timedelta(hours=int(hours), minutes=int(minutes))))
    return datetime.now(timezone.utc)
