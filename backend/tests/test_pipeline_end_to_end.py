"""End-to-end verification of the ACTUAL historical-bar data pipeline,
as implemented today. Every step below calls real, already-shipped
production code: real AlpacaProvider/MassiveProvider parsing (mocked
only at the httpx transport layer, exactly like
tests/test_alpaca_provider.py and tests/test_massive_provider.py
already do), the real CSV ingestion module, the real HTTP save/read/
compare routes, and the real storage layer. Nothing here is a shortcut
standing in for production code.

THE ACTUAL FLOW THIS TEST PROVES, as shipped (v0.1.19 -- a raw-storage
stage was added on top of the v0.1.18 pipeline this file originally
verified; see git history / the verification report for that gap and
its close):

    Alpaca/Massive (mocked at the transport layer -- real provider     \\
    parsing code runs)                                                   \\
                                                                            >-- provider.get_historical_data()
    CSV upload (app/api/historical_comparison.py, real parser)         /        |
                                                                        /        v
                                                        persist_raw_ingestion_safely()
                                                          -> raw_ingestions (ORIGINAL payload,
                                                             provider/CSV field names, unparsed)
                                                                        |
                                                                        v
                                                        MarketBar -> canonical HistoricalBar
                                                        (fetch_normalized_bars() / parse_ohlcv_csv())
                                                                        |
                                                                        v
                                          POST /market-data/history/save (app/api/historical_storage.py)
                                                             |
                                                   validate_bars()  (app/ingestion/bar_validation.py)
                                                    /                          \\
                                      save_validated_bars()             save_rejected_bars()
                                      -> historical_bars                 -> quarantined_bars
                                                    |
                                    GET /market-data/{symbol}/history/stored
                                    GET /market-data/{symbol}/history/quarantined

Raw capture happens INSIDE provider.get_historical_data() (both
AlpacaProvider and MassiveProvider) and inside the CSV comparison
route, BEFORE any parsing -- not as a new HTTP endpoint or a rewrite of
validate_bars()/save_validated_bars()/save_rejected_bars(), all of
which are byte-for-byte unchanged from v0.1.18 (see
TestValidation/TestHistoricalStorage/TestDeduplicationAndIdempotency
below -- the same assertions that passed before this change still
pass, unmodified, proving the existing pipeline was preserved).

One gap from the previous verification pass remains, unaffected by
this change: the CSV ingestion pathway (app/ingestion/ohlcv_csv.py) is
real, shipped, tested code, but no production route connects its
PARSED OUTPUT to the storage layer -- app/api/historical_comparison.py
(its only caller) uses it purely for CSV-vs-provider diffing, never for
saving. MCL below is still pushed through POST /market-data/history/save
directly for that reason (exactly what that route's own docstring says
it's for: bars the caller already has in hand); see
TestApiScopeAsymmetryFinding for the read-side asymmetry this reveals.
Raw CSV capture itself, however, IS wired into the real route now (see
TestCsvRawStorageThroughTheRealRoute) -- that part of the gap is closed.
"""

import io
import os
from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

import app.api.historical_data as historical_data_module
from app.ingestion.ohlcv_csv import parse_ohlcv_csv
from app.main import app
from app.providers.alpaca_provider import AlpacaProvider
from app.providers.massive_provider import MassiveProvider
from app.storage.historical_bar_repository import get_bars, get_quarantined_bars
from app.storage.raw_ingestion_repository import get_raw_ingestions, persist_raw_ingestion_safely

client = TestClient(app)


# ---------------------------------------------------------------------
# Fixture data -- deterministic, representative TSLA/NVDA/MCL records.
# Each symbol's batch includes at least one deliberately invalid record
# so items #10/#11 (invalid records rejected, one bad record doesn't
# sink the batch) are proven for every symbol, not asserted once and
# assumed to generalize.
# ---------------------------------------------------------------------

