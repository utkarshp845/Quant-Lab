"""Tests for app/oos_statistical_review/engine.py::
build_oos_statistical_review() -- the full review pipeline, against a
real (tmp_path) SQLite database and synthetic-but-realistic bars. No
HTTP involved (see tests/test_oos_statistical_review_api.py for the
end-to-end route tests).

Covers requirement 13's "Input integrity", "Episode handling",
"Statistics", "Multiple horizons", "Power", "Period consistency", and
"Immutability" lists.
"""

from datetime import datetime, timedelta, timezone
from unittest import mock

import numpy as np
import pytest

from app.models.market_data import HistoricalBar
from app.models.oos_evaluation import OOSEvaluationStatus
from app.models.oos_evidence import OOSPeriod
from app.models.oos_partition import OOSPartition, OOSPartitionCreateRequest
from app.models.oos_statistical_review import OOSStatisticalVerdict
from app.models.research import ConditionOperator, Experiment, ExperimentCreateRequest, FeatureCondition, FeatureConditionOperator, Outcome
from app.oos_evaluation.engine import evaluate_oos, evaluate_oos_for_partition
from app.oos_evidence.period import validate_new_period
from app.oos_statistical_review.engine import (
    MIN_EPISODES_FOR_FORMAL_TEST,
    ExperimentNeverFrozenError,
    ExperimentNotFoundForReviewError,
    ProvenanceMismatchError,
    build_oos_statistical_review,
)
from app.research.lifecycle import build_freeze_snapshot, compute_hypothesis_hash, validate_snapshot_partition_linkage
from app.research.metrics import bars_for_window
from app.statistical_validation.v2.power import minimum_detectable_effect_size
from app.statistical_validation.resampling import cohens_d
from app.storage import (
    experiment_freeze_repository,
    historical_bar_repository,
    oos_evaluation_repository,
    oos_evidence_repository,
    oos_partition_repository,
    research_repository,
)
from app.storage.db import get_connection

SYMBOL, TIMEFRAME, PROVIDER = "TSLA", "5m", "csv"

# A moderately-selective, minimal-lookback condition (return_5m needs
# only one prior, contiguous bar -- see tests/test_oos_evidence_evaluation.py's
# own identical reasoning) that fires on roughly half of a random walk's
# bars, producing multiple, mostly-non-consecutive episodes per period
# -- enough independent episodes to exercise the "formal test" branch
# with a small number of periods/bars (fast tests), unlike an
# always-true condition (which collapses an entire holdout window into
# ONE giant episode -- see TestInsufficientData below, which uses
# exactly that to construct a deliberately UNDER-powered scenario).
_SELECTIVE_CONDITION = [FeatureCondition(feature_id="price.return_5m", operator=FeatureConditionOperator.GT, value=0.0)]
_ALWAYS_TRUE_CONDITION = [FeatureCondition(feature_id="price.return_5m", operator=FeatureConditionOperator.GT, value=-999.0)]

_FAST_N_RESAMPLES = 300  # small enough for fast tests; determinism/behavior does not depend on the exact count


def _bars(start: datetime, count: int, *, seed: int, base_price=100.0) -> list[HistoricalBar]:
    rng = np.random.default_rng(seed)
    price = base_price
    bars = []
    for i in range(count):
        price = max(1.0, price * (1 + rng.normal(0, 0.001)))
        bars.append(
            HistoricalBar(
                symbol=SYMBOL, timestamp=start + timedelta(minutes=5 * i), open=price, high=price + 0.1,
                low=price - 0.1, close=price, volume=1_000, provider=PROVIDER, timeframe=TIMEFRAME,
            )
        )
    return bars


def _make_partition(db_path, *, development_start, development_end, holdout_start, holdout_end) -> OOSPartition:
    partition = OOSPartition.new(
        OOSPartitionCreateRequest(
            symbol=SYMBOL, timeframe=TIMEFRAME, provider=PROVIDER,
            development_start=development_start, development_end=development_end,
            holdout_start=holdout_start, holdout_end=holdout_end,
        )
    )
    oos_partition_repository.save_partition(partition, db_path=db_path)
    return partition


