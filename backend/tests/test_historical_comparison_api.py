"""Tests for POST /api/market-data/history/compare (v0.1.16) -- the
CSV-vs-provider comparison route described as this feature's single
most important test. Mocks the provider layer (via
app.api.historical_data.get_provider, which the route calls through
fetch_normalized_bars) exactly like test_historical_data_api.py, plus
an UploadFile for the CSV side.

v0.1.19: this route also persists the uploaded CSV's raw text (see
app/storage/raw_ingestion_repository.py) before parsing it --
isolated_db below points DATABASE_PATH at a throwaway tmp_path file for
every test here, same as tests/test_historical_storage_api.py, so this
file never writes into a developer's real backend/data/historical_bars.db.
"""

import io

import httpx
import pytest
from fastapi.testclient import TestClient

import app.api.historical_data as historical_data_module
from app.main import app
from app.models.market_data import MarketBar, MarketTimestamp

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_historical_comparison.db"))


def _bar(symbol, ts, open_, high, low, close, volume) -> MarketBar:
    return MarketBar(
        symbol=symbol,
        timestamp=MarketTimestamp(value=ts, source="alpaca"),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


class _FakeProvider:
    def __init__(self, bars: list[MarketBar] | None = None, exc: Exception | None = None):
        self._bars = bars
        self._exc = exc

    def get_historical_data(self, **kwargs) -> list[MarketBar]:
        if self._exc is not None:
            raise self._exc
        return self._bars or []


def _post_compare(csv_text: str, **form) -> httpx.Response:
    files = {"file": ("bars.csv", io.BytesIO(csv_text.encode()), "text/csv")}
    data = {
        "symbol": "TSLA",
        "start": "2026-08-10",
        "end": "2026-08-11",
        "timeframe": "1d",
        "provider": "alpaca",
        **form,
    }
    return client.post("/api/market-data/history/compare", data=data, files=files)


class TestCompareRouteMocked:
    def test_identical_data_on_both_sides_shows_no_diffs(self, monkeypatch):
        bars = [
            _bar("TSLA", "2026-08-10T00:00:00Z", 245.10, 248.75, 243.20, 247.55, 98_400_000),
            _bar("TSLA", "2026-08-11T00:00:00Z", 247.60, 250.00, 246.00, 249.30, 87_200_000),
        ]
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(bars=bars))

        csv_text = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-08-10,245.10,248.75,243.20,247.55,98400000\n"
            "2026-08-11,247.60,250.00,246.00,249.30,87200000\n"
        )
        resp = _post_compare(csv_text)

        assert resp.status_code == 200
        data = resp.json()
        assert data["csv_row_count"] == 2
        assert data["api_row_count"] == 2
        assert data["row_count_diff"] == 0
        assert data["matched_count"] == 2
        assert data["csv_only_count"] == 0
        assert data["api_only_count"] == 0
        assert data["rows_with_value_diffs"] == 0
        assert data["max_open_diff"] == 0.0
        assert data["max_volume_diff"] == 0

    def test_a_close_price_discrepancy_is_surfaced_as_a_signed_diff(self, monkeypatch):
        bars = [_bar("TSLA", "2026-08-10T00:00:00Z", 245.10, 248.75, 243.20, 247.55, 98_400_000)]
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(bars=bars))

        csv_text = "Date,Open,High,Low,Close,Volume\n2026-08-10,245.10,248.75,243.20,247.00,98400000\n"
        resp = _post_compare(csv_text, end="2026-08-10")

        assert resp.status_code == 200
        data = resp.json()
        row = data["rows"][0]
        assert row["in_csv"] is True
        assert row["in_api"] is True
        assert row["csv_close"] == 247.00
        assert row["api_close"] == 247.55
        assert row["close_diff"] == pytest.approx(0.55)
        assert data["max_close_diff"] == pytest.approx(0.55)
        assert data["rows_with_value_diffs"] == 1

    def test_a_timestamp_only_the_csv_has_is_reported_csv_only(self, monkeypatch):
        bars = [_bar("TSLA", "2026-08-10T00:00:00Z", 1, 2, 0.5, 1.5, 100)]
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(bars=bars))

        csv_text = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-08-10,1,2,0.5,1.5,100\n"
            "2026-08-11,1,2,0.5,1.5,100\n"  # API has no bar for this date
        )
        resp = _post_compare(csv_text)

        assert resp.status_code == 200
        data = resp.json()
        assert data["csv_only_count"] == 1
        assert data["api_only_count"] == 0
        only_row = next(r for r in data["rows"] if r["timestamp"] == "2026-08-11T00:00:00Z")
        assert only_row["in_csv"] is True
        assert only_row["in_api"] is False
        assert only_row["close_diff"] is None

    def test_a_timestamp_only_the_api_has_is_reported_api_only(self, monkeypatch):
        bars = [
            _bar("TSLA", "2026-08-10T00:00:00Z", 1, 2, 0.5, 1.5, 100),
            _bar("TSLA", "2026-08-11T00:00:00Z", 1, 2, 0.5, 1.5, 100),
        ]
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(bars=bars))

        csv_text = "Date,Open,High,Low,Close,Volume\n2026-08-10,1,2,0.5,1.5,100\n"
        resp = _post_compare(csv_text)

        data = resp.json()
        assert data["api_only_count"] == 1
        only_row = next(r for r in data["rows"] if r["timestamp"] == "2026-08-11T00:00:00Z")
        assert only_row["in_csv"] is False
        assert only_row["in_api"] is True

    def test_csv_rows_for_a_different_symbol_are_excluded_and_noted(self, monkeypatch):
        bars = [_bar("TSLA", "2026-08-10T00:00:00Z", 1, 2, 0.5, 1.5, 100)]
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(bars=bars))

        csv_text = (
            "Symbol,Date,Open,High,Low,Close,Volume\n"
            "TSLA,2026-08-10,1,2,0.5,1.5,100\n"
            "NVDA,2026-08-10,10,20,5,15,1000\n"
        )
        resp = _post_compare(csv_text, end="2026-08-10")

        data = resp.json()
        assert data["csv_row_count"] == 1  # NVDA row excluded
        assert any("NVDA" in note for note in data["notes"])

    def test_csv_row_outside_the_requested_date_range_is_excluded(self, monkeypatch):
        bars = [_bar("TSLA", "2026-08-10T00:00:00Z", 1, 2, 0.5, 1.5, 100)]
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(bars=bars))

        csv_text = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-08-10,1,2,0.5,1.5,100\n"
            "2026-01-01,9,9,9,9,9\n"  # outside start/end
        )
        resp = _post_compare(csv_text, end="2026-08-10")

        assert resp.json()["csv_row_count"] == 1

    def test_csv_parse_errors_are_included_but_dont_fail_the_request(self, monkeypatch):
        bars = [_bar("TSLA", "2026-08-10T00:00:00Z", 1, 2, 0.5, 1.5, 100)]
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(bars=bars))

        csv_text = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-08-10,1,2,0.5,1.5,100\n"
            "not-a-date,1,2,0.5,1.5,100\n"
        )
        resp = _post_compare(csv_text, end="2026-08-10")

        assert resp.status_code == 200
        data = resp.json()
        assert data["csv_total_rows"] == 2
        assert len(data["csv_row_errors"]) == 1
        assert data["csv_row_errors"][0]["row_number"] == 3

    def test_missing_required_csv_column_returns_422(self, monkeypatch):
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(bars=[]))

        csv_text = "Date,Open,High,Low,Close\n2026-08-10,1,2,0.5,1.5\n"  # no Volume column
        resp = _post_compare(csv_text)

        assert resp.status_code == 422

    def test_empty_upload_returns_422(self, monkeypatch):
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(bars=[]))
        resp = _post_compare("")
        assert resp.status_code == 422

    def test_unsupported_symbol_returns_400_before_touching_any_provider(self, monkeypatch):
        called = False

        def fail_if_called(name):
            nonlocal called
            called = True
            return _FakeProvider(bars=[])

        monkeypatch.setattr(historical_data_module, "get_provider", fail_if_called)

        csv_text = "Date,Open,High,Low,Close,Volume\n2026-08-10,1,2,0.5,1.5,100\n"
        resp = _post_compare(csv_text, symbol="AAPL")

        assert resp.status_code == 400
        assert called is False

    def test_missing_credentials_returns_503(self, monkeypatch):
        monkeypatch.setattr(
            historical_data_module,
            "get_provider",
            lambda name: _FakeProvider(exc=RuntimeError("AlpacaProvider requires ALPACA_API_KEY_ID ...")),
        )

        csv_text = "Date,Open,High,Low,Close,Volume\n2026-08-10,1,2,0.5,1.5,100\n"
        resp = _post_compare(csv_text)

        assert resp.status_code == 503

    def test_result_always_includes_the_utc_assumption_note(self, monkeypatch):
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _FakeProvider(bars=[]))

        csv_text = "Date,Open,High,Low,Close,Volume\n2026-08-10,1,2,0.5,1.5,100\n"
        resp = _post_compare(csv_text)

        assert any("UTC" in note for note in resp.json()["notes"])