# TSLA, via AlpacaProvider -- raw Alpaca bars-endpoint JSON shape
# (see tests/test_alpaca_provider.py). Third bar has high < low: an
# impossible OHLC relationship that must be quarantined, never stored.
TSLA_ALPACA_BARS = [
    {"t": "2026-08-10T04:00:00Z", "o": 245.10, "h": 248.75, "l": 243.20, "c": 247.55, "v": 98_400_000},
    {"t": "2026-08-11T04:00:00Z", "o": 247.60, "h": 250.00, "l": 246.00, "c": 249.30, "v": 87_200_000},
    {"t": "2026-08-12T04:00:00Z", "o": 249.00, "h": 1.00, "l": 999.00, "c": 250.00, "v": 50_000_000},
]


def _alpaca_provider() -> AlpacaProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/stocks/TSLA/bars"
        return httpx.Response(200, json={"symbol": "TSLA", "bars": TSLA_ALPACA_BARS, "next_page_token": None})

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="https://data.alpaca.markets/v2", transport=transport)
    return AlpacaProvider(api_key_id="fake_key", api_secret_key="fake_secret", client=http_client)


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


# NVDA, via MassiveProvider -- raw Massive aggs-endpoint JSON shape,
# millisecond-epoch timestamps (see tests/test_massive_provider.py).
# Second bar has a negative close: an impossible price that must be
# quarantined, never stored.
NVDA_MASSIVE_BARS = [
    {"t": _ms("2026-08-10T00:00:00Z"), "o": 118.40, "h": 120.10, "l": 117.90, "c": 119.85, "v": 210_000_000},
    {"t": _ms("2026-08-11T00:00:00Z"), "o": 119.90, "h": 121.50, "l": 119.00, "c": -5.00, "v": 190_000_000},
]


def _massive_provider() -> MassiveProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/v2/aggs/ticker/NVDA/range/")
        return httpx.Response(200, json={"ticker": "NVDA", "results": NVDA_MASSIVE_BARS, "status": "OK"})

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="https://api.massive.com", transport=transport)
    return MassiveProvider(api_key="fake_key", client=http_client)


# MCL, via the CSV ingestion pathway (app/ingestion/ohlcv_csv.py) --
# not a provider-fetchable symbol in this app (ALLOWED_SYMBOLS is
# TSLA/NVDA only), but a real, shipped ingestion entry point; see
# app/api/historical_comparison.py's own docstring, which uses an
# "MCL-style broker export" as its example. Row 2 is missing `close`
# (a malformed/missing-field record -- rejected at CSV-parse time,
# never becomes a bar at all). Row 4 is well-formed but has negative
# volume (rejected later, by bar_validation.validate_bars()) -- two
# different invalid-record paths, deliberately both covered.
MCL_CSV_TEXT = (
    "Date,Open,High,Low,Close,Volume\n"
    "2026-08-10,68.10,68.90,67.50,68.40,145000\n"
    "2026-08-11,68.45,69.20,68.00,,150000\n"
    "2026-08-12,68.50,69.00,68.10,68.75,140000\n"
    "2026-08-13,68.70,69.10,68.20,68.90,-500\n"
)


# ---------------------------------------------------------------------
# The pipeline is run ONCE for the whole module (module-scoped, not
# per-test) -- every test below reads back what one real ingestion
# session left in the database, the same way a real user's session
# would, rather than each test constructing its own isolated fixture.
# ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def pipeline_db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("pipeline") / "pipeline_e2e.db"
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(path)
    yield path
    if previous is None:
        os.environ.pop("DATABASE_PATH", None)
    else:
        os.environ["DATABASE_PATH"] = previous


