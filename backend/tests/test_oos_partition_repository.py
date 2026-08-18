"""Tests for app/storage/oos_partition_repository.py -- the only module
that writes SQL for `oos_partitions`. Every test uses an explicit
db_path pointing at a pytest tmp_path file, matching
tests/test_backtest_repository.py's own isolation convention."""

from datetime import datetime, timezone

from app.models.oos_partition import OOSPartition, OOSPartitionCreateRequest
from app.storage.oos_partition_repository import get_partition, list_partitions, save_partition


def _partition(**overrides) -> OOSPartition:
    fields = {
        "symbol": "TSLA",
        "timeframe": "5m",
        "provider": "csv",
        "development_start": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "development_end": datetime(2024, 6, 30, tzinfo=timezone.utc),
        "holdout_start": datetime(2024, 7, 1, tzinfo=timezone.utc),
        "holdout_end": datetime(2024, 12, 31, tzinfo=timezone.utc),
    }
    fields.update(overrides)
    return OOSPartition.new(OOSPartitionCreateRequest(**fields))


class TestSaveAndGet:
    def test_a_saved_partition_round_trips_exactly(self, tmp_path):
        db_path = tmp_path / "oos.db"
        partition = _partition(label="2024 H1/H2 split")
        assert save_partition(partition, db_path=db_path) is True

        fetched = get_partition(partition.id, db_path=db_path)
        assert fetched == partition

    def test_getting_an_unknown_id_returns_none(self, tmp_path):
        db_path = tmp_path / "oos.db"
        assert get_partition("does-not-exist", db_path=db_path) is None


class TestIdempotentSave:
    def test_saving_the_same_partition_twice_does_not_duplicate_it(self, tmp_path):
        db_path = tmp_path / "oos.db"
        partition = _partition()

        first_insert = save_partition(partition, db_path=db_path)
        second_insert = save_partition(_partition(), db_path=db_path)  # independently constructed, same inputs

        assert first_insert is True
        assert second_insert is False  # already existed -- ignored, not duplicated
        assert len(list_partitions(db_path=db_path)) == 1

    def test_the_original_created_at_is_preserved_on_a_no_op_resave(self, tmp_path):
        db_path = tmp_path / "oos.db"
        first = _partition()
        save_partition(first, db_path=db_path)

        later = _partition()
        later.created_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
        save_partition(later, db_path=db_path)

        assert get_partition(first.id, db_path=db_path).created_at == first.created_at


class TestListPartitions:
    def test_list_is_newest_first(self, tmp_path):
        db_path = tmp_path / "oos.db"
        older = _partition(symbol="TSLA")
        older.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        newer = _partition(symbol="NVDA")
        newer.created_at = datetime(2025, 6, 1, tzinfo=timezone.utc)
        save_partition(older, db_path=db_path)
        save_partition(newer, db_path=db_path)

        results = list_partitions(db_path=db_path)
        assert [p.id for p in results] == [newer.id, older.id]

    def test_list_can_be_filtered_by_symbol(self, tmp_path):
        db_path = tmp_path / "oos.db"
        save_partition(_partition(symbol="TSLA"), db_path=db_path)
        save_partition(_partition(symbol="NVDA"), db_path=db_path)

        results = list_partitions(symbol="NVDA", db_path=db_path)
        assert [p.symbol for p in results] == ["NVDA"]
