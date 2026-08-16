"""Tests for app/streaming/massive_stream.py -- message normalization,
timestamp parsing, and the fatal-vs-retryable distinction in
_authenticate(), all without ever opening a real network connection.

Mirrors test_alpaca_stream.py's structure -- see that file for why
these are plain asyncio.run() tests rather than pytest-asyncio (not a
dependency of this project).
"""

import asyncio
import json
from datetime import timezone

import pytest

from app.models.market_data import LiveQuote
from app.streaming.base import StreamAuthRejected, StreamCredentialsMissing, StreamTransientError
from app.streaming.massive_stream import MassiveQuoteStream, _from_unix_ms


def _make_stream(**overrides) -> tuple[MassiveQuoteStream, list[LiveQuote], list[tuple[str, str | None]]]:
    quotes: list[LiveQuote] = []
    statuses: list[tuple[str, str | None]] = []

    async def on_quote(q: LiveQuote) -> None:
        quotes.append(q)

    async def on_status(status: str, detail: str | None) -> None:
        statuses.append((status, detail))

    kwargs = dict(symbol="TSLA", on_quote=on_quote, on_status=on_status, api_key="fake")
    kwargs.update(overrides)
    return MassiveQuoteStream(**kwargs), quotes, statuses


class TestTimestampParsing:
    def test_unix_milliseconds_are_converted_to_utc_datetime(self):
        # 2026-08-14T20:00:00Z in Unix ms (verified via datetime.timestamp(), not hand-computed)
        dt = _from_unix_ms(1786737600000)
        assert dt.tzinfo == timezone.utc
        assert dt.year == 2026 and dt.month == 8 and dt.day == 14 and dt.hour == 20

    def test_missing_value_falls_back_to_now_rather_than_raising(self):
        dt = _from_unix_ms(None)
        assert dt.tzinfo == timezone.utc

    def test_non_numeric_value_falls_back_to_now_rather_than_raising(self):
        dt = _from_unix_ms("not-a-timestamp")
        assert dt.tzinfo == timezone.utc


class TestMessageNormalization:
    def test_bid_and_ask_together_publish_a_quote(self):
        stream, quotes, _ = _make_stream()
        asyncio.run(stream._handle_message({"ev": "Q", "sym": "TSLA", "bp": 340.0, "ap": 340.5, "t": 1786737600000}))
        assert len(quotes) == 1
        q = quotes[0]
        assert q.symbol == "TSLA"
        assert q.bid == 340.0
        assert q.ask == 340.5
        assert q.price is None  # no trade seen yet
        assert q.volume is None  # no trade size accumulated yet
        assert q.provider == "massive"

    def test_trade_after_quote_fills_in_price_and_accumulates_volume(self):
        stream, quotes, _ = _make_stream()

        async def go():
            await stream._handle_message({"ev": "Q", "bp": 340.0, "ap": 340.5, "t": 1786737600000})
            await stream._handle_message({"ev": "T", "p": 340.2, "s": 100, "t": 1786737601000})
            await stream._handle_message({"ev": "T", "p": 340.3, "s": 50, "t": 1786737602000})

        asyncio.run(go())

        assert len(quotes) == 3
        last = quotes[-1]
        assert last.price == 340.3
        assert last.volume == 150  # 100 + 50 -- cumulative since this connection, not session volume
        assert last.bid == 340.0 and last.ask == 340.5  # carried forward from the last quote message

    def test_trade_before_any_quote_does_not_publish(self):
        stream, quotes, _ = _make_stream()
        asyncio.run(stream._handle_message({"ev": "T", "p": 340.2, "s": 100, "t": 1786737600000}))
        assert quotes == []

    def test_successful_subscribe_status_is_ignored(self):
        stream, quotes, statuses = _make_stream()
        asyncio.run(stream._handle_message({"ev": "status", "status": "success", "message": "subscribed to: T.TSLA,Q.TSLA"}))
        assert quotes == []
        assert statuses == []

    def test_other_post_connect_status_is_reported_but_not_fatal_by_itself(self):
        stream, quotes, statuses = _make_stream()
        asyncio.run(stream._handle_message({"ev": "status", "status": "max_connections", "message": "exceeded max connections"}))
        assert quotes == []
        assert statuses == [("error", "Massive stream status: exceeded max connections")]

    def test_unrecognized_event_type_is_ignored(self):
        stream, quotes, statuses = _make_stream()
        asyncio.run(stream._handle_message({"ev": "A", "sym": "TSLA"}))  # per-second aggregate, not consumed
        assert quotes == []
        assert statuses == []