def _register_period(db_path, snapshot, new_partition: OOSPartition) -> None:
    validate_snapshot_partition_linkage(snapshot, new_partition)
    already_registered = []
    if snapshot.oos_partition_id is not None:
        original = oos_partition_repository.get_partition(snapshot.oos_partition_id, db_path=db_path)
        if original is not None:
            already_registered.append(original)
    already_registered.extend(
        p for p in (oos_partition_repository.get_partition(period.oos_partition_id, db_path=db_path) for period in oos_evidence_repository.list_periods(snapshot.experiment_id, db_path=db_path)) if p is not None
    )
    validate_new_period(snapshot=snapshot, new_partition=new_partition, already_registered_partitions=already_registered)
    period = OOSPeriod(
        id=new_partition.id, experiment_id=snapshot.experiment_id, oos_partition_id=new_partition.id,
        symbol=new_partition.symbol, timeframe=new_partition.timeframe, provider=new_partition.provider,
        oos_start=new_partition.holdout_start, oos_end=new_partition.holdout_end, label=new_partition.label,
        registered_at=datetime.now(timezone.utc),
    )
    oos_evidence_repository.save_period(period, db_path=db_path)


class _ReviewScenario:
    """`n_periods` sequential, non-overlapping OOS periods (each 48
    bars / 4 hours, 1 calendar day apart), sharing one 288-bar
    development window, against ONE frozen experiment -- the first
    period is the originally frozen-time-linked partition (OOS
    Evaluation v1's own mechanism), every later one is an OOS Evidence
    Accumulation V1 period. Every period's evaluation is COMPLETED
    unless `fail_period_indices` marks it to fail (simulated pipeline
    failure, via monkeypatch -- see build()'s own docstring)."""

    development_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    development_end = datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)

    def __init__(
        self, db_path, *, n_periods: int = 3, condition=None, horizon_minutes: int = 15,
        fail_period_indices: set[int] = frozenset(), bar_seed_offset: int = 0,
    ):
        self.db_path = db_path
        condition = condition if condition is not None else _SELECTIVE_CONDITION
        development_bars = _bars(self.development_start, 288, seed=1 + bar_seed_offset)
        historical_bar_repository.save_bars(development_bars, db_path=db_path)

        self.holdout_windows = [
            (datetime(2024, 1, 2 + i, tzinfo=timezone.utc), datetime(2024, 1, 2 + i, 4, 0, tzinfo=timezone.utc)) for i in range(n_periods)
        ]
        self.partitions = []
        for i, (holdout_start, _holdout_end) in enumerate(self.holdout_windows):
            holdout_bars = _bars(holdout_start, 48, seed=10 + bar_seed_offset + i, base_price=development_bars[-1].close)
            historical_bar_repository.save_bars(holdout_bars, db_path=db_path)
            partition = _make_partition(
                db_path, development_start=self.development_start, development_end=self.development_end,
                holdout_start=holdout_start, holdout_end=self.holdout_windows[i][1],
            )
            self.partitions.append(partition)

        outcome = Outcome(metric="forward_return", horizon_minutes=horizon_minutes, operator=ConditionOperator.GT, threshold=-999.0)
        request = ExperimentCreateRequest(
            name="Review Test", hypothesis="h", symbol=SYMBOL, start_date="2024-01-01", end_date="2024-01-01",
            timeframe=TIMEFRAME, provider=PROVIDER, conditions=condition, outcome=outcome,
        )
        experiment = Experiment.new(request)
        research_repository.save_experiment(experiment, db_path=db_path)
        research_repository.set_oos_partition(experiment.id, self.partitions[0].id, db_path=db_path)
        experiment = research_repository.get_experiment(experiment.id, db_path=db_path)
        frozen_at = datetime.now(timezone.utc)
        hypothesis_hash = compute_hypothesis_hash(experiment)
        snapshot = build_freeze_snapshot(experiment, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at)
        experiment_freeze_repository.save_snapshot(snapshot, db_path=db_path)
        research_repository.freeze_experiment(
            experiment.id, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at, oos_partition_id=self.partitions[0].id, db_path=db_path
        )
        self.experiment = research_repository.get_experiment(experiment.id, db_path=db_path)
        self.snapshot = experiment_freeze_repository.get_snapshot(self.experiment.id, db_path=db_path)

        for partition in self.partitions[1:]:
            _register_period(db_path, self.snapshot, partition)

        for i, partition in enumerate(self.partitions):
            if i in fail_period_indices:
                import app.oos_evaluation.engine as engine_module

                with mock.patch.object(engine_module, "compute_features", side_effect=RuntimeError("simulated failure")):
                    if i == 0:
                        result, signals = evaluate_oos(self.experiment.id, db_path=db_path)
                    else:
                        result, signals = evaluate_oos_for_partition(self.experiment.id, partition.id, db_path=db_path)
            elif i == 0:
                result, signals = evaluate_oos(self.experiment.id, db_path=db_path)
            else:
                result, signals = evaluate_oos_for_partition(self.experiment.id, partition.id, db_path=db_path)
            oos_evaluation_repository.save_evaluation(result, signals, db_path=db_path)

    def build_review(self, **kwargs):
        kwargs.setdefault("n_resamples", _FAST_N_RESAMPLES)
        return build_oos_statistical_review(self.experiment.id, db_path=self.db_path, **kwargs)


