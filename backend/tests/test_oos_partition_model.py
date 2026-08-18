"""Tests for app/models/oos_partition.py: structural range validation
(requirements 1/3) and deterministic identity (requirement 5)."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.oos_partition import OOSPartition, OOSPartitionCreateRequest, compute_partition_id


def _request(**overrides) -> OOSPartitionCreateRequest:
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
    return OOSPartitionCreateRequest(**fields)


class TestValidPartition:
    def test_a_well_formed_request_is_accepted(self):
        request = _request()
        partition = OOSPartition.new(request)
        assert partition.symbol == "TSLA"
        assert partition.development_end < partition.holdout_start
        assert partition.id  # deterministic id was assigned

    def test_symbol_is_upper_cased(self):
        partition = OOSPartition.new(_request(symbol="tsla"))
        assert partition.symbol == "TSLA"


class TestOverlapAndOrderingRejected:
    def test_overlapping_development_and_holdout_is_rejected(self):
        with pytest.raises(ValidationError, match="overlapping or touching"):
            _request(
                development_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                development_end=datetime(2024, 8, 1, tzinfo=timezone.utc),
                holdout_start=datetime(2024, 7, 1, tzinfo=timezone.utc),
                holdout_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            )

    def test_development_after_holdout_is_rejected(self):
        with pytest.raises(ValidationError, match="strictly before"):
            _request(
                development_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
                development_end=datetime(2025, 6, 30, tzinfo=timezone.utc),
                holdout_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                holdout_end=datetime(2024, 6, 30, tzinfo=timezone.utc),
            )

    def test_inverted_development_range_is_rejected(self):
        with pytest.raises(ValidationError, match="development_start"):
            _request(
                development_start=datetime(2024, 6, 30, tzinfo=timezone.utc),
                development_end=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )

    def test_inverted_holdout_range_is_rejected(self):
        with pytest.raises(ValidationError, match="holdout_start"):
            _request(
                holdout_start=datetime(2024, 12, 31, tzinfo=timezone.utc),
                holdout_end=datetime(2024, 7, 1, tzinfo=timezone.utc),
            )


class TestBoundaryTimestamps:
    def test_development_end_exactly_equal_to_holdout_start_is_rejected(self):
        boundary = datetime(2024, 6, 30, 16, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(ValidationError, match="overlapping or touching"):
            _request(development_end=boundary, holdout_start=boundary)

    def test_holdout_start_one_microsecond_after_development_end_is_accepted(self):
        development_end = datetime(2024, 6, 30, 16, 0, 0, tzinfo=timezone.utc)
        holdout_start = development_end.replace(microsecond=1)
        partition = OOSPartition.new(
            _request(development_end=development_end, holdout_start=holdout_start, holdout_end=holdout_start.replace(day=1, month=7))
        )
        assert partition.development_end < partition.holdout_start

    def test_naive_timestamps_are_normalized_to_utc(self):
        partition = OOSPartition.new(
            _request(
                development_start=datetime(2024, 1, 1),
                development_end=datetime(2024, 6, 30),
                holdout_start=datetime(2024, 7, 1),
                holdout_end=datetime(2024, 12, 31),
            )
        )
        assert partition.development_start.tzinfo is not None


class TestMissingMetadata:
    def test_blank_symbol_is_rejected(self):
        with pytest.raises(ValidationError):
            _request(symbol="   ")

    def test_blank_provider_is_rejected(self):
        with pytest.raises(ValidationError):
            _request(provider="")

    def test_missing_required_field_is_rejected(self):
        with pytest.raises(ValidationError):
            OOSPartitionCreateRequest(
                symbol="TSLA", timeframe="5m", provider="csv", development_start=datetime.now(timezone.utc)
            )


class TestDeterministicIdentity:
    def test_same_inputs_produce_the_same_id(self):
        id_a = compute_partition_id(
            provider="csv",
            symbol="TSLA",
            timeframe="5m",
            development_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            development_end=datetime(2024, 6, 30, tzinfo=timezone.utc),
            holdout_start=datetime(2024, 7, 1, tzinfo=timezone.utc),
            holdout_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        id_b = compute_partition_id(
            provider="csv",
            symbol="TSLA",
            timeframe="5m",
            development_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            development_end=datetime(2024, 6, 30, tzinfo=timezone.utc),
            holdout_start=datetime(2024, 7, 1, tzinfo=timezone.utc),
            holdout_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        assert id_a == id_b

    def test_two_new_partitions_from_identical_requests_have_the_same_id(self):
        partition_a = OOSPartition.new(_request())
        partition_b = OOSPartition.new(_request())
        assert partition_a.id == partition_b.id
        # created_at is real wall-clock time, not part of identity -- it
        # may legitimately differ between two independent constructions.

    def test_a_different_range_produces_a_different_id(self):
        partition_a = OOSPartition.new(_request())
        partition_b = OOSPartition.new(_request(holdout_end=datetime(2024, 11, 30, tzinfo=timezone.utc)))
        assert partition_a.id != partition_b.id

    def test_case_and_offset_insensitive(self):
        id_lower = compute_partition_id(
            provider="CSV",
            symbol="tsla",
            timeframe="5m",
            development_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            development_end=datetime(2024, 6, 30, tzinfo=timezone.utc),
            holdout_start=datetime(2024, 7, 1, tzinfo=timezone.utc),
            holdout_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        id_upper = compute_partition_id(
            provider="csv",
            symbol="TSLA",
            timeframe="5m",
            development_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            development_end=datetime(2024, 6, 30, tzinfo=timezone.utc),
            holdout_start=datetime(2024, 7, 1, tzinfo=timezone.utc),
            holdout_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        assert id_lower == id_upper
