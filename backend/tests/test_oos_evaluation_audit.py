"""OOS Evaluation V1 Audit (2026-08-18) -- adversarial verification
tests beyond tests/test_oos_evaluation_engine.py's own coverage.
Organized by the audit's own section numbers:

  1. Holdout access (sole access path, no fetch-then-slice, no writes)
  3. Boundary conditions (final-bar signal, insufficient future bars,
     outcome exactly at/beyond holdout_end, timestamp equality)
  4. Backtest semantics (mutable Experiment cannot alter a frozen
     evaluation's inputs)
  5. Provenance independence (mutating the live row after freezing
     cannot change an ALREADY-PERSISTED evaluation's interpretation)
  6. Repeatability (identical hash/timestamps/outcomes on re-run)
  7. Failure safety (a pipeline-stage failure never reaches
     OOS_EVALUATED, never persists partial signals, a subsequent
     success is still possible)

Section 2 (feature warm-up) is covered by tests/test_oos_evaluation_warmup.py
(including the audit's own gap-leak finding + fix). Section 8
(real-data validation) was run manually against a copy of this lab's
real `backend/data/historical_bars.db` (10 real Alpaca TSLA daily
bars) -- not repeated here as an automated test since it depends on
that specific file's contents, not a synthetic fixture.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

from app.models.market_data import HistoricalBar
from app.models.oos_evaluation import OOSEvaluationStatus
from app.models.oos_partition import OOSPartition, OOSPartitionCreateRequest
from app.models.research import ConditionOperator, Experiment, ExperimentCreateRequest, ExperimentLifecycleState, FeatureCondition, FeatureConditionOperator, Outcome
from app.oos_evaluation.engine import evaluate_oos
from app.research.lifecycle import build_freeze_snapshot, compute_hypothesis_hash
from app.storage import experiment_freeze_repository, historical_bar_repository, oos_evaluation_repository, oos_partition_repository, research_repository

SYMBOL, TIMEFRAME, PROVIDER = "TSLA", "5m", "csv"
DEVELOPMENT_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
HOLDOUT_START = datetime(2024, 1, 2, tzinfo=timezone.utc)
HOLDOUT_END = datetime(2024, 1, 2, 4, 0, tzinfo=timezone.utc)  # 48 5-minute bars, index 0..47
DEVELOPMENT_END = HOLDOUT_START - timedelta(microseconds=1)

_MA50_CONDITION = [FeatureCondition(feature_id="price_position.ma50_distance", operator=FeatureConditionOperator.GT, value=-1.0)]


def _bars(start: datetime, count: int, *, base_price=100.0, price_step=0.01) -> list[HistoricalBar]:
    return [
        HistoricalBar(
            symbol=SYMBOL, timestamp=start + timedelta(minutes=5 * i),
            open=base_price + i * price_step, high=base_price + i * price_step + 0.05,
            low=base_price + i * price_step - 0.05, close=base_price + i * price_step,
            volume=1_000, provider=PROVIDER, timeframe=TIMEFRAME,
        )
        for i in range(count)
    ]


def _seed_full_scenario(db_path, *, horizon_minutes=5) -> tuple[OOSPartition, Experiment]:
    development_bars = _bars(DEVELOPMENT_START, 288)
    holdout_bars = _bars(HOLDOUT_START, 48, base_price=development_bars[-1].close + 0.01)
    historical_bar_repository.save_bars(development_bars, db_path=db_path)
    historical_bar_repository.save_bars(holdout_bars, db_path=db_path)

    partition = OOSPartition.new(
        OOSPartitionCreateRequest(
            symbol=SYMBOL, timeframe=TIMEFRAME, provider=PROVIDER,
            development_start=DEVELOPMENT_START, development_end=DEVELOPMENT_END,
            holdout_start=HOLDOUT_START, holdout_end=HOLDOUT_END,
        )
    )
    oos_partition_repository.save_partition(partition, db_path=db_path)

    outcome = Outcome(metric="forward_return", horizon_minutes=horizon_minutes, operator=ConditionOperator.GT, threshold=-999.0)
    request = ExperimentCreateRequest(
        name="Audit scenario", hypothesis="h", symbol=SYMBOL, start_date="2024-01-01", end_date="2024-01-01",
        timeframe=TIMEFRAME, provider=PROVIDER, conditions=_MA50_CONDITION, outcome=outcome,
    )
    experiment = Experiment.new(request)
    research_repository.save_experiment(experiment, db_path=db_path)
    research_repository.set_oos_partition(experiment.id, partition.id, db_path=db_path)
    experiment = research_repository.get_experiment(experiment.id, db_path=db_path)

    frozen_at = datetime.now(timezone.utc)
    hypothesis_hash = compute_hypothesis_hash(experiment)
    snapshot = build_freeze_snapshot(experiment, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at)
    experiment_freeze_repository.save_snapshot(snapshot, db_path=db_path)
    research_repository.freeze_experiment(
        experiment.id, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at, oos_partition_id=partition.id, db_path=db_path
    )
    return partition, research_repository.get_experiment(experiment.id, db_path=db_path)


def _table_row_counts(db_path) -> dict:
    conn = sqlite3.connect(db_path)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'") if r[0] != "sqlite_sequence"]
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}  # noqa: S608 -- table names come from sqlite_master, not user input
    conn.close()
    return counts


class TestSection1HoldoutAccessTrace:
    def test_engine_module_reads_holdout_bars_through_exactly_one_call_site(self):
        """Static trace: app/oos_evaluation/engine.py must call
        app.oos.access.get_holdout_bars(...) exactly once, and every
        other bar read in the file must go through
        historical_bar_repository.get_bars_in_range() with an
        explicit, pre-computed start/end (never an unbounded read that
        is filtered/sliced afterward)."""
        import inspect

        from app.oos_evaluation import engine as engine_module

        source = inspect.getsource(engine_module)
        pipeline_source = inspect.getsource(engine_module._run_pipeline)
        assert pipeline_source.count("get_holdout_bars(") == 1  # the only function that reads any bars at all
        assert "confirm_oos_validation_use=True" in pipeline_source
        # Every OTHER bar read in this file goes through
        # get_bars_in_range() (timestamp-bounded), never the whole-day
        # date-bounded get_bars() (which this module never imports at
        # all) -- so there is no code path here that could read a
        # broader range and filter/slice it down afterward.
        assert "get_bars_in_range(" in source
        assert "historical_bar_repository.get_bars(" not in source
        assert "import historical_bar_repository" in source or "storage import" in source
        assert "ORDER BY" not in source  # this module has no SQL of its own at all -- every read goes through a repository function

    def test_evaluate_oos_writes_nothing_to_any_table(self, tmp_path):
        """evaluate_oos() must be read-only end to end -- persistence
        is the caller's job (app/api/oos_evaluation.py). Proven by
        comparing every table's row count before and after a full,
        successful evaluation call."""
        db_path = tmp_path / "audit.db"
        _partition, experiment = _seed_full_scenario(db_path)

        before = _table_row_counts(db_path)
        result, signals = evaluate_oos(experiment.id, db_path=db_path)
        after = _table_row_counts(db_path)

        assert result.status == OOSEvaluationStatus.COMPLETED
        assert signals  # sanity: the scenario actually produced signals
        assert before == after  # not one row inserted/updated/deleted anywhere

    def test_development_and_holdout_reads_never_overlap_in_timestamp(self, tmp_path):
        """Direct proof that the warm-up read and the holdout read can
        never return the same bar twice -- would silently double-count
        a bar as both 'warm-up context' and 'an eligible OOS
        observation' if they did."""
        db_path = tmp_path / "audit.db"
        _partition, experiment = _seed_full_scenario(db_path)
        _result, signals = evaluate_oos(experiment.id, db_path=db_path)

        signal_timestamps = {s.signal_timestamp for s in signals}
        assert signal_timestamps, "expected signals to compare"
        assert all(ts >= HOLDOUT_START for ts in signal_timestamps)
        # No development-side timestamp ever appears among the signals.
        assert not (signal_timestamps & {DEVELOPMENT_START, DEVELOPMENT_START + timedelta(minutes=5)})


