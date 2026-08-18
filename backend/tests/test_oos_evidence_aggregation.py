"""Tests for app/oos_evidence/aggregation.py::build_evidence_summary()
-- the pure read model, given already-constructed OOSEvaluationResult/
OOSSignal objects directly (no I/O, no database -- see
tests/test_oos_evidence_api.py for the end-to-end route test, which
exercises this through real evaluations and a real database).

Covers requirement 7's "Aggregation" list: multiple evaluations
aggregate correctly, failed evaluations excluded, raw signals and
episodes remain distinct, aggregate metrics reconcile with underlying
completed evaluations, and no statistical significance claims are
generated.
"""

from datetime import datetime, timedelta, timezone

from app.backtesting.aggregation import aggregate_results
from app.models.backtesting import BacktestWindowOutcome
from app.models.oos_evaluation import OOSEvaluationResult, OOSEvaluationStatus, OOSSignal
from app.models.oos_evidence import OOSEvidenceSummary
from app.oos_evidence.aggregation import build_evidence_summary

SYMBOL, TIMEFRAME, PROVIDER = "TSLA", "5m", "csv"
_HYPOTHESIS_HASH = "deadbeef"
_FROZEN_AT = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _signal(evaluation_id: str, *, timestamp: datetime, forward_return: float, mfe: float, mae: float, window_bars=1) -> OOSSignal:
    return OOSSignal(
        evaluation_id=evaluation_id, symbol=SYMBOL, timeframe=TIMEFRAME,
        signal_timestamp=timestamp, entry_timestamp=timestamp + timedelta(minutes=5), entry_price=100.0,
        feature_values={}, outcomes=[BacktestWindowOutcome(window_bars=window_bars, outcome_timestamp=timestamp + timedelta(minutes=10), forward_return=forward_return, mfe=mfe, mae=mae)],
    )


def _evaluation(
    evaluation_id: str, *, oos_partition_id: str, holdout_start: datetime, holdout_end: datetime,
    status: OOSEvaluationStatus, signals: list[OOSSignal], outcome_window_bars: int | None = 1,
) -> OOSEvaluationResult:
    results = aggregate_results(signals, windows=[outcome_window_bars]) if (status == OOSEvaluationStatus.COMPLETED and outcome_window_bars is not None) else None
    return OOSEvaluationResult(
        id=evaluation_id, experiment_id="exp-1", hypothesis_hash=_HYPOTHESIS_HASH, frozen_snapshot_id="exp-1",
        oos_partition_id=oos_partition_id, symbol=SYMBOL, timeframe=TIMEFRAME, provider=PROVIDER,
        holdout_start=holdout_start, holdout_end=holdout_end, feature_contract_version="v1",
        outcome_horizon_minutes=5, outcome_window_bars=outcome_window_bars, signal_count=len(signals),
        results=results, status=status, error_message=None if status == OOSEvaluationStatus.COMPLETED else "boom",
        frozen_at=_FROZEN_AT, evaluated_at=holdout_end + timedelta(minutes=1),
    )


class TestMultipleEvaluationsAggregateCorrectly:
    def test_two_completed_periods_combine_into_one_summary(self):
        period_1_start, period_1_end = datetime(2024, 1, 2, tzinfo=timezone.utc), datetime(2024, 1, 2, 4, 0, tzinfo=timezone.utc)
        period_2_start, period_2_end = datetime(2024, 1, 3, tzinfo=timezone.utc), datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc)
        signals_1 = [_signal("eval-1", timestamp=period_1_start + timedelta(minutes=5 * i), forward_return=0.01, mfe=0.02, mae=-0.01) for i in range(3)]
        signals_2 = [_signal("eval-2", timestamp=period_2_start + timedelta(minutes=5 * i), forward_return=-0.02, mfe=0.01, mae=-0.03) for i in range(4)]
        eval_1 = _evaluation("eval-1", oos_partition_id="p1", holdout_start=period_1_start, holdout_end=period_1_end, status=OOSEvaluationStatus.COMPLETED, signals=signals_1)
        eval_2 = _evaluation("eval-2", oos_partition_id="p2", holdout_start=period_2_start, holdout_end=period_2_end, status=OOSEvaluationStatus.COMPLETED, signals=signals_2)

        summary = build_evidence_summary(
            experiment_id="exp-1", hypothesis_hash=_HYPOTHESIS_HASH, evaluations=[eval_1, eval_2],
            signals_by_evaluation={"eval-1": signals_1, "eval-2": signals_2},
        )

        assert isinstance(summary, OOSEvidenceSummary)
        assert summary.experiment_id == "exp-1"
        assert summary.hypothesis_hash == _HYPOTHESIS_HASH
        assert summary.oos_period_count == 2
        assert summary.completed_evaluation_count == 2
        assert summary.failed_evaluation_count == 0
        assert summary.total_raw_signals == 7
        assert summary.earliest_oos_start == period_1_start
        assert summary.latest_oos_end == period_2_end
        assert len(summary.per_period_results) == 2
        assert {r.evaluation_id for r in summary.per_period_results} == {"eval-1", "eval-2"}

    def test_an_empty_evaluation_history_produces_an_empty_summary(self):
        summary = build_evidence_summary(experiment_id="exp-1", hypothesis_hash=_HYPOTHESIS_HASH, evaluations=[], signals_by_evaluation={})
        assert summary.oos_period_count == 0
        assert summary.completed_evaluation_count == 0
        assert summary.failed_evaluation_count == 0
        assert summary.total_raw_signals == 0
        assert summary.total_independent_episodes == 0
        assert summary.mean_return is None
        assert summary.earliest_oos_start is None
        assert summary.latest_oos_end is None
        assert summary.per_period_results == []