@pytest.fixture(scope="module")
def pipeline_result(pipeline_db_path):
    original_get_provider = historical_data_module.get_provider
    historical_data_module.get_provider = lambda name: _alpaca_provider() if name == "alpaca" else _massive_provider()
    try:
        tsla_fetch = client.get(
            "/api/market-data/TSLA/history",
            params={"start": "2026-08-10", "end": "2026-08-12", "timeframe": "1d", "provider": "alpaca"},
        )
        nvda_fetch = client.get(
            "/api/market-data/NVDA/history",
            params={"start": "2026-08-10", "end": "2026-08-11", "timeframe": "1d", "provider": "massive"},
        )

        tsla_save = client.post("/api/market-data/history/save", json={"bars": tsla_fetch.json()["bars"]})
        nvda_save = client.post("/api/market-data/history/save", json={"bars": nvda_fetch.json()["bars"]})

        # Idempotency / deduplication check: re-submit the IDENTICAL
        # TSLA batch a second time (same "ingestion", run again).
        tsla_save_again = client.post("/api/market-data/history/save", json={"bars": tsla_fetch.json()["bars"]})

        # This fixture calls parse_ohlcv_csv() directly rather than the
        # real POST /market-data/history/compare route (see module
        # docstring: MCL can't go through that route's live-provider
        # side, ALLOWED_SYMBOLS is TSLA/NVDA-only) -- so the raw-CSV
        # persist call that route makes is reproduced explicitly here,
        # identical to what app/api/historical_comparison.py itself
        # does. TestCsvRawStorageThroughTheRealRoute below proves the
        # REAL route wires this same call in correctly, using TSLA.
        persist_raw_ingestion_safely(
            source="csv", symbol="MCL", timeframe="1d", source_start=None, source_end=None,
            raw_payload=MCL_CSV_TEXT, content_type="csv", metadata={"filename": "mcl_bars.csv"},
        )

        mcl_parsed = parse_ohlcv_csv(MCL_CSV_TEXT, default_symbol="MCL")
        # parse_ohlcv_csv returns `timestamp` as a real datetime object
        # (see its docstring) -- fine for in-process callers, but the
        # TestClient sends this over real JSON, so it needs the same
        # ISO-string form any real HTTP client would send.
        mcl_bars = [dict(bar, timestamp=bar["timestamp"].isoformat(), timeframe="1d") for bar in mcl_parsed["bars"]]
        mcl_save = client.post("/api/market-data/history/save", json={"bars": mcl_bars})
    finally:
        historical_data_module.get_provider = original_get_provider

    return {
        "tsla_fetch": tsla_fetch,
        "nvda_fetch": nvda_fetch,
        "tsla_save": tsla_save,
        "nvda_save": nvda_save,
        "tsla_save_again": tsla_save_again,
        "mcl_parsed": mcl_parsed,
        "mcl_save": mcl_save,
    }


def _tsla_bars():
    return get_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=date(2026, 8, 1), end=date(2026, 8, 31))


def _nvda_bars():
    return get_bars(symbol="NVDA", timeframe="1d", provider="massive", start=date(2026, 8, 1), end=date(2026, 8, 31))


def _mcl_bars():
    return get_bars(symbol="MCL", timeframe="1d", provider="csv", start=date(2026, 8, 1), end=date(2026, 8, 31))


# ---------------------------------------------------------------------
# 1. INGESTION -- data enters through the existing ingestion pathways.
# ---------------------------------------------------------------------


class TestIngestion:
    def test_tsla_bars_enter_via_the_real_alpaca_provider_parsing_code(self, pipeline_result):
        resp = pipeline_result["tsla_fetch"]
        assert resp.status_code == 200
        bars = resp.json()["bars"]
        assert len(bars) == 3  # ingestion/parsing does not reject anything -- that's validation's job, next stage
        assert {b["provider"] for b in bars} == {"alpaca"}
        assert {b["symbol"] for b in bars} == {"TSLA"}

    def test_nvda_bars_enter_via_the_real_massive_provider_parsing_code(self, pipeline_result):
        resp = pipeline_result["nvda_fetch"]
        assert resp.status_code == 200
        bars = resp.json()["bars"]
        assert len(bars) == 2
        assert {b["provider"] for b in bars} == {"massive"}
        assert {b["symbol"] for b in bars} == {"NVDA"}

    def test_mcl_bars_enter_via_the_csv_ingestion_pathway(self, pipeline_result):
        parsed = pipeline_result["mcl_parsed"]
        assert parsed["total_rows"] == 4
        assert parsed["imported_rows"] == 3  # the missing-close row is excluded here, not silently faked
        assert len(parsed["row_errors"]) == 1
        assert "close" in parsed["row_errors"][0]["message"].lower()
        assert parsed["symbols"] == ["MCL"]


