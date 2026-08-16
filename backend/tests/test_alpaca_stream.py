"""Tests for app/streaming/alpaca_stream.py -- message normalization,
timestamp parsing, and the fatal-vs-retryable distinction in run(), all
without ever opening a real network connection.

No pytest-asyncio in this project's dependencies (see requirements.txt)
-- these tests drive the async pieces with plain asyncio.run(), same
approach as any other async unit test that doesn't need a running event
loop across multiple test steps.
"""

import asyncio
import json
from datetime import timezone

import pytest

from app.models.market_data import LiveQuote
from app.streaming.alpaca_stream import (
    AlpacaAuthRejected,
    AlpacaCredentialsMissing,
    AlpacaQuoteStream,
    AlpacaStreamTransientError,
    _parse_alpaca_timestamp,
)


def _make_stream(**overrides) -> tuple[AlpacaQuoteStream, list[LiveQuote], list[tuple[str, str | None]]]:
    quotes: list[LiveQuote] = []
    statuses: list[tuple[str, str | None]] = []

    async def on_quote(q: LiveQuote) -> None:
        quotes.append(q)

    async def on_status(status: str, detail: str | None) -> None:
        statuses.append((status, detail))

    kwargs = dict(symbol="TSLA", on_quote=on_quote, on_status=on_status, api_key_id="fake", api_secret_key="fake")
    kwargs.update(overrides)
    return AlpacaQuoteStream(**kwargs), quotes, statuses


class TestTimestampParsing:
    def test_nanosecond_precision_is_truncated_to_microseconds(self):
        dt = _parse_alpaca_timestamp("2026-08-14T20:00:00.123456789Z")
        assert dt.microsecond == 123456
        assert dt.tzinfo == timezone.utc
        assert dt.year == 2026 and dt.hour == 20

    def test_no_fractional_seconds_still_parses(self):
        dt = _parse_alpaca_timestamp("2026-08-14T20:00:00Z")
        assert dt.microsecond == 0
        assert dt.tzinfo == timezone.utc

    def test_missing_value_falls_back_to_now_rather_than_raising(self):
        dt = _parse_alpaca_timestamp(None)
        assert dt.tzinfo == timezone.utc

    def test_garbage_value_falls_back_to_now_rather_than_raising(self):
        dt = _parse_alpaca_timestamp("not-a-timestamp")
        assert dt.tzinfo == timezone.utc


class TestMessageNormalization:
    def test_bid_and_ask_together_publish_a_quote(self):
        stream, quotes, _ = _make_stream()
        asyncio.run(stream._handle_message({"T": "q", "bp": 340.0, "ap": 340.5, "t": "2026-08-15T20:00:00Z"}))
        assert len(quotes) == 1
        q = quotes[0]
        assert q.symbol == "TSLA"
        assert q.bid == 340.0
        assert q.ask == 340.5
        assert q.price is None  # no trade seen yet
        assert q.volume is None  # no trade size accumulated yet
        assert q.provider == "alpaca"

    def test_trade_after_quote_fills_in_price_and_accumulates_volume(self):
        stream, quotes, _ = _make_stream()

        async def go():
            await stream._handle_message({"T": "q", "bp": 340.0, "ap": 340.5, "t": "2026-08-15T20:00:00Z"})
            await stream._handle_message({"T": "t", "p": 340.2, "s": 100, "t": "2026-08-15T20:00:01Z"})
            await stream._handle_message({"T": "t", "p": 340.3, "s": 50, "t": "2026-08-15T20:00:02Z"})

        asyncio.run(go())

        assert len(quotes) == 3  # a quote is emitted on every update, including trade-only ones
        last = quotes[-1]
        assert last.price == 340.3
        assert last.volume == 150  # 100 + 50 -- cumulative since this connection, not session volume
        assert last.bid == 340.0 and last.ask == 340.5  # carried forward from the last quote message

    def test_trade_before_any_quote_does_not_publish(self):
        stream, quotes, _ = _make_stream()
        asyncio.run(stream._handle_message({"T": "t", "p": 340.2, "s": 100, "t": "2026-08-15T20:00:01Z"}))
        assert quotes == []  # no bid/ask yet -- nothing normalizable to a LiveQuote

    def test_subscription_confirmation_is_ignored(self):
        stream, quotes, statuses = _make_stream()
        asyncio.run(stream._handle_message({"T": "subscription", "trades": ["TSLA"], "quotes": ["TSLA"]}))
        assert quotes == []
        assert statuses == []

    def test_post_auth_error_message_is_reported_but_not_fatal_by_itself(self):
        stream, quotes, statuses = _make_stream()
        asyncio.run(stream._handle_message({"T": "error", "code": 405, "msg": "symbol limit exceeded"}))
        assert quotes == []
        assert statuses == [("error", "Alpaca stream error: symbol limit exceeded")]


