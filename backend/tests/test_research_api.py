"""End-to-end tests for Research v1's HTTP routes (app/api/research.py):

    POST /research/experiments
    GET  /research/experiments
    GET  /research/experiments/{id}
    GET  /research/experiments/{id}/events
    POST /research/experiments/{id}/run

Every test gets an isolated, throwaway SQLite file via the `isolated_db`
autouse fixture (same convention as tests/test_historical_storage_api.py)
-- never the real developer database. Bars are seeded directly through
app.storage.historical_bar_repository.save_bars(), bypassing the HTTP
save/validation route entirely: what matters here is Research v1's own
routes, not re-testing bar validation (already covered by
tests/test_historical_storage_api.py).
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.market_data import HistoricalBar
from app.storage.historical_bar_repository import get_bars, save_bars

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_research.db"))


def _bar(symbol: str, timestamp: datetime, close: float, *, provider="csv", timeframe="5m") -> HistoricalBar:
    return HistoricalBar(
        symbol=symbol,
        timestamp=timestamp,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000,
        provider=provider,
        timeframe=timeframe,
    )


def _seed_single_step_signal(symbol="TSLA", *, start=None, provider="csv") -> list[HistoricalBar]:
    """A minimal, hand-verifiable series (1-bar/5-minute condition and
    outcome windows): two flat bars, one -2% drop bar (a signal, since
    -2% <= -1%), one further-but-gentler decline bar (-0.61%, enough to
    satisfy the outcome's -0.5% threshold without itself re-triggering
    the -1% condition), then flat again -- exactly ONE qualifying
    event, unambiguously."""
    start = start or datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
    closes = [100.0, 100.0, 98.0, 97.4] + [97.4] * 4
    bars = [_bar(symbol, start + timedelta(minutes=5 * i), close, provider=provider) for i, close in enumerate(closes)]
    save_bars(bars)
    return bars


def _create_request(**overrides) -> dict:
    payload = {
        "name": "TSLA Early Selling Continuation",
        "hypothesis": "Declines >= 1% in 5m keep declining in the next 5m.",
        "symbol": "TSLA",
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
        "timeframe": "5m",
        "provider": "csv",
        "condition": {"metric": "5m_return", "operator": "<=", "threshold": -0.01},
        "outcome": {"metric": "forward_return", "horizon_minutes": 5, "operator": "<=", "threshold": -0.005},
    }
    payload.update(overrides)
    return payload


class TestCreateExperiment:
    def test_creates_a_draft_experiment(self):
        resp = client.post("/api/research/experiments", json=_create_request())

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "draft"
        assert body["symbol"] == "TSLA"
        assert body["completed_at"] is None
        assert body["results"] is None
        assert body["id"]

    def test_unsupported_symbol_is_rejected(self):
        resp = client.post("/api/research/experiments", json=_create_request(symbol="AAPL"))
        assert resp.status_code == 400

    def test_unsupported_timeframe_is_rejected(self):
        resp = client.post("/api/research/experiments", json=_create_request(timeframe="3m"))
        assert resp.status_code == 400

    def test_end_before_start_is_rejected(self):
        resp = client.post("/api/research/experiments", json=_create_request(start_date="2026-01-31", end_date="2026-01-01"))
        assert resp.status_code == 400

    def test_malformed_condition_metric_is_rejected_at_the_model_level(self):
        """'Invalid condition handling': a metric that is not '{N}m_return'
        fails Condition's own field validator during request parsing,
        before the route body even runs -- a 422, not a 400."""
        bad_request = _create_request(condition={"metric": "not_a_real_metric", "operator": "<=", "threshold": -0.01})
        resp = client.post("/api/research/experiments", json=bad_request)
        assert resp.status_code == 422

    def test_unsupported_outcome_metric_is_rejected_at_the_model_level(self):
        bad_request = _create_request(
            outcome={"metric": "something_else", "horizon_minutes": 60, "operator": "<=", "threshold": -0.005}
        )
        resp = client.post("/api/research/experiments", json=bad_request)
        assert resp.status_code == 422

    def test_unsupported_operator_is_rejected_at_the_model_level(self):
        bad_request = _create_request(condition={"metric": "5m_return", "operator": "!=", "threshold": -0.01})
        resp = client.post("/api/research/experiments", json=bad_request)
        assert resp.status_code == 422

    def test_a_condition_window_that_does_not_align_with_the_timeframe_is_rejected(self):
        """30 minutes is not a whole number of 1-hour bars -- rejected
        by the route's own metrics.bars_for_window() check, a 400."""
        bad_request = _create_request(
            timeframe="1h",
            condition={"metric": "30m_return", "operator": "<=", "threshold": -0.01},
            outcome={"metric": "forward_return", "horizon_minutes": 60, "operator": "<=", "threshold": -0.005},
        )
        resp = client.post("/api/research/experiments", json=bad_request)
        assert resp.status_code == 400