# ---------------------------------------------------------------------
# 2/12. RAW STORAGE -- the original provider/CSV payload is persisted
# BEFORE parsing, independently of the normalized/quarantined tables,
# and survives even when the record it contains later fails validation.
# ---------------------------------------------------------------------


class TestRawStorage:
    def test_raw_storage_table_exists_alongside_the_existing_ones(self, pipeline_db_path):
        from app.storage.db import get_connection

        conn = get_connection(str(pipeline_db_path))
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()

        # sqlite_sequence is SQLite's own AUTOINCREMENT bookkeeping
        # table, not application data. experiments/experiment_events
        # (Research v1, app/storage/db.py) are the same schema file's
        # tables as everything else here -- get_connection() creates
        # the whole schema in one executescript() call, so any table it
        # knows about shows up the moment a connection is opened, not
        # just the ones this particular pipeline test exercises.
        assert tables - {"sqlite_sequence"} == {
            "historical_bars",
            "quarantined_bars",
            "raw_ingestions",
            "experiments",
            "experiment_events",
        }

    def test_raw_ingestion_row_exists_for_tsla_after_fetch(self, pipeline_result):
        rows = get_raw_ingestions(source="alpaca", symbol="TSLA")
        assert len(rows) >= 1
        assert all(r.content_type == "json" for r in rows)
        assert all(r.symbol == "TSLA" for r in rows)
        # "1Day", not this app's normalized "1d" -- the raw row records
        # the timeframe value Alpaca itself was actually asked for
        # (Alpaca's own vocabulary), consistent with "raw" meaning
        # exactly what was sent/received, not the app's translation of it.
        assert all(r.timeframe == "1Day" for r in rows)

    def test_raw_ingestion_row_exists_for_nvda_after_fetch(self, pipeline_result):
        rows = get_raw_ingestions(source="massive", symbol="NVDA")
        assert len(rows) >= 1
        assert all(r.content_type == "json" for r in rows)
        assert all(r.symbol == "NVDA" for r in rows)

    def test_raw_payload_preserves_alpacas_original_representation(self, pipeline_result):
        """Not the canonical HistoricalBar shape (open/high/low/close) --
        Alpaca's own field names (o/h/l/c/v, nested under "bars", with
        Alpaca's own "next_page_token" pagination field) exactly as
        received. This is the entire point of a raw stage: forcing it
        into this app's schema would defeat it."""
        import json as jsonlib

        row = get_raw_ingestions(source="alpaca", symbol="TSLA")[0]
        pages = jsonlib.loads(row.raw_payload)
        assert isinstance(pages, list) and len(pages) == 1  # one HTTP page, this fixture's response wasn't paginated
        page = pages[0]
        assert "bars" in page and "next_page_token" in page  # Alpaca's own response shape, untouched
        assert page["bars"][0]["o"] == 245.10  # Alpaca's short field names, not "open"
        assert "open" not in page["bars"][0]

    def test_raw_payload_preserves_massives_original_representation(self, pipeline_result):
        import json as jsonlib

        row = get_raw_ingestions(source="massive", symbol="NVDA")[0]
        pages = jsonlib.loads(row.raw_payload)
        page = pages[0]
        assert "results" in page and "ticker" in page and "status" in page  # Massive's own response shape
        assert page["results"][0]["o"] == 118.40
        assert "open" not in page["results"][0]

    def test_raw_csv_payload_preserves_the_original_csv_text_verbatim(self, pipeline_result):
        rows = get_raw_ingestions(source="csv", symbol="MCL")
        assert len(rows) >= 1
        assert rows[0].raw_payload == MCL_CSV_TEXT  # byte-for-byte, not re-parsed or reshaped
        assert rows[0].content_type == "csv"

    def test_raw_and_normalized_data_are_independently_stored(self, pipeline_result):
        """TSLA's raw row holds all 3 bars the provider actually sent
        (including the impossible-OHLC one); historical_bars holds only
        the 2 that passed validation. Different row counts, different
        tables, different lifecycles -- not a mirror of each other."""
        import json as jsonlib

        raw_row = get_raw_ingestions(source="alpaca", symbol="TSLA")[0]
        raw_bar_count = len(jsonlib.loads(raw_row.raw_payload)[0]["bars"])
        assert raw_bar_count == 3
        assert len(_tsla_bars()) == 2  # historical_bars only has the valid ones

    def test_raw_data_survives_even_when_the_bar_was_later_quarantined(self, pipeline_result):
        """Answers exactly the question raw storage exists to answer:
        "what did the provider give us when this record failed?" --
        the impossible-OHLC bar (high=1.00, low=999.00) is right there
        in the raw payload, even though it was rejected and never
        reached historical_bars."""
        import json as jsonlib

        row = get_raw_ingestions(source="alpaca", symbol="TSLA")[0]
        raw_bars = jsonlib.loads(row.raw_payload)[0]["bars"]
        offending = [b for b in raw_bars if b["h"] == 1.00 and b["l"] == 999.00]
        assert len(offending) == 1

        # Confirm it really was rejected, not silently dropped somewhere.
        quarantined = get_quarantined_bars(
            symbol="TSLA", timeframe="1d", provider="alpaca", start=date(2026, 8, 1), end=date(2026, 8, 31)
        )
        assert any(q.bar.high == 1.00 and q.bar.low == 999.00 for q in quarantined)

    def test_raw_ingestion_is_never_blocked_by_a_later_quarantine(self, pipeline_result):
        """Ordering check: the raw row for TSLA's batch must exist
        regardless of what validate_bars() decided afterward -- raw
        storage happens BEFORE parsing/validation, not conditionally on
        their outcome."""
        assert len(get_raw_ingestions(source="alpaca", symbol="TSLA")) >= 1
        assert pipeline_result["tsla_save"].json()["rejected_invalid"] == 1  # the batch DID have a rejection
        # ... and the raw row above still exists despite that.