class TestMissingCredentialsIsFatalNotRetried:
    def test_run_reports_error_once_and_returns_without_looping(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
        monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
        stream, quotes, statuses = _make_stream(api_key_id=None, api_secret_key=None)

        asyncio.run(asyncio.wait_for(stream.run(), timeout=5))

        assert statuses == [("connecting", None), ("error", statuses[-1][1])]
        assert "ALPACA_API_KEY_ID" in statuses[-1][1]
        assert quotes == []

    def test_connect_once_raises_before_touching_the_network(self):
        stream, _, _ = _make_stream(api_key_id=None, api_secret_key=None)
        with pytest.raises(AlpacaCredentialsMissing):
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


class TestAuthErrorCodeClassification:
    """Confirmed against a real account (see AlpacaStreamTransientError's
    docstring): only code 402 means the credentials themselves are
    wrong. Every other auth-stage error is Alpaca's side of the
    handshake and should be retried, not treated as a dead end."""

    def test_code_402_bad_credentials_is_fatal(self):
        stream, _, _ = _make_stream()
        ws = _FakeWebSocket([[{"T": "error", "code": 402, "msg": "auth failed"}]])
        with pytest.raises(AlpacaAuthRejected, match="auth failed"):
            asyncio.run(stream._authenticate(ws))

    def test_code_406_connection_limit_is_transient(self):
        stream, _, _ = _make_stream()
        ws = _FakeWebSocket([[{"T": "error", "code": 406, "msg": "connection limit exceeded"}]])
        with pytest.raises(AlpacaStreamTransientError, match="connection limit exceeded"):
            asyncio.run(stream._authenticate(ws))

    def test_code_404_login_timeout_is_transient(self):
        stream, _, _ = _make_stream()
        ws = _FakeWebSocket([[{"T": "error", "code": 404, "msg": "auth timeout"}]])
        with pytest.raises(AlpacaStreamTransientError):
            asyncio.run(stream._authenticate(ws))

    def test_connected_then_authenticated_success_returns_cleanly(self):
        stream, _, _ = _make_stream()
        ws = _FakeWebSocket(
            [
                [{"T": "success", "msg": "connected"}],
                [{"T": "success", "msg": "authenticated"}],
            ]
        )
        asyncio.run(stream._authenticate(ws))  # does not raise

    def test_a_transient_auth_failure_is_retried_not_treated_as_fatal_by_run(self):
        """End-to-end through run(): a transient auth error should show
        up as "disconnected" (retrying), never "error" (fatal) -- and
        run() must actually retry rather than return immediately."""
        stream, quotes, statuses = _make_stream()
        call_count = 0

        async def fake_connect_once():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise AlpacaStreamTransientError("connection limit exceeded")
            stream.stop()  # succeed on the second attempt, then stop cleanly

        stream._connect_once = fake_connect_once
        asyncio.run(asyncio.wait_for(stream.run(), timeout=5))

        assert call_count == 2  # it really did retry after the transient failure
        assert not any(status == "error" for status, _ in statuses)  # never treated as fatal
        assert any(status == "disconnected" for status, _ in statuses)