class TestGetAndListExperiments:
    def test_get_by_id_returns_what_was_created(self):
        created = client.post("/api/research/experiments", json=_create_request()).json()

        resp = client.get(f"/api/research/experiments/{created['id']}")

        assert resp.status_code == 200
        assert resp.json() == created

    def test_get_missing_id_returns_404(self):
        resp = client.get("/api/research/experiments/does-not-exist")
        assert resp.status_code == 404

    def test_list_returns_every_created_experiment(self):
        first = client.post("/api/research/experiments", json=_create_request(name="First")).json()
        second = client.post("/api/research/experiments", json=_create_request(name="Second")).json()

        resp = client.get("/api/research/experiments")

        assert resp.status_code == 200
        ids = {e["id"] for e in resp.json()}
        assert {first["id"], second["id"]} <= ids


class TestRunExperiment:
    def test_run_missing_id_returns_404(self):
        resp = client.post("/api/research/experiments/does-not-exist/run")
        assert resp.status_code == 404

    def test_running_finds_the_seeded_signal_and_completes(self):
        _seed_single_step_signal()
        created = client.post("/api/research/experiments", json=_create_request()).json()

        resp = client.post(f"/api/research/experiments/{created['id']}/run")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["completed_at"] is not None
        assert body["results"]["total_events"] == 1
        assert body["results"]["successful_events"] == 1
        assert body["results"]["success_rate"] == pytest.approx(1.0)

    def test_running_with_no_matching_bars_completes_with_zero_events(self):
        """An empty dataset is a legitimate, non-error outcome -- not a
        FAILED run."""
        created = client.post("/api/research/experiments", json=_create_request()).json()

        resp = client.post(f"/api/research/experiments/{created['id']}/run")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["results"]["total_events"] == 0
        assert body["results"]["success_rate"] is None

    def test_run_never_modifies_historical_bars(self):
        """Data integrity requirement: Research must never modify the
        historical dataset it reads."""
        bars = _seed_single_step_signal()
        before = get_bars(symbol="TSLA", timeframe="5m", provider="csv", start=bars[0].timestamp.date(), end=bars[-1].timestamp.date())
        created = client.post("/api/research/experiments", json=_create_request()).json()

        client.post(f"/api/research/experiments/{created['id']}/run")

        after = get_bars(symbol="TSLA", timeframe="5m", provider="csv", start=bars[0].timestamp.date(), end=bars[-1].timestamp.date())
        assert after == before


class TestExperimentEvents:
    def test_events_are_empty_before_the_experiment_has_run(self):
        created = client.post("/api/research/experiments", json=_create_request()).json()

        resp = client.get(f"/api/research/experiments/{created['id']}/events")

        assert resp.status_code == 200
        assert resp.json() == {"experiment_id": created["id"], "event_count": 0, "events": []}

    def test_events_are_populated_after_a_run(self):
        _seed_single_step_signal()
        created = client.post("/api/research/experiments", json=_create_request()).json()
        client.post(f"/api/research/experiments/{created['id']}/run")

        resp = client.get(f"/api/research/experiments/{created['id']}/events")

        assert resp.status_code == 200
        body = resp.json()
        assert body["event_count"] == 1
        event = body["events"][0]
        assert event["symbol"] == "TSLA"
        assert event["success"] is True
        assert event["condition_value"] == pytest.approx(-0.02)

    def test_events_for_missing_experiment_returns_404(self):
        resp = client.get("/api/research/experiments/does-not-exist/events")
        assert resp.status_code == 404


class TestSymbolFiltering:
    def test_an_experiment_only_sees_its_own_symbols_bars(self):
        _seed_single_step_signal(symbol="TSLA")
        _seed_single_step_signal(symbol="NVDA")

        tsla_created = client.post("/api/research/experiments", json=_create_request(symbol="TSLA")).json()
        nvda_created = client.post("/api/research/experiments", json=_create_request(symbol="NVDA")).json()

        tsla_run = client.post(f"/api/research/experiments/{tsla_created['id']}/run").json()
        nvda_run = client.post(f"/api/research/experiments/{nvda_created['id']}/run").json()

        assert tsla_run["results"]["total_events"] == 1
        assert nvda_run["results"]["total_events"] == 1

        tsla_events = client.get(f"/api/research/experiments/{tsla_created['id']}/events").json()["events"]
        nvda_events = client.get(f"/api/research/experiments/{nvda_created['id']}/events").json()["events"]
        assert all(e["symbol"] == "TSLA" for e in tsla_events)
        assert all(e["symbol"] == "NVDA" for e in nvda_events)