# ---------------------------------------------------------------------
# 3/10/11. VALIDATION -- valid records pass; invalid ones are rejected
# and quarantined; one bad record never sinks the rest of the batch.
# ---------------------------------------------------------------------


class TestValidation:
    def test_tsla_impossible_ohlc_bar_is_rejected(self, pipeline_result):
        body = pipeline_result["tsla_save"].json()
        assert body["total"] == 3
        assert body["rejected_invalid"] == 1
        reasons = " ".join(pipeline_result["tsla_save"].json()["rejected"][0]["reasons"]).lower()
        assert "low" in reasons and "high" in reasons

    def test_nvda_negative_price_bar_is_rejected(self, pipeline_result):
        body = pipeline_result["nvda_save"].json()
        assert body["total"] == 2
        assert body["rejected_invalid"] == 1
        reasons = " ".join(body["rejected"][0]["reasons"]).lower()
        assert "positive" in reasons or "negative" in reasons

    def test_mcl_negative_volume_bar_is_rejected_by_bar_validation(self, pipeline_result):
        body = pipeline_result["mcl_save"].json()
        assert body["total"] == 3  # the malformed CSV row already dropped out before this point
        assert body["rejected_invalid"] == 1
        assert "negative" in body["rejected"][0]["reasons"][0].lower()

    def test_one_bad_record_never_sinks_the_rest_of_the_batch(self, pipeline_result):
        assert pipeline_result["tsla_save"].json()["inserted"] == 2  # 3 submitted, 1 rejected, 2 saved
        assert pipeline_result["nvda_save"].json()["inserted"] == 1  # 2 submitted, 1 rejected, 1 saved
        assert pipeline_result["mcl_save"].json()["inserted"] == 2  # 3 submitted, 1 rejected, 2 saved

    def test_rejected_bars_never_leak_into_clean_storage(self, pipeline_result):
        assert all(b.high >= b.low for b in _tsla_bars())
        assert all(b.close > 0 for b in _nvda_bars())
        assert all(b.volume >= 0 for b in _mcl_bars())

    def test_rejected_bars_are_visible_in_the_quarantine_audit_trail(self, pipeline_result):
        q = get_quarantined_bars(
            symbol="TSLA", timeframe="1d", provider="alpaca", start=date(2026, 8, 1), end=date(2026, 8, 31)
        )
        # >= 1, not == 1: quarantine has no UNIQUE constraint by design
        # (see db.py's schema comment) -- every submission of the same
        # bad bar logs again, and this module's dedup/idempotency tests
        # deliberately resubmit the same TSLA batch more than once.
        assert len(q) >= 1
        assert all(bar.bar.symbol == "TSLA" for bar in q)
        assert all(any("low" in reason.lower() for reason in bar.validation_errors) for bar in q)


