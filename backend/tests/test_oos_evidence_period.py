"""Tests for app/oos_evidence/period.py::validate_new_period() -- pure
partition/period registration rules, no I/O, no HTTP (see
tests/test_oos_evidence_api.py for the end-to-end route tests).

Covers requirement 7's "Partition rules" list: valid sequential OOS
periods, overlapping periods rejected, touching periods rejected,
development overlap rejected, mismatched symbol/timeframe/provider
rejected, duplicate registration rejected, and cross-partition
contamination (an OOS period's holdout window silently reading through
another period's development window, or vice versa) rejected.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.experiment_freeze import ExperimentFreezeSnapshot
from app.models.oos_partition import OOSPartition, OOSPartitionCreateRequest
from app.models.research import ConditionOperator, FeatureCondition, FeatureConditionOperator, Outcome
from app.oos_evidence.period import OOSPeriodLinkageError, validate_new_period

SYMBOL, TIMEFRAME, PROVIDER = "TSLA", "5m", "csv"

# A single, fixed, safely-early development window every period below
# reuses by default -- 2023-12-01 through 2024-01-01, strictly before
# EVERY holdout window used anywhere in this file (all of which start
# 2024-01-02 or later). Sharing one development window across several
# periods is itself a valid, explicitly-tested scenario (see
# TestDevelopmentOverlapRejected::test_two_periods_reusing_the_same_development_window_is_allowed) --
# using it as every OTHER test's default too keeps those tests focused
# on the ONE rule each is actually exercising, rather than every test
# having to separately reason about where a freshly-derived development
# window might accidentally land relative to some other period's
# holdout window.
_SHARED_DEV_START = datetime(2023, 12, 1, tzinfo=timezone.utc)
_SHARED_DEV_END = datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)


def _partition(*, development_start, development_end, holdout_start, holdout_end, symbol=SYMBOL, timeframe=TIMEFRAME, provider=PROVIDER) -> OOSPartition:
    return OOSPartition.new(
        OOSPartitionCreateRequest(
            symbol=symbol, timeframe=timeframe, provider=provider,
            development_start=development_start, development_end=development_end,
            holdout_start=holdout_start, holdout_end=holdout_end,
        )
    )


def _period(*, holdout_start, holdout_end, development_start=_SHARED_DEV_START, development_end=_SHARED_DEV_END, **kwargs) -> OOSPartition:
    return _partition(development_start=development_start, development_end=development_end, holdout_start=holdout_start, holdout_end=holdout_end, **kwargs)


def _snapshot(*, oos_partition_id: str | None = None) -> ExperimentFreezeSnapshot:
    return ExperimentFreezeSnapshot(
        experiment_id="exp-1",
        hypothesis_hash="deadbeef",
        name="n", hypothesis="h",
        symbol=SYMBOL, timeframe=TIMEFRAME, provider=PROVIDER,
        start_date=datetime(2024, 1, 1).date(), end_date=datetime(2024, 1, 1).date(),
        feature_contract_version="v1",
        conditions=[FeatureCondition(feature_id="price.return_5m", operator=FeatureConditionOperator.GT, value=-999.0)],
        outcome=Outcome(metric="forward_return", horizon_minutes=5, operator=ConditionOperator.GT, threshold=-999.0),
        oos_partition_id=oos_partition_id,
        experiment_created_at=datetime.now(timezone.utc),
        frozen_at=datetime.now(timezone.utc),
    )


# The experiment's own frozen development range is 2024-01-01 (whole
# day); _ORIGINAL is the partition originally linked at freeze time,
# with a slightly wider development window ([_SHARED_DEV_START ..
# _SHARED_DEV_END]) than the experiment's own -- exactly the
# containment validate_snapshot_partition_linkage() would require at
# the real registration route (app/api/oos_evidence.py; not exercised
# directly by these pure-function tests, which call validate_new_period()
# on its own).
_ORIGINAL = _partition(
    development_start=_SHARED_DEV_START, development_end=_SHARED_DEV_END,
    holdout_start=datetime(2024, 1, 2, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 2, 4, 0, tzinfo=timezone.utc),
)
_SNAPSHOT = _snapshot(oos_partition_id=_ORIGINAL.id)


class TestValidSequentialPeriods:
    def test_a_period_strictly_after_the_original_holdout_is_accepted(self):
        new_period = _period(holdout_start=datetime(2024, 1, 3, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc))
        validate_new_period(snapshot=_SNAPSHOT, new_partition=new_period, already_registered_partitions=[_ORIGINAL])  # must not raise

    def test_several_sequential_periods_are_all_mutually_accepted(self):
        period_2 = _period(holdout_start=datetime(2024, 1, 3, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc))
        period_3 = _period(holdout_start=datetime(2024, 1, 4, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 4, 4, 0, tzinfo=timezone.utc))
        validate_new_period(snapshot=_SNAPSHOT, new_partition=period_2, already_registered_partitions=[_ORIGINAL])
        validate_new_period(snapshot=_SNAPSHOT, new_partition=period_3, already_registered_partitions=[_ORIGINAL, period_2])  # must not raise

    def test_first_ever_period_with_no_original_partition_is_accepted(self):
        """A FROZEN experiment that was never linked to an original
        partition at freeze time (snapshot.oos_partition_id is None) --
        OOS Evidence Accumulation V1 can still be its FIRST evaluation
        mechanism, requiring no prior partition to exist."""
        snapshot = _snapshot(oos_partition_id=None)
        new_period = _period(holdout_start=datetime(2024, 1, 3, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc))
        validate_new_period(snapshot=snapshot, new_partition=new_period, already_registered_partitions=[])  # must not raise


class TestOverlappingPeriodsRejected:
    def test_a_period_overlapping_the_original_holdout_is_rejected(self):
        overlapping = _period(holdout_start=datetime(2024, 1, 2, 2, 0, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 2, 6, 0, tzinfo=timezone.utc))
        with pytest.raises(OOSPeriodLinkageError, match="must not overlap"):
            validate_new_period(snapshot=_SNAPSHOT, new_partition=overlapping, already_registered_partitions=[_ORIGINAL])

    def test_a_period_overlapping_another_registered_period_is_rejected(self):
        period_2 = _period(holdout_start=datetime(2024, 1, 3, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc))
        overlapping = _period(holdout_start=datetime(2024, 1, 3, 2, 0, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 3, 6, 0, tzinfo=timezone.utc))
        with pytest.raises(OOSPeriodLinkageError, match="must not overlap"):
            validate_new_period(snapshot=_SNAPSHOT, new_partition=overlapping, already_registered_partitions=[_ORIGINAL, period_2])

    def test_a_period_fully_contained_within_another_is_rejected(self):
        period_2 = _period(holdout_start=datetime(2024, 1, 3, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 3, 8, 0, tzinfo=timezone.utc))
        nested = _period(holdout_start=datetime(2024, 1, 3, 1, 0, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 3, 2, 0, tzinfo=timezone.utc))
        with pytest.raises(OOSPeriodLinkageError, match="must not overlap"):
            validate_new_period(snapshot=_SNAPSHOT, new_partition=nested, already_registered_partitions=[_ORIGINAL, period_2])


class TestTouchingPeriodsRejected:
    def test_a_period_starting_exactly_where_another_ends_is_rejected(self):
        """Matches app/models/oos_partition.py's own
        `_validate_range_ordering()` philosophy: touching at a single
        shared instant is treated the same as overlapping (a bar
        timestamped exactly at that boundary would be ambiguous about
        which period it belongs to)."""
        touching = _period(holdout_start=_ORIGINAL.holdout_end, holdout_end=datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc))
        with pytest.raises(OOSPeriodLinkageError, match="must not overlap"):
            validate_new_period(snapshot=_SNAPSHOT, new_partition=touching, already_registered_partitions=[_ORIGINAL])

    def test_a_period_immediately_after_with_a_real_gap_is_accepted(self):
        """The negative-space counterpart to the above: a period that
        starts even one microsecond after another ends is NOT touching,
        and must be accepted."""
        adjacent = _period(
            holdout_start=_ORIGINAL.holdout_end + timedelta(microseconds=1),
            holdout_end=_ORIGINAL.holdout_end + timedelta(hours=4),
        )
        validate_new_period(snapshot=_SNAPSHOT, new_partition=adjacent, already_registered_partitions=[_ORIGINAL])  # must not raise


class TestDevelopmentOverlapRejected:
    def test_a_period_whose_holdout_overlaps_another_periods_development_is_rejected(self):
        """Cross-partition contamination, direction 1: a NEW period's
        holdout window must never overlap an ALREADY-REGISTERED
        period's development (warm-up) window -- that would mean this
        period's own "OOS" data was already read as development context
        for another evaluation."""
        period_2 = _period(
            development_start=datetime(2024, 1, 5, tzinfo=timezone.utc),
            development_end=datetime(2024, 1, 5, 23, 0, tzinfo=timezone.utc),
            holdout_start=datetime(2024, 1, 6, tzinfo=timezone.utc),
            holdout_end=datetime(2024, 1, 6, 4, 0, tzinfo=timezone.utc),
        )
        contaminating = _period(holdout_start=datetime(2024, 1, 5, 10, 0, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 5, 12, 0, tzinfo=timezone.utc))
        with pytest.raises(OOSPeriodLinkageError, match="contamination"):
            validate_new_period(snapshot=_SNAPSHOT, new_partition=contaminating, already_registered_partitions=[_ORIGINAL, period_2])

    def test_a_period_whose_development_overlaps_another_periods_holdout_is_rejected(self):
        """Cross-partition contamination, direction 2: a NEW period's
        own development (warm-up) window must never overlap an
        ALREADY-REGISTERED period's holdout window -- that would mean
        this period's warm-up silently reads through reserved holdout
        data."""
        contaminating = _period(
            development_start=datetime(2024, 1, 2, tzinfo=timezone.utc),  # overlaps _ORIGINAL's own holdout window
            development_end=datetime(2024, 1, 2, 12, 0, tzinfo=timezone.utc),
            holdout_start=datetime(2024, 1, 3, tzinfo=timezone.utc),
            holdout_end=datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc),
        )
        with pytest.raises(OOSPeriodLinkageError, match="contamination"):
            validate_new_period(snapshot=_SNAPSHOT, new_partition=contaminating, already_registered_partitions=[_ORIGINAL])

    def test_two_periods_reusing_the_same_development_window_is_allowed(self):
        """The negative-space counterpart: two DIFFERENT periods
        legitimately sharing the SAME underlying development-side
        warm-up context (the shared default this whole file uses) is
        normal and expected, not a leak -- only development-vs-HOLDOUT
        overlap is ever flagged."""
        period_2 = _period(holdout_start=datetime(2024, 1, 3, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc))
        period_3 = _period(holdout_start=datetime(2024, 1, 4, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 4, 4, 0, tzinfo=timezone.utc))
        assert period_2.development_start == period_3.development_start == _ORIGINAL.development_start  # same shared dev window
        validate_new_period(snapshot=_SNAPSHOT, new_partition=period_3, already_registered_partitions=[_ORIGINAL, period_2])  # must not raise


class TestMismatchedSymbolTimeframeProviderRejected:
    def test_mismatched_symbol_is_rejected(self):
        wrong_symbol = _period(holdout_start=datetime(2024, 1, 3, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc), symbol="NVDA")
        with pytest.raises(OOSPeriodLinkageError, match="not compatible"):
            validate_new_period(snapshot=_SNAPSHOT, new_partition=wrong_symbol, already_registered_partitions=[_ORIGINAL])

    def test_mismatched_timeframe_is_rejected(self):
        wrong_timeframe = _period(holdout_start=datetime(2024, 1, 3, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc), timeframe="1h")
        with pytest.raises(OOSPeriodLinkageError, match="not compatible"):
            validate_new_period(snapshot=_SNAPSHOT, new_partition=wrong_timeframe, already_registered_partitions=[_ORIGINAL])

    def test_mismatched_provider_is_rejected(self):
        wrong_provider = _period(holdout_start=datetime(2024, 1, 3, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc), provider="alpaca")
        with pytest.raises(OOSPeriodLinkageError, match="not compatible"):
            validate_new_period(snapshot=_SNAPSHOT, new_partition=wrong_provider, already_registered_partitions=[_ORIGINAL])


class TestDuplicateRegistrationRejected:
    def test_registering_the_same_partition_twice_is_rejected(self):
        with pytest.raises(OOSPeriodLinkageError, match="already registered"):
            validate_new_period(snapshot=_SNAPSHOT, new_partition=_ORIGINAL, already_registered_partitions=[_ORIGINAL])

    def test_registering_an_already_registered_additional_period_again_is_rejected(self):
        period_2 = _period(holdout_start=datetime(2024, 1, 3, tzinfo=timezone.utc), holdout_end=datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc))
        with pytest.raises(OOSPeriodLinkageError, match="already registered"):
            validate_new_period(snapshot=_SNAPSHOT, new_partition=period_2, already_registered_partitions=[_ORIGINAL, period_2])