class TestFailedEvaluationsExcluded:
    def test_a_failed_evaluation_contributes_only_to_the_failed_count(self):
        period_1_start, period_1_end = datetime(2024, 1, 2, tzinfo=timezone.utc), datetime(2024, 1, 2, 4, 0, tzinfo=timezone.utc)
        period_2_start, period_2_end = datetime(2024, 1, 3, tzinfo=timezone.utc), datetime(2024, 1, 3, 4, 0, tzinfo=timezone.utc)
        signals_1 = [_signal("eval-1", timestamp=period_1_start, forward_return=0.01, mfe=0.02, mae=-0.01)]
        eval_1 = _evaluation("eval-1", oos_partition_id="p1", holdout_start=period_1_start, holdout_end=period_1_end, status=OOSEvaluationStatus.COMPLETED, signals=signals_1)
        eval_2_failed = _evaluation("eval-2", oos_partition_id="p2", holdout_start=period_2_start, holdout_end=period_2_end, status=OOSEvaluationStatus.FAILED, signals=[])

        summary = build_evidence_summary(
            experiment_id="exp-1", hypothesis_hash=_HYPOTHESIS_HASH, evaluations=[eval_1, eval_2_failed],
            signals_by_evaluation={"eval-1": signals_1},
        )

        assert summary.completed_evaluation_count == 1
        assert summary.failed_evaluation_count == 1
        assert summary.oos_period_count == 1  # only the COMPLETED period counts
        assert summary.total_raw_signals == 1  # the failed evaluation's own (zero) signals never counted
        assert summary.per_period_results == [r for r in summary.per_period_results if r.evaluation_id != "eval-2"]
        assert len(summary.per_period_results) == 1  # the failed evaluation never appears as a "period result"
        # A failed period's own would-be range never pollutes earliest/latest either.
        assert summary.earliest_oos_start == period_1_start
        assert summary.latest_oos_end == period_1_end