# ---------------------------------------------------------------------
# 4. NORMALIZATION -- provider-specific fields converted to the
# canonical schema.
# ---------------------------------------------------------------------


class TestNormalization:
    def test_alpaca_bar_converts_to_the_canonical_schema(self, pipeline_result):
        bar = pipeline_result["tsla_fetch"].json()["bars"][0]
        assert set(bar.keys()) == {"symbol", "timestamp", "open", "high", "low", "close", "volume", "provider", "timeframe"}
        assert bar["symbol"] == "TSLA"
        assert bar["provider"] == "alpaca"
        assert bar["timeframe"] == "1d"
        assert bar["close"] == 247.55

    def test_massive_ms_epoch_timestamp_converts_to_a_real_utc_datetime(self, pipeline_result):
        bar = pipeline_result["nvda_fetch"].json()["bars"][0]
        parsed = datetime.fromisoformat(bar["timestamp"].replace("Z", "+00:00"))
        assert parsed == datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
        assert bar["provider"] == "massive"

    def test_csv_bar_carries_source_csv_and_the_canonical_shape(self, pipeline_result):
        bar = pipeline_result["mcl_parsed"]["bars"][0]
        assert bar["provider"] == "csv"
        assert bar["symbol"] == "MCL"
        assert set(bar.keys()) == {"symbol", "timestamp", "open", "high", "low", "close", "volume", "provider"}


# ---------------------------------------------------------------------
# 5/6. HISTORICAL STORAGE -- normalized records land in historical
# storage with the expected fields.
# ---------------------------------------------------------------------


class TestHistoricalStorage:
    def test_valid_tsla_bars_are_stored_with_expected_fields(self, pipeline_result):
        stored = _tsla_bars()
        assert len(stored) == 2
        assert {b.timestamp.date().isoformat() for b in stored} == {"2026-08-10", "2026-08-11"}
        assert stored[0].symbol == "TSLA"
        assert stored[0].provider == "alpaca"
        assert stored[0].timeframe == "1d"
        assert stored[0].close == 247.55
        assert stored[0].volume == 98_400_000

    def test_valid_nvda_bars_are_stored_with_expected_fields(self, pipeline_result):
        stored = _nvda_bars()
        assert len(stored) == 1
        assert stored[0].symbol == "NVDA"
        assert stored[0].provider == "massive"
        assert stored[0].close == 119.85

    def test_valid_mcl_bars_are_stored_with_expected_fields(self, pipeline_result):
        stored = _mcl_bars()
        assert len(stored) == 2
        assert {b.close for b in stored} == {68.40, 68.75}
        assert all(b.provider == "csv" for b in stored)
        assert all(b.timeframe == "1d" for b in stored)


# ---------------------------------------------------------------------
# 7. TIMESTAMPS -- handled consistently (UTC-aware) across every
# symbol/pathway.
# ---------------------------------------------------------------------


class TestTimestampConsistency:
    def test_every_stored_bar_has_a_utc_aware_timestamp(self, pipeline_result):
        for bar in [*_tsla_bars(), *_nvda_bars(), *_mcl_bars()]:
            assert bar.timestamp.tzinfo is not None
            assert bar.timestamp.utcoffset().total_seconds() == 0