class TestSection3BoundaryConditions:
    def test_a_signal_on_the_final_holdout_bar_produces_no_signal(self, tmp_path):
        """The condition is true at the LAST holdout bar too (smooth
        increasing series) -- but there is no bar AFTER it within
        `holdout_bars` to enter at, so no signal is produced there."""
        db_path = tmp_path / "audit.db"
        _partition, experiment = _seed_full_scenario(db_path)
        _result, signals = evaluate_oos(experiment.id, db_path=db_path)

        last_holdout_bar_timestamp = HOLDOUT_START + timedelta(minutes=5 * 47)
        assert last_holdout_bar_timestamp == HOLDOUT_END - timedelta(minutes=5)  # sanity on the fixture's own bar count
        assert last_holdout_bar_timestamp not in {s.signal_timestamp for s in signals}

    def test_insufficient_future_bars_omits_the_outcome_not_fabricates_it(self, tmp_path):
        """A signal near the end of holdout, whose configured window
        would require a bar past holdout_end, must have that window
        simply absent from its outcomes -- never estimated."""
        db_path = tmp_path / "audit.db"
        # horizon 15 minutes = 3 bars forward from entry -- a signal at
        # holdout index 44 (2nd-to-last that can even get an entry
        # bar+1) needs outcome_index = entry_index(45)+3=48, which does
        # not exist (only indices 0..47 exist) -- must be omitted.
        _partition, experiment = _seed_full_scenario(db_path, horizon_minutes=15)
        result, signals = evaluate_oos(experiment.id, db_path=db_path)

        assert result.status == OOSEvaluationStatus.COMPLETED
        last_signal = max(signals, key=lambda s: s.signal_timestamp)
        # No signal exists whose entry_index + 3 bars would exceed the
        # 48-bar holdout dataset -- confirmed by construction (every
        # returned signal actually HAS an outcome, never an empty list,
        # since run_backtest() drops a signal entirely if zero windows
        # were measurable).
        for signal in signals:
            for outcome in signal.outcomes:
                assert outcome.outcome_timestamp <= HOLDOUT_END

    def test_outcome_exactly_at_holdout_end_is_included(self, tmp_path):
        """A signal whose outcome bar lands EXACTLY on the last holdout
        bar (== holdout_end's own bar) must be included, not
        conservatively dropped."""
        db_path = tmp_path / "audit.db"
        _partition, experiment = _seed_full_scenario(db_path, horizon_minutes=5)  # 1-bar window
        _result, signals = evaluate_oos(experiment.id, db_path=db_path)

        last_possible_outcome_timestamp = HOLDOUT_START + timedelta(minutes=5 * 47)  # the actual last holdout bar
        outcome_timestamps = {o.outcome_timestamp for s in signals for o in s.outcomes}
        assert last_possible_outcome_timestamp in outcome_timestamps  # reached, not held back defensively

    def test_no_outcome_ever_exceeds_holdout_end(self, tmp_path):
        db_path = tmp_path / "audit.db"
        _partition, experiment = _seed_full_scenario(db_path, horizon_minutes=5)
        _result, signals = evaluate_oos(experiment.id, db_path=db_path)
        for s in signals:
            for o in s.outcomes:
                assert o.outcome_timestamp <= HOLDOUT_END  # "one bar beyond holdout_end" never occurs -- no such bar exists to read

    def test_a_bar_exactly_at_holdout_start_is_never_double_counted(self, tmp_path):
        """The bar AT holdout_start is holdout, never warm-up -- proven
        by the warm-up query's own exclusive upper bound
        (holdout_start - 1 microsecond)."""
        db_path = tmp_path / "audit.db"
        _partition, experiment = _seed_full_scenario(db_path)
        _result, signals = evaluate_oos(experiment.id, db_path=db_path)
        assert signals[0].signal_timestamp == HOLDOUT_START  # first eligible signal IS the boundary bar itself