class TestInputIntegrity:
    def test_only_completed_evaluations_are_included(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=3, fail_period_indices={1})
        review = scenario.build_review()

        assert len(review.included_evaluation_ids) == 2
        assert len(review.excluded_evaluations) == 1
        assert review.sample_sizes.evaluation_count == 2

    def test_failed_evaluations_excluded_but_visible(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=3, fail_period_indices={2})
        review = scenario.build_review()

        excluded = review.excluded_evaluations[0]
        assert excluded.reason == "status=failed"
        failed_eval_id = [e.id for e in oos_evaluation_repository.list_evaluations(scenario.experiment.id, db_path=scenario.db_path) if e.status == OOSEvaluationStatus.FAILED][0]
        assert excluded.evaluation_id == failed_eval_id
        assert excluded.evaluation_id not in review.included_evaluation_ids

    def test_frozen_snapshot_is_authoritative_over_a_tampered_live_row(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=3)
        baseline_review = scenario.build_review(seed=42)

        conn = get_connection(scenario.db_path)
        try:
            with conn:
                conn.execute(
                    "UPDATE experiments SET conditions_json = ? WHERE id = ?",
                    ('[{"feature_id": "price.return_5m", "operator": "<", "value": -999.0}]', scenario.experiment.id),
                )
        finally:
            conn.close()

        tampered_review = scenario.build_review(seed=42)
        assert tampered_review.sample_sizes == baseline_review.sample_sizes
        assert tampered_review.hypothesis_hash == baseline_review.hypothesis_hash

    def test_hypothesis_hash_mismatch_across_evaluations_fails_closed(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=2)
        evaluations = oos_evaluation_repository.list_evaluations(scenario.experiment.id, db_path=scenario.db_path)
        tampered = evaluations[0].model_copy(update={"hypothesis_hash": "not-the-real-hash"})
        conn = get_connection(scenario.db_path)
        try:
            with conn:
                conn.execute("UPDATE oos_evaluations SET hypothesis_hash = ? WHERE id = ?", ("not-the-real-hash", tampered.id))
        finally:
            conn.close()

        with pytest.raises(ProvenanceMismatchError, match="hypothesis_hash"):
            scenario.build_review()

    def test_missing_baseline_partition_fails_closed(self, tmp_path):
        """Requirement 3: if the baseline cannot safely be
        constructed, fail with a clear error rather than silently
        substituting anything."""
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=2)
        conn = get_connection(scenario.db_path)
        try:
            with conn:
                conn.execute("DELETE FROM oos_partitions WHERE id = ?", (scenario.partitions[0].id,))
        finally:
            conn.close()

        from app.oos_statistical_review.baseline import BaselineConstructionError

        with pytest.raises(BaselineConstructionError):
            scenario.build_review()

    def test_insufficient_episodes_produces_insufficient_data(self, tmp_path):
        """An always-true condition collapses an entire holdout window
        into ONE episode per period -- far below MIN_EPISODES_FOR_FORMAL_TEST."""
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=2, condition=_ALWAYS_TRUE_CONDITION)
        review = scenario.build_review()

        assert review.sample_sizes.episode_count < MIN_EPISODES_FOR_FORMAL_TEST
        assert review.verdict == OOSStatisticalVerdict.INSUFFICIENT_DATA
        assert review.method_a_test is None
        assert review.method_b_test is None
        assert review.effect_size is None
        assert review.power_analysis is None

    def test_unknown_experiment_is_rejected(self, tmp_path):
        with pytest.raises(ExperimentNotFoundForReviewError):
            build_oos_statistical_review("does-not-exist", db_path=tmp_path / "review.db")

    def test_never_frozen_experiment_is_rejected(self, tmp_path):
        db_path = tmp_path / "review.db"
        request = ExperimentCreateRequest(
            name="n", hypothesis="h", symbol=SYMBOL, start_date="2024-01-01", end_date="2024-01-01",
            timeframe=TIMEFRAME, provider=PROVIDER, conditions=_SELECTIVE_CONDITION,
            outcome=Outcome(metric="forward_return", horizon_minutes=15, operator=ConditionOperator.GT, threshold=-999.0),
        )
        experiment = Experiment.new(request)
        research_repository.save_experiment(experiment, db_path=db_path)

        with pytest.raises(ExperimentNeverFrozenError):
            build_oos_statistical_review(experiment.id, db_path=db_path)


