"""Tests for app/oos/partition.py (classify_range / require_development_range)
and app/oos/access.py (get_development_bars / get_holdout_bars) -- the
leakage-guard and explicit-consumption-boundary logic (requirements
2/3)."""

from datetime import datetime, timezone

import pytest

from app.models.market_data import HistoricalBar
from app.models.oos_partition import OOSPartition, OOSPartitionCreateRequest
from app.oos.access import HoldoutAccessError, get_development_bars, get_holdout_bars
from app.oos.partition import DatasetSegment, PartitionLeakageError, classify_range, require_development_range
from app.storage.historical_bar_repository import save_bars


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


def _bar(timestamp: datetime, *, symbol="TSLA", timeframe="5m", provider="csv", close=100.0) -> HistoricalBar:
    return HistoricalBar(
        symbol=symbol, timestamp=timestamp, open=close, high=close, low=close, close=close, volume=100,
        provider=provider, timeframe=timeframe,
    )


class TestClassifyRange:
    def test_range_entirely_inside_development_is_classified_development(self):
        partition = _partition()
        segment = classify_range(
            partition, datetime(2024, 2, 1, tzinfo=timezone.utc), datetime(2024, 3, 1, tzinfo=timezone.utc)
        )
        assert segment == DatasetSegment.DEVELOPMENT

    def test_range_entirely_inside_holdout_is_classified_holdout(self):
        partition = _partition()
        segment = classify_range(
            partition, datetime(2024, 8, 1, tzinfo=timezone.utc), datetime(2024, 9, 1, tzinfo=timezone.utc)
        )
        assert segment == DatasetSegment.HOLDOUT

    def test_range_spanning_both_windows_is_mixed_and_classified_as_none(self):
        partition = _partition()
        segment = classify_range(
            partition, datetime(2024, 6, 1, tzinfo=timezone.utc), datetime(2024, 8, 1, tzinfo=timezone.utc)
        )
        assert segment is None

    def test_range_outside_the_partition_entirely_is_none(self):
        partition = _partition()
        segment = classify_range(
            partition, datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2023, 6, 1, tzinfo=timezone.utc)
        )
        assert segment is None

    def test_malformed_range_start_after_end_is_none(self):
        partition = _partition()
        segment = classify_range(
            partition, datetime(2024, 3, 1, tzinfo=timezone.utc), datetime(2024, 2, 1, tzinfo=timezone.utc)
        )
        assert segment is None


class TestRequireDevelopmentRange:
    def test_a_development_only_range_passes_silently(self):
        partition = _partition()
        require_development_range(
            partition, datetime(2024, 2, 1, tzinfo=timezone.utc), datetime(2024, 3, 1, tzinfo=timezone.utc)
        )  # no raise

    def test_a_range_touching_holdout_is_rejected(self):
        partition = _partition()
        with pytest.raises(PartitionLeakageError):
            require_development_range(
                partition, datetime(2024, 6, 1, tzinfo=timezone.utc), datetime(2024, 8, 1, tzinfo=timezone.utc)
            )

    def test_a_holdout_only_range_is_rejected(self):
        partition = _partition()
        with pytest.raises(PartitionLeakageError):
            require_development_range(
                partition, datetime(2024, 8, 1, tzinfo=timezone.utc), datetime(2024, 9, 1, tzinfo=timezone.utc)
            )


class TestSegmentBarAccess:
    def test_development_bars_never_include_holdout_bars(self, tmp_path):
        db_path = tmp_path / "oos.db"
        partition = _partition()
        save_bars(
            [
                _bar(datetime(2024, 3, 1, tzinfo=timezone.utc)),  # development
                _bar(datetime(2024, 8, 1, tzinfo=timezone.utc)),  # holdout
            ],
            db_path=db_path,
        )
        development_bars = get_development_bars(partition, db_path=db_path)
        assert len(development_bars) == 1
        assert development_bars[0].timestamp == datetime(2024, 3, 1, tzinfo=timezone.utc)

    def test_holdout_bars_are_refused_without_explicit_confirmation(self, tmp_path):
        db_path = tmp_path / "oos.db"
        partition = _partition()
        save_bars([_bar(datetime(2024, 8, 1, tzinfo=timezone.utc))], db_path=db_path)

        with pytest.raises(HoldoutAccessError):
            get_holdout_bars(partition, confirm_oos_validation_use=False, db_path=db_path)

    def test_holdout_bars_are_returned_once_explicitly_confirmed(self, tmp_path):
        db_path = tmp_path / "oos.db"
        partition = _partition()
        save_bars(
            [
                _bar(datetime(2024, 3, 1, tzinfo=timezone.utc)),  # development
                _bar(datetime(2024, 8, 1, tzinfo=timezone.utc)),  # holdout
            ],
            db_path=db_path,
        )
        holdout_bars = get_holdout_bars(partition, confirm_oos_validation_use=True, db_path=db_path)
        assert len(holdout_bars) == 1
        assert holdout_bars[0].timestamp == datetime(2024, 8, 1, tzinfo=timezone.utc)