class TestSection4MutableExperimentCannotAlterFrozenInputs:
    def test_mutating_conditions_after_freeze_does_not_change_the_evaluation(self, tmp_path):
        """Directly UPDATEs `experiments.conditions_json` via raw SQL
        after freezing (bypassing every application-level guard) --
        proves evaluate_oos() still uses the immutable snapshot's
        ORIGINAL conditions, not the mutated live row, because it never
        reads live-row conditions at all."""
        db_path = tmp_path / "audit.db"
        _partition, experiment = _seed_full_scenario(db_path)
        original_hash = experiment.hypothesis_hash

        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE experiments SET conditions_json = ? WHERE id = ?",
            ('[{"feature_id": "price.return_60m", "operator": "<", "value": -0.9}]', experiment.id),
        )
        conn.commit()
        conn.close()

        result, signals = evaluate_oos(experiment.id, db_path=db_path)
        assert result.hypothesis_hash == original_hash  # unaffected by the mutated live row
        assert result.status == OOSEvaluationStatus.COMPLETED
        assert signals  # still evaluates the ORIGINAL ma50_distance condition, which fires plenty

    def test_mutating_symbol_timeframe_feature_contract_after_freeze_does_not_change_the_evaluation(self, tmp_path):
        db_path = tmp_path / "audit.db"
        _partition, experiment = _seed_full_scenario(db_path)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE experiments SET symbol = 'NVDA', timeframe = '1h', feature_contract_version = 'tampered' WHERE id = ?",
            (experiment.id,),
        )
        conn.commit()
        conn.close()

        result, _signals = evaluate_oos(experiment.id, db_path=db_path)
        assert result.symbol == SYMBOL  # NOT "NVDA"
        assert result.timeframe == TIMEFRAME  # NOT "1h"
        assert result.feature_contract_version != "tampered"
        assert result.status == OOSEvaluationStatus.COMPLETED

    def test_mutating_outcome_threshold_and_horizon_after_freeze_does_not_change_the_evaluation(self, tmp_path):
        """Outcome carries both the "threshold" (operator/threshold --
        unused by Backtesting semantics but still part of the frozen
        definition) and the "horizon" (horizon_minutes, which DOES
        drive the single window run_backtest() is invoked with) --
        tampering the live row's outcome_json must change neither."""
        db_path = tmp_path / "audit.db"
        _partition, experiment = _seed_full_scenario(db_path, horizon_minutes=5)
        original_window_bars = evaluate_oos(experiment.id, db_path=db_path)[0].outcome_window_bars

        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE experiments SET outcome_json = ? WHERE id = ?",
            ('{"metric": "forward_return", "horizon_minutes": 60, "operator": "<", "threshold": -0.5}', experiment.id),
        )
        conn.commit()
        conn.close()

        result, _signals = evaluate_oos(experiment.id, db_path=db_path)
        assert result.outcome_horizon_minutes == 5  # NOT 60 from the tampered row
        assert result.outcome_window_bars == original_window_bars  # NOT recomputed against the 60m horizon
        assert result.status == OOSEvaluationStatus.COMPLETED