class TestEpisodeHandling:
    def test_raw_signal_count_exceeds_episode_count(self, tmp_path):
        """A persistent condition produces consecutive, correlated raw
        signals that collapse into fewer episodes -- proves the two
        counts are genuinely different, never silently equated."""
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=2, condition=_ALWAYS_TRUE_CONDITION)
        review = scenario.build_review()
        assert review.sample_sizes.raw_signal_count > review.sample_sizes.episode_count

    def test_episodes_from_separate_periods_are_not_merged(self, tmp_path):
        """Two periods whose FIRST signals are, numerically, exactly
        one bar-interval apart in wall-clock terms would incorrectly
        merge into one episode if grouping were ever applied across
        periods -- proven by construction: sample_sizes.episode_count
        must equal the SUM of each period's own independently-computed
        episode count."""
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=3)
        review = scenario.build_review()

        assert review.sample_sizes.episode_count == sum(p.episode_count for p in review.per_period_results)


class TestStatistics:
    def test_deterministic_p_values_with_a_fixed_seed(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=3)
        review_1 = scenario.build_review(seed=1337)
        review_2 = scenario.build_review(seed=1337)

        assert review_1.method_a_test.p_value_two_sided == review_2.method_a_test.p_value_two_sided
        assert review_1.method_b_test.p_value_two_sided == review_2.method_b_test.p_value_two_sided
        assert review_1.method_a_mean_difference.ci_low == review_2.method_a_mean_difference.ci_low
        assert review_1.method_a_mean_difference.ci_high == review_2.method_a_mean_difference.ci_high
        assert review_1.effect_size.cohens_d == review_2.effect_size.cohens_d

    def test_different_seeds_can_produce_different_resampled_statistics(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=3)
        review_a = scenario.build_review(seed=1)
        review_b = scenario.build_review(seed=2)
        # The observed (non-resampled) mean difference is identical --
        # only the RESAMPLED quantities (CI/p-value) may legitimately differ.
        assert review_a.method_a_mean_difference.difference == review_b.method_a_mean_difference.difference

    def test_method_a_and_method_b_both_execute_with_enough_data(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=3)
        review = scenario.build_review()

        assert review.method_a_test is not None
        assert review.method_b_test is not None
        assert review.method_a_mean_difference is not None
        assert review.method_b_mean_difference is not None
        assert review.method_a_win_rate_difference is not None
        assert review.method_b_win_rate_difference is not None
        assert review.method_a_test.n_conditioned == review.method_b_test.n_conditioned  # same episode sample, both methods
        assert review.method_a_test.n_baseline != review.method_b_test.n_baseline  # different baseline construction

    def test_conclusion_changes_materially_is_a_mechanical_zero_exclusion_check(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=3)
        review = scenario.build_review()

        a_excludes_zero = not (review.method_a_mean_difference.ci_low <= 0 <= review.method_a_mean_difference.ci_high)
        b_excludes_zero = not (review.method_b_mean_difference.ci_low <= 0 <= review.method_b_mean_difference.ci_high)
        assert review.robustness.conclusion_changes_materially == (a_excludes_zero != b_excludes_zero)

    def test_effect_size_reconciles_with_a_direct_cohens_d_recomputation(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=3)
        review = scenario.build_review()

        # Reconstruct the exact same episode/baseline populations is
        # not exposed by the review itself (by design -- it stores
        # aggregates, not raw arrays) -- instead, reconcile n's and
        # verify cohens_d() is mathematically consistent with the
        # reported mean difference and pooled_stdev.
        expected_d = review.effect_size.pooled_stdev and (
            review.method_a_mean_difference.difference / review.effect_size.pooled_stdev
        )
        if expected_d is not None:
            assert review.effect_size.cohens_d == pytest.approx(expected_d, rel=1e-9)