class TestRawSignalsAndEpisodesRemainDistinct:
    def test_consecutive_bar_signals_collapse_into_one_episode_but_all_count_as_raw(self):
        """Three CONSECUTIVE-bar signals (exactly one bar-interval
        apart, app/statistical_validation/episodes.py's own rule) are
        one episode, but all three still count toward
        total_raw_signals -- exactly the distinction this feature's own
        instructions require never be conflated."""
        start = datetime(2024, 1, 2, tzinfo=timezone.utc)
        signals = [_signal("eval-1", timestamp=start + timedelta(minutes=5 * i), forward_return=0.01, mfe=0.02, mae=-0.01) for i in range(3)]
        evaluation = _evaluation("eval-1", oos_partition_id="p1", holdout_start=start, holdout_end=start + timedelta(hours=1), status=OOSEvaluationStatus.COMPLETED, signals=signals)

        summary = build_evidence_summary(experiment_id="exp-1", hypothesis_hash=_HYPOTHESIS_HASH, evaluations=[evaluation], signals_by_evaluation={"eval-1": signals})

        assert summary.total_raw_signals == 3
        assert summary.total_independent_episodes == 1

    def test_two_separated_evaluations_never_merge_episodes_across_periods(self):
        """Even though the LAST signal of period 1 and the FIRST signal
        of period 2 could, numerically, be exactly one bar-interval
        apart in the wrong test construction, episode grouping is
        applied PER EVALUATION -- two different OOS periods' signals
        must never be treated as one episode."""
        period_1_start = datetime(2024, 1, 2, tzinfo=timezone.utc)
        period_2_start = datetime(2024, 1, 2, 0, 5, tzinfo=timezone.utc)  # exactly one bar-interval after period 1's own single signal
        signals_1 = [_signal("eval-1", timestamp=period_1_start, forward_return=0.01, mfe=0.02, mae=-0.01)]
        signals_2 = [_signal("eval-2", timestamp=period_2_start, forward_return=0.02, mfe=0.03, mae=-0.02)]
        eval_1 = _evaluation("eval-1", oos_partition_id="p1", holdout_start=period_1_start, holdout_end=period_1_start + timedelta(minutes=5), status=OOSEvaluationStatus.COMPLETED, signals=signals_1)
        eval_2 = _evaluation("eval-2", oos_partition_id="p2", holdout_start=period_2_start, holdout_end=period_2_start + timedelta(minutes=5), status=OOSEvaluationStatus.COMPLETED, signals=signals_2)

        summary = build_evidence_summary(
            experiment_id="exp-1", hypothesis_hash=_HYPOTHESIS_HASH, evaluations=[eval_1, eval_2],
            signals_by_evaluation={"eval-1": signals_1, "eval-2": signals_2},
        )

        assert summary.total_raw_signals == 2
        assert summary.total_independent_episodes == 2  # NOT 1 -- never merged across periods

    def test_episode_count_sums_across_multiple_periods(self):
        period_1_start = datetime(2024, 1, 2, tzinfo=timezone.utc)
        period_2_start = datetime(2024, 1, 3, tzinfo=timezone.utc)
        # period 1: two consecutive signals (1 episode) + one isolated one (1 episode) = 2 episodes, 3 raw signals
        signals_1 = [
            _signal("eval-1", timestamp=period_1_start, forward_return=0.01, mfe=0.02, mae=-0.01),
            _signal("eval-1", timestamp=period_1_start + timedelta(minutes=5), forward_return=0.01, mfe=0.02, mae=-0.01),
            _signal("eval-1", timestamp=period_1_start + timedelta(minutes=30), forward_return=0.01, mfe=0.02, mae=-0.01),
        ]
        # period 2: three isolated signals = 3 episodes, 3 raw signals
        signals_2 = [
            _signal("eval-2", timestamp=period_2_start, forward_return=0.02, mfe=0.03, mae=-0.02),
            _signal("eval-2", timestamp=period_2_start + timedelta(minutes=30), forward_return=0.02, mfe=0.03, mae=-0.02),
            _signal("eval-2", timestamp=period_2_start + timedelta(minutes=60), forward_return=0.02, mfe=0.03, mae=-0.02),
        ]
        eval_1 = _evaluation("eval-1", oos_partition_id="p1", holdout_start=period_1_start, holdout_end=period_1_start + timedelta(hours=1), status=OOSEvaluationStatus.COMPLETED, signals=signals_1)
        eval_2 = _evaluation("eval-2", oos_partition_id="p2", holdout_start=period_2_start, holdout_end=period_2_start + timedelta(hours=1), status=OOSEvaluationStatus.COMPLETED, signals=signals_2)

        summary = build_evidence_summary(
            experiment_id="exp-1", hypothesis_hash=_HYPOTHESIS_HASH, evaluations=[eval_1, eval_2],
            signals_by_evaluation={"eval-1": signals_1, "eval-2": signals_2},
        )

        assert summary.total_raw_signals == 6
        assert summary.total_independent_episodes == 5  # 2 (period 1) + 3 (period 2)


