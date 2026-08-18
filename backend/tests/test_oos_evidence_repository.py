"""Tests for app/storage/oos_evidence_repository.py -- the ONLY module
that writes SQL for `experiment_oos_periods` (against a real tmp_path
SQLite database, matching every other repository test in this suite).

Covers: save/get/list round-trip, idempotent duplicate INSERT OR
IGNORE, per-experiment isolation, and that no update/replace function
exists (immutability -- a period, once registered, cannot be mutated).
"""

from datetime import datetime, timezone

from app.models.oos_evidence import OOSPeriod
from app.storage import oos_evidence_repository

SYMBOL, TIMEFRAME, PROVIDER = "TSLA", "5m", "csv"


def _period(experiment_id: str, oos_partition_id: str, *, oos_start, oos_end, label=None) -> OOSPeriod:
    return OOSPeriod(
        id=oos_partition_id, experiment_id=experiment_id, oos_partition_id=oos_partition_id,
        symbol=SYMBOL, timeframe=TIMEFRAME, provider=PROVIDER,
        oos_start=oos_start, oos_end=oos_end, label=label, registered_at=datetime.now(timezone.utc),
    )


class TestSaveAndGet:
    def test_round_trips_every_field(self, tmp_path):
        db_path = tmp_path / "oos_evidence.db"
        period = _period(
            "exp-1", "partition-a",
            oos_start=datetime(2024, 1, 3, tzinfo=timezone.utc), oos_end=datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc),
            label="second walk-forward window",
        )
        inserted = oos_evidence_repository.save_period(period, db_path=db_path)
        assert inserted is True

        fetched = oos_evidence_repository.get_period("exp-1", "partition-a", db_path=db_path)
        assert fetched == period

    def test_get_missing_period_returns_none(self, tmp_path):
        db_path = tmp_path / "oos_evidence.db"
        assert oos_evidence_repository.get_period("does-not-exist", "also-missing", db_path=db_path) is None


class TestIdempotentDuplicateInsert:
    def test_registering_the_same_pair_twice_does_not_insert_a_second_row(self, tmp_path):
        db_path = tmp_path / "oos_evidence.db"
        period = _period("exp-1", "partition-a", oos_start=datetime(2024, 1, 3, tzinfo=timezone.utc), oos_end=datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc))
        first_insert = oos_evidence_repository.save_period(period, db_path=db_path)
        second_insert = oos_evidence_repository.save_period(period, db_path=db_path)

        assert first_insert is True
        assert second_insert is False
        assert len(oos_evidence_repository.list_periods("exp-1", db_path=db_path)) == 1


class TestListPeriods:
    def test_lists_every_period_for_an_experiment_earliest_first(self, tmp_path):
        db_path = tmp_path / "oos_evidence.db"
        later = _period("exp-1", "partition-b", oos_start=datetime(2024, 1, 10, tzinfo=timezone.utc), oos_end=datetime(2024, 1, 10, 4, 0, tzinfo=timezone.utc))
        earlier = _period("exp-1", "partition-a", oos_start=datetime(2024, 1, 3, tzinfo=timezone.utc), oos_end=datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc))
        oos_evidence_repository.save_period(later, db_path=db_path)
        oos_evidence_repository.save_period(earlier, db_path=db_path)

        periods = oos_evidence_repository.list_periods("exp-1", db_path=db_path)
        assert [p.oos_partition_id for p in periods] == ["partition-a", "partition-b"]

    def test_periods_are_isolated_per_experiment(self, tmp_path):
        db_path = tmp_path / "oos_evidence.db"
        oos_evidence_repository.save_period(
            _period("exp-1", "partition-a", oos_start=datetime(2024, 1, 3, tzinfo=timezone.utc), oos_end=datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc)),
            db_path=db_path,
        )
        oos_evidence_repository.save_period(
            _period("exp-2", "partition-b", oos_start=datetime(2024, 1, 3, tzinfo=timezone.utc), oos_end=datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc)),
            db_path=db_path,
        )

        assert [p.oos_partition_id for p in oos_evidence_repository.list_periods("exp-1", db_path=db_path)] == ["partition-a"]
        assert [p.oos_partition_id for p in oos_evidence_repository.list_periods("exp-2", db_path=db_path)] == ["partition-b"]

    def test_no_periods_returns_an_empty_list(self, tmp_path):
        db_path = tmp_path / "oos_evidence.db"
        assert oos_evidence_repository.list_periods("exp-1", db_path=db_path) == []


class TestImmutability:
    def test_the_module_exposes_no_update_or_replace_function(self):
        """Structural proof, not a convention someone has to remember:
        there is no function DEFINED IN this module (as opposed to
        merely imported into it, e.g. get_connection) capable of
        mutating an already-saved period -- the same "append-only,
        checked by the absence of a mutation function" guarantee
        app/storage/oos_evaluation_repository.py's own module docstring
        establishes for `oos_evaluations`."""
        functions_defined_here = {
            name
            for name in dir(oos_evidence_repository)
            if not name.startswith("_")
            and callable(getattr(oos_evidence_repository, name))
            and getattr(getattr(oos_evidence_repository, name), "__module__", None) == oos_evidence_repository.__name__
        }
        assert functions_defined_here == {"save_period", "get_period", "list_periods"}