# ---------------------------------------------------------------------
# 8/9. DEDUPLICATION + IDEMPOTENCY -- re-submitting the same
# ingestion never creates duplicate rows.
# ---------------------------------------------------------------------


class TestDeduplicationAndIdempotency:
    def test_resaving_the_identical_tsla_batch_creates_no_duplicate_rows(self, pipeline_result):
        again = pipeline_result["tsla_save_again"].json()
        assert again["inserted"] == 0
        assert again["skipped_duplicates"] == 2
        assert len(_tsla_bars()) == 2  # still 2, not 4

    def test_re_running_the_full_fetch_and_save_ingestion_is_idempotent(self, pipeline_result):
        before = _tsla_bars()
        raw_rows_before = len(get_raw_ingestions(source="alpaca", symbol="TSLA"))

        # Provider mock was only installed for the module-scoped
        # pipeline_result fixture's duration -- re-install it here to
        # prove idempotency of a genuinely SEPARATE ingestion run, not
        # a rerun still inside the same monkeypatched block.
        original_get_provider = historical_data_module.get_provider
        historical_data_module.get_provider = lambda name: _alpaca_provider()
        try:
            resp = client.get(
                "/api/market-data/TSLA/history",
                params={"start": "2026-08-10", "end": "2026-08-12", "timeframe": "1d", "provider": "alpaca"},
            )
            client.post("/api/market-data/history/save", json={"bars": resp.json()["bars"]})
        finally:
            historical_data_module.get_provider = original_get_provider

        after = _tsla_bars()
        assert before == after  # normalized/historical storage: no duplicate bars from re-ingesting

        # Raw storage, by contrast, is deliberately NOT deduplicated
        # (see db.py's schema comment): a real second provider call
        # happened here, so a second raw row is correct, not a bug --
        # this is what makes auto_ingest.py's overlapping-lookback-
        # window re-fetching safe to log every time.
        raw_rows_after = len(get_raw_ingestions(source="alpaca", symbol="TSLA"))
        assert raw_rows_after == raw_rows_before + 1


# ---------------------------------------------------------------------
# Symbol separation -- TSLA/NVDA/MCL never bleed into each other.
# ---------------------------------------------------------------------


class TestSymbolSeparation:
    def test_tsla_nvda_mcl_stay_separated_through_storage(self, pipeline_result):
        tsla, nvda, mcl = _tsla_bars(), _nvda_bars(), _mcl_bars()
        assert {b.symbol for b in tsla} == {"TSLA"}
        assert {b.symbol for b in nvda} == {"NVDA"}
        assert {b.symbol for b in mcl} == {"MCL"}
        assert len(tsla) + len(nvda) + len(mcl) == 5  # 2 + 1 + 2, no cross-contamination inflating any one query

    def test_tsla_nvda_mcl_stay_separated_through_quarantine(self, pipeline_result):
        tsla_q = get_quarantined_bars(symbol="TSLA", timeframe="1d", provider="alpaca", start=date(2026, 8, 1), end=date(2026, 8, 31))
        nvda_q = get_quarantined_bars(symbol="NVDA", timeframe="1d", provider="massive", start=date(2026, 8, 1), end=date(2026, 8, 31))
        mcl_q = get_quarantined_bars(symbol="MCL", timeframe="1d", provider="csv", start=date(2026, 8, 1), end=date(2026, 8, 31))
        # >= 1, not == 1 -- see test_rejected_bars_are_visible_in_the_quarantine_audit_trail
        # above for why (no UNIQUE constraint on quarantine, by design).
        assert len(tsla_q) >= 1 and all(q.bar.symbol == "TSLA" for q in tsla_q)
        assert len(nvda_q) >= 1 and all(q.bar.symbol == "NVDA" for q in nvda_q)
        assert len(mcl_q) >= 1 and all(q.bar.symbol == "MCL" for q in mcl_q)