class TestAggregateMetricsReconcileWithUnderlyingEvaluations:
    def test_pooled_mean_and_win_rate_match_a_direct_recomputation_over_every_raw_signal(self):
        period_1_start = datetime(2024, 1, 2, tzinfo=timezone.utc)
        period_2_start = datetime(2024, 1, 3, tzinfo=timezone.utc)
        signals_1 = [_signal("eval-1", timestamp=period_1_start + timedelta(minutes=5 * i), forward_return=r, mfe=abs(r), mae=-abs(r)) for i, r in enumerate([0.01, -0.02, 0.03])]
        signals_2 = [_signal("eval-2", timestamp=period_2_start + timedelta(minutes=5 * i), forward_return=r, mfe=abs(r), mae=-abs(r)) for i, r in enumerate([-0.01, 0.04])]
        eval_1 = _evaluation("eval-1", oos_partition_id="p1", holdout_start=period_1_start, holdout_end=period_1_start + timedelta(hours=1), status=OOSEvaluationStatus.COMPLETED, signals=signals_1)
        eval_2 = _evaluation("eval-2", oos_partition_id="p2", holdout_start=period_2_start, holdout_end=period_2_start + timedelta(hours=1), status=OOSEvaluationStatus.COMPLETED, signals=signals_2)

        summary = build_evidence_summary(
            experiment_id="exp-1", hypothesis_hash=_HYPOTHESIS_HASH, evaluations=[eval_1, eval_2],
            signals_by_evaluation={"eval-1": signals_1, "eval-2": signals_2},
        )

        all_signals = signals_1 + signals_2
        expected = aggregate_results(all_signals, windows=[1]).windows[0]
        assert summary.mean_return == expected.mean_return
        assert summary.median_return == expected.median_return
        assert summary.win_rate == expected.win_rate
        assert summary.std_dev_return == expected.std_dev_return
        assert summary.mean_mfe == expected.mean_mfe
        assert summary.mean_mae == expected.mean_mae
        assert summary.total_raw_signals == expected.signal_count == 5

    def test_per_period_results_signal_counts_sum_to_total_raw_signals(self):
        period_1_start = datetime(2024, 1, 2, tzinfo=timezone.utc)
        period_2_start = datetime(2024, 1, 3, tzinfo=timezone.utc)
        signals_1 = [_signal("eval-1", timestamp=period_1_start, forward_return=0.01, mfe=0.01, mae=-0.01)]
        signals_2 = [_signal("eval-2", timestamp=period_2_start, forward_return=0.02, mfe=0.02, mae=-0.02) for _ in range(3)]
        eval_1 = _evaluation("eval-1", oos_partition_id="p1", holdout_start=period_1_start, holdout_end=period_1_start + timedelta(hours=1), status=OOSEvaluationStatus.COMPLETED, signals=signals_1)
        eval_2 = _evaluation("eval-2", oos_partition_id="p2", holdout_start=period_2_start, holdout_end=period_2_start + timedelta(hours=1), status=OOSEvaluationStatus.COMPLETED, signals=signals_2)

        summary = build_evidence_summary(
            experiment_id="exp-1", hypothesis_hash=_HYPOTHESIS_HASH, evaluations=[eval_1, eval_2],
            signals_by_evaluation={"eval-1": signals_1, "eval-2": signals_2},
        )

        assert sum(r.signal_count for r in summary.per_period_results) == summary.total_raw_signals


class TestNoStatisticalSignificanceClaimsGenerated:
    def test_the_summary_model_has_no_significance_fields(self):
        """Structural proof (requirement: "do not perform formal
        statistical significance testing in V1"): no p-value,
        confidence interval, standard error, or verdict field exists
        anywhere on OOSEvidenceSummary."""
        field_names = set(OOSEvidenceSummary.model_fields.keys())
        forbidden_substrings = ("p_value", "pvalue", "confidence", "significan", "ci_", "verdict", "effect_size")
        for field_name in field_names:
            lowered = field_name.lower()
            assert not any(forbidden in lowered for forbidden in forbidden_substrings), field_name

    def test_a_zero_signal_completed_evaluation_produces_no_fabricated_statistics(self):
        """A COMPLETED evaluation that legitimately found zero signals
        (a real, reportable outcome -- app/models/oos_evaluation.py's
        own convention) must never make the pooled statistics silently
        become 0.0 -- they stay None, the same "never fabricate" rule
        app/backtesting/aggregation.py already applies per-evaluation,
        reused here across periods too."""
        start = datetime(2024, 1, 2, tzinfo=timezone.utc)
        evaluation = _evaluation("eval-1", oos_partition_id="p1", holdout_start=start, holdout_end=start + timedelta(hours=1), status=OOSEvaluationStatus.COMPLETED, signals=[])

        summary = build_evidence_summary(experiment_id="exp-1", hypothesis_hash=_HYPOTHESIS_HASH, evaluations=[evaluation], signals_by_evaluation={"eval-1": []})

        assert summary.total_raw_signals == 0
        assert summary.mean_return is None
        assert summary.median_return is None
        assert summary.win_rate is None
        assert summary.std_dev_return is None