class TestDateRangeFiltering:
    def test_an_experiment_only_sees_bars_inside_its_own_date_range(self):
        early_start = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
        late_start = datetime(2026, 3, 5, 14, 0, tzinfo=timezone.utc)
        _seed_single_step_signal(start=early_start)  # inside January
        _seed_single_step_signal(start=late_start)  # inside March

        january_only = client.post(
            "/api/research/experiments",
            json=_create_request(start_date="2026-01-01", end_date="2026-01-31"),
        ).json()

        run = client.post(f"/api/research/experiments/{january_only['id']}/run").json()

        # Only the January signal is inside this experiment's window --
        # the March signal must not be found.
        assert run["results"]["total_events"] == 1
        events = client.get(f"/api/research/experiments/{january_only['id']}/events").json()["events"]
        assert events[0]["signal_timestamp"].startswith("2026-01-05")


class TestReproducibility:
    def test_running_the_same_experiment_twice_gives_identical_results(self):
        _seed_single_step_signal()
        created = client.post("/api/research/experiments", json=_create_request()).json()

        first_run = client.post(f"/api/research/experiments/{created['id']}/run").json()
        first_events = client.get(f"/api/research/experiments/{created['id']}/events").json()

        second_run = client.post(f"/api/research/experiments/{created['id']}/run").json()
        second_events = client.get(f"/api/research/experiments/{created['id']}/events").json()

        assert first_run["results"] == second_run["results"]
        assert first_events == second_events

    def test_a_completed_experiment_preserves_its_original_parameters_across_a_rerun(self):
        _seed_single_step_signal()
        created = client.post("/api/research/experiments", json=_create_request()).json()
        client.post(f"/api/research/experiments/{created['id']}/run")

        rerun = client.post(f"/api/research/experiments/{created['id']}/run").json()

        assert rerun["symbol"] == created["symbol"]
        assert rerun["condition"] == created["condition"]
        assert rerun["outcome"] == created["outcome"]
        assert rerun["start_date"] == created["start_date"]
        assert rerun["end_date"] == created["end_date"]

    def test_a_completed_experiment_can_be_retrieved_later_by_id(self):
        _seed_single_step_signal()
        created = client.post("/api/research/experiments", json=_create_request()).json()
        client.post(f"/api/research/experiments/{created['id']}/run")

        retrieved = client.get(f"/api/research/experiments/{created['id']}").json()

        assert retrieved["status"] == "completed"
        assert retrieved["results"]["total_events"] == 1


class TestExampleExperiment:
    """The literal example from this feature's spec: TSLA declining
    >= 1% during the first 30 minutes (a "30m_return <= -1%" condition
    on 5-minute bars), predicting a further >= 0.5% decline over the
    following 60 minutes ("forward_return", 60-minute horizon,
    <= -0.5%). Uses a bar series long enough to contain at least one
    qualifying signal; exact per-signal arithmetic is already covered
    by tests/test_research_engine.py -- this test proves the whole
    stack (route -> engine -> repository) supports exactly this
    experiment shape end to end."""

    def test_tsla_early_selling_continuation(self):
        start = datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc)
        closes = [100.0] * 6 + [98.0] + [98.0 - 0.3 * i for i in range(1, 13)] + [90.0] * 5
        bars = [_bar("TSLA", start + timedelta(minutes=5 * i), close) for i, close in enumerate(closes)]
        save_bars(bars)

        create_resp = client.post(
            "/api/research/experiments",
            json={
                "name": "TSLA Early Selling Continuation",
                "hypothesis": (
                    "When TSLA declines >= 1% during the first 30 minutes, "
                    "TSLA will decline another >= 0.5% during the following 60 minutes."
                ),
                "symbol": "TSLA",
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
                "timeframe": "5m",
                "provider": "csv",
                "condition": {"metric": "30m_return", "operator": "<=", "threshold": -0.01},
                "outcome": {"metric": "forward_return", "horizon_minutes": 60, "operator": "<=", "threshold": -0.005},
            },
        )
        assert create_resp.status_code == 200
        experiment = create_resp.json()
        assert experiment["status"] == "draft"

        run_resp = client.post(f"/api/research/experiments/{experiment['id']}/run")
        assert run_resp.status_code == 200
        completed = run_resp.json()

        assert completed["status"] == "completed"
        results = completed["results"]
        assert results["total_events"] >= 1
        assert results["total_events"] == results["successful_events"] + results["failed_events"]
        if results["total_events"] > 0:
            assert 0.0 <= results["success_rate"] <= 1.0

        events_resp = client.get(f"/api/research/experiments/{experiment['id']}/events")
        events = events_resp.json()["events"]
        assert len(events) == results["total_events"]
        for event in events:
            assert event["symbol"] == "TSLA"
            assert event["condition_value"] <= -0.01  # every event genuinely satisfied the condition
            assert event["success"] == (event["outcome_value"] <= -0.005)  # classification matches the outcome definition