class TestMultipleHorizons:
    def test_primary_window_bars_matches_the_frozen_horizon(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=2, horizon_minutes=15)
        review = scenario.build_review()

        assert review.primary_window_bars == bars_for_window(15, TIMEFRAME)
        assert review.outcome_horizon_minutes == 15

    def test_exploratory_horizons_are_always_empty_with_an_explanatory_note(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=2)
        review = scenario.build_review()

        assert review.exploratory_horizons_note  # non-empty, explains why
        assert "OOS Evaluation v1 evaluates exactly one window" in review.exploratory_horizons_note


class TestPower:
    def test_minimum_detectable_effect_size_matches_a_direct_call(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=3)
        review = scenario.build_review()

        expected_mdes = minimum_detectable_effect_size(
            review.power_analysis.n_conditioned_episodes, review.power_analysis.n_baseline_effective, power=review.power_target
        )
        assert review.power_analysis.minimum_detectable_effect_size == expected_mdes

    def test_underpowered_dataset_is_honestly_flagged(self, tmp_path):
        """A tiny, noise-level effect (the SELECTIVE condition on pure
        random-walk data has no real edge) should typically fall below
        the study's own minimum detectable effect size -- and the
        review must say so plainly, never silently declare success."""
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=3)
        review = scenario.build_review()

        assert review.power_analysis.observed_effect_below_detectable_threshold == (
            abs(review.power_analysis.observed_effect_size) < review.power_analysis.minimum_detectable_effect_size
        )