class TestSection5ProvenanceIndependence:
    def test_mutating_the_live_row_after_persisting_an_evaluation_does_not_change_it(self, tmp_path):
        db_path = tmp_path / "audit.db"
        _partition, experiment = _seed_full_scenario(db_path)
        result, signals = evaluate_oos(experiment.id, db_path=db_path)
        oos_evaluation_repository.save_evaluation(result, signals, db_path=db_path)

        before = oos_evaluation_repository.get_evaluation(result.id, db_path=db_path)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE experiments SET symbol = 'NVDA', hypothesis_hash = 'tampered', lifecycle_state = 'archived' WHERE id = ?",
            (experiment.id,),
        )
        conn.commit()
        conn.close()

        after = oos_evaluation_repository.get_evaluation(result.id, db_path=db_path)
        assert after == before  # the persisted evaluation is completely independent of the live row

    def test_persisted_evaluation_independently_records_every_provenance_field(self, tmp_path):
        db_path = tmp_path / "audit.db"
        partition, experiment = _seed_full_scenario(db_path)
        result, signals = evaluate_oos(experiment.id, db_path=db_path)
        oos_evaluation_repository.save_evaluation(result, signals, db_path=db_path)

        fetched = oos_evaluation_repository.get_evaluation(result.id, db_path=db_path)
        assert fetched.experiment_id == experiment.id
        assert fetched.hypothesis_hash == experiment.hypothesis_hash
        assert fetched.frozen_snapshot_id == experiment.id
        assert fetched.oos_partition_id == partition.id
        assert fetched.feature_contract_version == experiment.feature_contract_version
        assert (fetched.holdout_start, fetched.holdout_end) == (partition.holdout_start, partition.holdout_end)
        assert fetched.signal_count == len(signals)
        assert fetched.results is not None


