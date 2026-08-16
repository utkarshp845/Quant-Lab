"""Alpaca real-time market-data stream -- backend-only WebSocket client.

Connects to Alpaca's Market Data v2 streaming API on the same "iex"
free-tier feed AlpacaProvider's REST calls already use (see
alpaca_provider.py's DEFAULT_FEED), authenticates with
ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY (read via app.config, exactly
like AlpacaProvider -- these credentials never reach the frontend or
any HTTP/WebSocket response), subscribes to trade + quote updates for
one symbol, and normalizes each incoming message into the existing
LiveQuote model (app/models/market_data.py) via an async callback.

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

Reconnection: run() retries forever with exponential backoff (1s, 2s,
4s, ... capped at 30s) on any connection drop, EXCEPT when the failure
is not going to fix itself on retry -- no credentials configured, or
Alpaca rejects the credentials as invalid -- which are reported as a
fatal "error" status and stop the retry loop, so a bad API key doesn't
spin forever.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import websockets
from websockets.exceptions import WebSocketException

from app import config
from app.models.market_data import LiveQuote

logger = logging.getLogger(__name__)

STREAM_URL_TEMPLATE = "wss://stream.data.alpaca.markets/v2/{feed}"
DEFAULT_FEED = "iex"  # matches alpaca_provider.py's free-tier default

_MIN_BACKOFF_SECONDS = 1
_MAX_BACKOFF_SECONDS = 30
_AUTH_MESSAGES_TO_READ = 5  # bounded so a malformed handshake can't hang forever

StatusCallback = Callable[[str, str | None], Awaitable[None]]
QuoteCallback = Callable[[LiveQuote], Awaitable[None]]


class AlpacaCredentialsMissing(RuntimeError):
    """Fatal, non-retryable: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY
    aren't configured. Same env vars AlpacaProvider reads -- see
    app.config.get_provider_credential."""


class AlpacaAuthRejected(RuntimeError):
    """Fatal, non-retryable: Alpaca's stream itself rejected the API
    key/secret as WRONG (Alpaca error code 402), so a bad key never
    retries forever."""


class AlpacaStreamTransientError(RuntimeError):
    """Retryable: the auth handshake failed for a reason that isn't
    "these credentials are wrong" -- confirmed against a real account
    (see README section 13's testing convention), not assumed: killing
    and restarting this backend process doesn't send Alpaca a clean
    WebSocket close, so Alpaca can keep believing the old connection is
    still open for a short window and reject the new one with
    "connection limit exceeded" (code 406) even though the *credentials*
    are perfectly valid and the very next attempt typically succeeds.
    A login timeout (404) or a "slow client" disconnect (407) are the
    same kind of "try again shortly," not "this key is bad." Only 402
    means the credentials themselves need to change."""


class AlpacaQuoteStream:
    """One upstream connection to Alpaca's streaming API for one symbol.

    run() loops until stop() is called: connect, authenticate,
    subscribe, read messages, normalize, invoke on_quote -- and on any
    non-fatal disconnect, reconnect with backoff. Callers get status
    changes via on_status("connecting" | "connected" | "disconnected" |
    "error", detail).
    """

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
        self.symbol = symbol.upper()
        self._on_quote = on_quote
        self._on_status = on_status
        self._api_key_id = api_key_id or config.get_provider_credential("alpaca", "api_key_id")
        self._api_secret_key = api_secret_key or config.get_provider_credential("alpaca", "api_secret_key")
        self._feed = feed
        self._stopped = False

        # Running state, filled in from whichever message type last
        # updated -- see this module's docstring.
        self._bid: float | None = None
        self._ask: float | None = None
        self._price: float | None = None
        self._volume = 0

    async def run(self) -> None:
        backoff = _MIN_BACKOFF_SECONDS
        while not self._stopped:
            await self._on_status("connecting", None)
            try:
                await self._connect_once()
                backoff = _MIN_BACKOFF_SECONDS  # reset after any clean session
            except (AlpacaCredentialsMissing, AlpacaAuthRejected) as exc:
                await self._on_status("error", str(exc))
                return  # fatal -- retrying would just fail again
            except (AlpacaStreamTransientError, WebSocketException, OSError) as exc:
                if self._stopped:
                    return
                await self._on_status("disconnected", f"{exc}; retrying in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
            except Exception as exc:  # never let an unexpected error kill the hub's task
                if self._stopped:
                    return
                logger.exception("Unexpected error in Alpaca stream for %s", self.symbol)
                await self._on_status("disconnected", f"{exc}; retrying in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

    def stop(self) -> None:
        """Signals run() to stop reconnecting. Does not by itself
        interrupt a blocked recv() -- callers that need that (the hub
        does) also cancel the asyncio.Task running run()."""
        self._stopped = True

    async def _connect_once(self) -> None:
        if not self._api_key_id or not self._api_secret_key:
            raise AlpacaCredentialsMissing(
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
                        raise AlpacaAuthRejected(f"Alpaca rejected the API credentials: {detail}")
                    # Any other auth-stage error (406 connection limit,
                    # 404 login timeout, 407 slow client, ...) is
                    # Alpaca's side of the handshake, not a bad key --
                    # see AlpacaStreamTransientError's docstring for how
                    # this was confirmed against a real account.
                    raise AlpacaStreamTransientError(f"Alpaca auth handshake failed (code {code}): {detail}")
        raise AlpacaStreamTransientError("Alpaca did not confirm authentication within the expected number of messages.")

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