# ---------------------------------------------------------------------
# A real asymmetry this test surfaces (not fixed here -- see
# constraint: only report gaps a test finds, don't redesign scope
# decisions unilaterally): POST .../history/save is symbol-agnostic,
# but GET .../history/stored and .../quarantined enforce
# ALLOWED_SYMBOLS (TSLA/NVDA only). MCL bars saved above are real rows
# in historical_bars/quarantined_bars, readable via the repository,
# but unreachable through either read route.
# ---------------------------------------------------------------------


class TestApiScopeAsymmetryFinding:
    def test_mcl_bars_were_genuinely_saved_through_the_real_save_route(self, pipeline_result):
        assert pipeline_result["mcl_save"].status_code == 200
        assert pipeline_result["mcl_save"].json()["inserted"] == 2

    def test_but_mcl_cannot_be_read_back_through_get_stored(self, pipeline_result):
        resp = client.get(
            "/api/market-data/MCL/history/stored",
            params={"start": "2026-08-01", "end": "2026-08-31", "timeframe": "1d", "provider": "csv"},
        )
        assert resp.status_code == 400  # data exists (see TestHistoricalStorage) but this route can't reach it

    def test_and_mcl_quarantine_is_equally_unreachable_through_get_quarantined(self, pipeline_result):
        resp = client.get(
            "/api/market-data/MCL/history/quarantined",
            params={"start": "2026-08-01", "end": "2026-08-31", "timeframe": "1d", "provider": "csv"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------
# Raw CSV capture through the REAL production route (not the direct
# parse_ohlcv_csv() call the main fixture above uses for MCL, since MCL
# can't go through this route's live-provider side -- see module
# docstring). TSLA here, so both sides of the comparison succeed,
# proving the actual wiring in app/api/historical_comparison.py, not
# just the underlying repository function it calls.
# ---------------------------------------------------------------------


@pytest.fixture
def isolated_db_for_csv_route(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "csv_route_raw_storage.db"))


class TestCsvRawStorageThroughTheRealRoute:
    _CSV_TEXT = "Date,Open,High,Low,Close,Volume\n2026-08-10,245.10,248.75,243.20,247.55,98400000\n"

    def test_uploading_a_csv_persists_its_raw_text_through_the_real_compare_route(self, isolated_db_for_csv_route, monkeypatch):
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _alpaca_provider())

        resp = client.post(
            "/api/market-data/history/compare",
            data={"symbol": "TSLA", "start": "2026-08-10", "end": "2026-08-10", "timeframe": "1d", "provider": "alpaca"},
            files={"file": ("tsla_bars.csv", io.BytesIO(self._CSV_TEXT.encode()), "text/csv")},
        )

        assert resp.status_code == 200
        rows = get_raw_ingestions(source="csv", symbol="TSLA")
        assert len(rows) == 1
        assert rows[0].raw_payload == self._CSV_TEXT
        assert rows[0].content_type == "csv"
        assert rows[0].metadata.get("filename") == "tsla_bars.csv"

    def test_raw_csv_is_persisted_even_when_the_file_fails_to_parse(self, isolated_db_for_csv_route, monkeypatch):
        """Requirement: raw data must remain available even when
        parsing/validation subsequently fails. A CSV missing a required
        column fails app/ingestion/ohlcv_csv.py's parser entirely (422),
        but the raw text must still have been captured first."""
        monkeypatch.setattr(historical_data_module, "get_provider", lambda name: _alpaca_provider())
        malformed_csv = "Date,Open,High,Low,Close\n2026-08-10,245.10,248.75,243.20,247.55\n"  # no Volume column

        resp = client.post(
            "/api/market-data/history/compare",
            data={"symbol": "TSLA", "start": "2026-08-10", "end": "2026-08-10", "timeframe": "1d", "provider": "alpaca"},
            files={"file": ("bad.csv", io.BytesIO(malformed_csv.encode()), "text/csv")},
        )

        assert resp.status_code == 422  # the request itself fails...
        rows = get_raw_ingestions(source="csv", symbol="TSLA")
        assert len(rows) == 1  # ...but the raw content was still persisted before parsing was attempted
        assert rows[0].raw_payload == malformed_csv