class TestSection6Repeatability:
    def test_two_runs_against_unchanged_holdout_data_are_fully_identical_analytically(self, tmp_path):
        db_path = tmp_path / "audit.db"
        _partition, experiment = _seed_full_scenario(db_path)

        result_1, signals_1 = evaluate_oos(experiment.id, db_path=db_path)
        oos_evaluation_repository.save_evaluation(result_1, signals_1, db_path=db_path)
        result_2, signals_2 = evaluate_oos(experiment.id, db_path=db_path)
        oos_evaluation_repository.save_evaluation(result_2, signals_2, db_path=db_path)

        assert result_1.hypothesis_hash == result_2.hypothesis_hash
        assert result_1.results == result_2.results
        assert [s.signal_timestamp for s in signals_1] == [s.signal_timestamp for s in signals_2]
        assert [s.outcomes for s in signals_1] == [s.outcomes for s in signals_2]
        assert result_1.id != result_2.id  # new record each time

        # The first evaluation is untouched by the second's persistence.
        reloaded_first = oos_evaluation_repository.get_evaluation(result_1.id, db_path=db_path)
        assert reloaded_first == result_1
        both = oos_evaluation_repository.list_evaluations(experiment.id, db_path=db_path)
        assert len(both) == 2  # no overwrite, no dedup