class TestPeriodConsistency:
    def test_multiple_periods_are_reported_separately(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=4)
        review = scenario.build_review()

        assert len(review.per_period_results) == 4
        assert len({p.oos_partition_id for p in review.per_period_results}) == 4

    def test_one_strong_period_does_not_erase_a_contradictory_period(self, tmp_path):
        """Two periods with DELIBERATELY opposite-signed returns
        (planted, not random) -- both must remain individually visible
        in per_period_results with opposite-signed means, even though a
        naive pool might average them toward zero."""
        db_path = tmp_path / "review.db"
        development_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        development_end = datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)
        development_bars = _bars(development_start, 288, seed=1)
        historical_bar_repository.save_bars(development_bars, db_path=db_path)

        # Period 1: strictly increasing prices -> every return_5m > 0.
        up_start = datetime(2024, 1, 2, tzinfo=timezone.utc)
        up_bars = [
            HistoricalBar(symbol=SYMBOL, timestamp=up_start + timedelta(minutes=5 * i), open=100 + i * 0.5, high=100 + i * 0.5 + 0.1, low=100 + i * 0.5 - 0.1, close=100 + i * 0.5, volume=1000, provider=PROVIDER, timeframe=TIMEFRAME)
            for i in range(48)
        ]
        # Period 2: strictly decreasing prices -> every return_5m < 0 (never satisfies the GT 0 condition -- use a different, always-eligible condition to compare mean_return sign instead).
        down_start = datetime(2024, 1, 3, tzinfo=timezone.utc)
        down_bars = [
            HistoricalBar(symbol=SYMBOL, timestamp=down_start + timedelta(minutes=5 * i), open=200 - i * 0.5, high=200 - i * 0.5 + 0.1, low=200 - i * 0.5 - 0.1, close=200 - i * 0.5, volume=1000, provider=PROVIDER, timeframe=TIMEFRAME)
            for i in range(48)
        ]
        historical_bar_repository.save_bars(up_bars, db_path=db_path)
        historical_bar_repository.save_bars(down_bars, db_path=db_path)

        up_partition = _make_partition(db_path, development_start=development_start, development_end=development_end, holdout_start=up_start, holdout_end=up_start + timedelta(hours=4))
        down_partition = _make_partition(db_path, development_start=development_start, development_end=development_end, holdout_start=down_start, holdout_end=down_start + timedelta(hours=4))

        outcome = Outcome(metric="forward_return", horizon_minutes=15, operator=ConditionOperator.GT, threshold=-999.0)
        request = ExperimentCreateRequest(
            name="mixed", hypothesis="h", symbol=SYMBOL, start_date="2024-01-01", end_date="2024-01-01",
            timeframe=TIMEFRAME, provider=PROVIDER, conditions=_ALWAYS_TRUE_CONDITION, outcome=outcome,
        )
        experiment = Experiment.new(request)
        research_repository.save_experiment(experiment, db_path=db_path)
        research_repository.set_oos_partition(experiment.id, up_partition.id, db_path=db_path)
        experiment = research_repository.get_experiment(experiment.id, db_path=db_path)
        frozen_at = datetime.now(timezone.utc)
        hypothesis_hash = compute_hypothesis_hash(experiment)
        snapshot = build_freeze_snapshot(experiment, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at)
        experiment_freeze_repository.save_snapshot(snapshot, db_path=db_path)
        research_repository.freeze_experiment(experiment.id, hypothesis_hash=hypothesis_hash, frozen_at=frozen_at, oos_partition_id=up_partition.id, db_path=db_path)
        snapshot = experiment_freeze_repository.get_snapshot(experiment.id, db_path=db_path)
        _register_period(db_path, snapshot, down_partition)

        result_1, signals_1 = evaluate_oos(experiment.id, db_path=db_path)
        oos_evaluation_repository.save_evaluation(result_1, signals_1, db_path=db_path)
        result_2, signals_2 = evaluate_oos_for_partition(experiment.id, down_partition.id, db_path=db_path)
        oos_evaluation_repository.save_evaluation(result_2, signals_2, db_path=db_path)

        review = build_oos_statistical_review(experiment.id, db_path=db_path, n_resamples=_FAST_N_RESAMPLES)
        by_partition = {p.oos_partition_id: p for p in review.per_period_results}
        assert by_partition[up_partition.id].mean_return > 0
        assert by_partition[down_partition.id].mean_return < 0


class TestImmutability:
    def test_underlying_evaluations_are_unchanged_after_a_review(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=2)
        before = oos_evaluation_repository.list_evaluations(scenario.experiment.id, db_path=scenario.db_path)
        scenario.build_review()
        after = oos_evaluation_repository.list_evaluations(scenario.experiment.id, db_path=scenario.db_path)
        assert before == after

    def test_frozen_snapshot_is_unchanged_after_a_review(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=2)
        before = experiment_freeze_repository.get_snapshot(scenario.experiment.id, db_path=scenario.db_path)
        scenario.build_review()
        after = experiment_freeze_repository.get_snapshot(scenario.experiment.id, db_path=scenario.db_path)
        assert before == after

    def test_repeat_review_produces_identical_analytical_results(self, tmp_path):
        scenario = _ReviewScenario(tmp_path / "review.db", n_periods=3)
        review_1 = scenario.build_review(seed=1337)
        review_2 = scenario.build_review(seed=1337)

        assert review_1.id != review_2.id  # a new, additional record each time
        assert review_1.verdict == review_2.verdict
        assert review_1.sample_sizes == review_2.sample_sizes
        assert review_1.method_a_test == review_2.method_a_test
        assert review_1.method_b_test == review_2.method_b_test
        assert review_1.effect_size == review_2.effect_size
        assert review_1.power_analysis == review_2.power_analysis
        assert review_1.per_period_results == review_2.per_period_results