class TestMissingCredentialsIsFatalNotRetried:
    def test_run_reports_error_once_and_returns_without_looping(self, monkeypatch):
        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
        stream, quotes, statuses = _make_stream(api_key=None)

        asyncio.run(asyncio.wait_for(stream.run(), timeout=5))

        assert statuses == [("connecting", None), ("error", statuses[-1][1])]
        assert "MASSIVE_API_KEY" in statuses[-1][1]
        assert quotes == []

    def test_connect_once_raises_before_touching_the_network(self):
        stream, _, _ = _make_stream(api_key=None)
        with pytest.raises(StreamCredentialsMissing):
            asyncio.run(stream._connect_once())


class _FakeWebSocket:
    """Stands in for the `ws` object _authenticate() sends to and reads
    from -- just enough of websockets' interface (async send/recv) to
    drive _authenticate() without a real socket."""

    def __init__(self, frames: list[list[dict]]):
        self._frames = [json.dumps(frame) for frame in frames]
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        return self._frames.pop(0)


class TestAuthStatusClassification:
    """Confirmed against the official massive-com/client-python source
    (see this module's docstring): "auth_failed" is the one status the
    official client itself treats as failure. Everything else (a plain
    "connected" greeting, "auth_success") is the handshake proceeding."""

    def test_auth_failed_is_fatal(self):
        stream, _, _ = _make_stream()
        ws = _FakeWebSocket([[{"ev": "status", "status": "auth_failed", "message": "invalid api key"}]])
        with pytest.raises(StreamAuthRejected, match="invalid api key"):
            asyncio.run(stream._authenticate(ws))

    def test_connected_then_auth_success_returns_cleanly(self):
        stream, _, _ = _make_stream()
        ws = _FakeWebSocket(
            [
                [{"ev": "status", "status": "connected", "message": "Connected Successfully"}],
                [{"ev": "status", "status": "auth_success", "message": "authenticated"}],
            ]
        )
        asyncio.run(stream._authenticate(ws))  # does not raise

    def test_no_confirmation_within_the_read_bound_is_transient(self):
        stream, _, _ = _make_stream()
        ws = _FakeWebSocket([[{"ev": "status", "status": "connected", "message": "Connected Successfully"}]] * 5)
        with pytest.raises(StreamTransientError):
            asyncio.run(stream._authenticate(ws))

    def test_a_real_account_reporting_a_plan_entitlement_gap_is_still_classified_as_auth_rejected(self):
        """This app's own MassiveProvider already found real-time REST
        quotes 403 on the account's current plan (see
        massive_provider.py); if the WebSocket similarly rejects an
        otherwise-valid key for lacking a paid plan, Massive is
        expected to report it through the same "auth_failed" status
        this test drives -- there is no separate "entitlement" status
        to special-case, per the official client source."""
        stream, _, _ = _make_stream()
        ws = _FakeWebSocket([[{"ev": "status", "status": "auth_failed", "message": "not entitled to real-time data on your plan"}]])
        with pytest.raises(StreamAuthRejected, match="not entitled"):
            asyncio.run(stream._authenticate(ws))


class TestSubscribeMessage:
    def test_subscribes_to_trades_and_quotes_for_the_symbol(self):
        stream, _, _ = _make_stream(symbol="tsla")  # lowercase in -- normalized on the way out
        ws = _FakeWebSocket([])
        asyncio.run(stream._subscribe(ws))
        assert json.loads(ws.sent[0]) == {"action": "subscribe", "params": "T.TSLA,Q.TSLA"}