class TestSection7FailureSafety:
    def test_a_pipeline_failure_does_not_advance_the_lifecycle_and_a_later_success_still_works(self, tmp_path, monkeypatch):
        db_path = tmp_path / "audit.db"
        _partition, experiment = _seed_full_scenario(db_path)

        import app.oos_evaluation.engine as engine_module

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated Feature Engine failure")

        monkeypatch.setattr(engine_module, "compute_features", _boom)
        failed_result, failed_signals = evaluate_oos(experiment.id, db_path=db_path)

        assert failed_result.status == OOSEvaluationStatus.FAILED
        assert failed_result.error_message and "simulated Feature Engine failure" in failed_result.error_message
        assert failed_signals == []  # no partial signals
        assert failed_result.results is None
        # Provenance identity is still fully populated even on failure.
        assert failed_result.hypothesis_hash == experiment.hypothesis_hash
        assert failed_result.experiment_id == experiment.id

        # Persist it the same way the real API route would, and confirm
        # the lifecycle is untouched by a FAILED result.
        oos_evaluation_repository.save_evaluation(failed_result, failed_signals, db_path=db_path)
        still_frozen = research_repository.get_experiment(experiment.id, db_path=db_path)
        assert still_frozen.lifecycle_state == ExperimentLifecycleState.FROZEN  # never advanced

        monkeypatch.undo()  # remove the fault
        success_result, success_signals = evaluate_oos(experiment.id, db_path=db_path)
        assert success_result.status == OOSEvaluationStatus.COMPLETED
        assert success_signals  # a subsequent successful evaluation IS still possible

        oos_evaluation_repository.save_evaluation(success_result, success_signals, db_path=db_path)
        research_repository.mark_oos_evaluated(experiment.id, oos_evaluated_at=success_result.evaluated_at, db_path=db_path)
        final = research_repository.get_experiment(experiment.id, db_path=db_path)
        assert final.lifecycle_state == ExperimentLifecycleState.OOS_EVALUATED

        history = oos_evaluation_repository.list_evaluations(experiment.id, db_path=db_path)
        assert len(history) == 2  # the FAILED attempt is preserved in history, not erased
        statuses = {e.status for e in history}
        assert statuses == {OOSEvaluationStatus.FAILED, OOSEvaluationStatus.COMPLETED}

    def test_api_route_never_transitions_lifecycle_on_a_failed_evaluation(self, tmp_path, monkeypatch):
        """Same proof, through the real HTTP route (app/api/oos_evaluation.py),
        not just the bare engine function. Uses the same
        monkeypatch.setenv("DATABASE_PATH", ...) isolation convention as
        every other API test file (tests/test_oos_evaluation_api.py
        etc.) -- app.storage.db.get_connection() reads
        config.get_database_path() fresh on every call, so the already-
        imported `app.main.app` picks up the new path with no reload
        needed."""
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "audit_api.db"))
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        client = TestClient(fastapi_app)

        response = client.post(
            "/api/oos/partitions",
            json={
                "symbol": SYMBOL, "timeframe": TIMEFRAME, "provider": PROVIDER,
                "development_start": DEVELOPMENT_START.isoformat(), "development_end": DEVELOPMENT_END.isoformat(),
                "holdout_start": HOLDOUT_START.isoformat(), "holdout_end": HOLDOUT_END.isoformat(),
            },
        )
        assert response.status_code == 200, response.text
        partition_id = response.json()["id"]

        development_bars = _bars(DEVELOPMENT_START, 288)
        holdout_bars = _bars(HOLDOUT_START, 48, base_price=development_bars[-1].close + 0.01)
        historical_bar_repository.save_bars(development_bars, db_path=str(tmp_path / "audit_api.db"))
        historical_bar_repository.save_bars(holdout_bars, db_path=str(tmp_path / "audit_api.db"))

        experiment_response = client.post(
            "/api/research/experiments",
            json={
                "name": "audit", "hypothesis": "h", "symbol": SYMBOL, "start_date": "2024-01-01", "end_date": "2024-01-01",
                "timeframe": TIMEFRAME, "provider": PROVIDER,
                "conditions": [{"feature_id": "price_position.ma50_distance", "operator": ">", "value": -1.0}],
                "outcome": {"metric": "forward_return", "horizon_minutes": 5, "operator": ">", "threshold": -999.0},
            },
        )
        experiment_id = experiment_response.json()["id"]
        client.post(f"/api/research/experiments/{experiment_id}/oos-partition", json={"oos_partition_id": partition_id})
        client.post(f"/api/research/experiments/{experiment_id}/freeze")

        import app.oos_evaluation.engine as engine_module

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated failure via HTTP")

        monkeypatch.setattr(engine_module, "run_backtest", _boom)
        response = client.post(f"/api/research/experiments/{experiment_id}/oos-evaluate")
        assert response.status_code == 200  # a pipeline failure is still a 200 with status=failed, not a 500
        body = response.json()
        assert body["status"] == "failed"

        reloaded = client.get(f"/api/research/experiments/{experiment_id}").json()
        assert reloaded["lifecycle_state"] == "frozen"  # NOT oos_evaluated
