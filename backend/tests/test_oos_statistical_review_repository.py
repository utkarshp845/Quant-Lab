"""Tests for app/storage/oos_statistical_review_repository.py -- the
ONLY module that writes SQL for `oos_statistical_reviews` (against a
real tmp_path SQLite database).

Covers: save/get/list round-trip, append-only (no update/replace
function exists), per-experiment isolation, newest-first ordering.
"""

from datetime import datetime, timezone

from app.models.oos_statistical_review import OOSEpisodeSampleSizes, OOSStatisticalReview, OOSStatisticalVerdict
from app.storage import oos_statistical_review_repository


def _review(review_id: str, experiment_id: str, *, verdict=OOSStatisticalVerdict.INSUFFICIENT_DATA) -> OOSStatisticalReview:
    return OOSStatisticalReview(
        id=review_id, experiment_id=experiment_id, frozen_snapshot_id=experiment_id, hypothesis_hash="deadbeef",
        review_config_version="oos-statistical-review-v1", created_at=datetime.now(timezone.utc),
        included_evaluation_ids=[], excluded_evaluations=[], oos_periods=[],
        outcome_metric="forward_return", outcome_operator=">", outcome_threshold=-999.0, outcome_horizon_minutes=15,
        primary_window_bars=3, symbol="TSLA", timeframe="5m", provider="csv", feature_contract_version="v1",
        seed=1337, n_resamples=10_000, ci_level=0.95, block_length_multiplier=4, power_target=0.80,
        min_episodes_for_formal_test=10,
        sample_sizes=OOSEpisodeSampleSizes(evaluation_count=0, raw_signal_count=0, episode_count=0, baseline_raw_observations=0, method_a_effective_baseline_n=0),
        method_a_mean_difference=None, method_a_win_rate_difference=None, method_a_test=None,
        method_b_mean_difference=None, method_b_win_rate_difference=None, method_b_test=None,
        effect_size=None, power_analysis=None, robustness=None,
        exploratory_horizons_note="note", per_period_results=[],
        verdict=verdict, verdict_reasoning="reasoning",
    )


class TestSaveAndGet:
    def test_round_trips_every_field(self, tmp_path):
        db_path = tmp_path / "reviews.db"
        review = _review("review-1", "exp-1", verdict=OOSStatisticalVerdict.SUPPORTED)
        oos_statistical_review_repository.save_review(review, db_path=db_path)

        fetched = oos_statistical_review_repository.get_review("review-1", db_path=db_path)
        assert fetched == review

    def test_get_missing_review_returns_none(self, tmp_path):
        db_path = tmp_path / "reviews.db"
        assert oos_statistical_review_repository.get_review("does-not-exist", db_path=db_path) is None


class TestAppendOnly:
    def test_running_the_review_twice_produces_two_rows(self, tmp_path):
        db_path = tmp_path / "reviews.db"
        oos_statistical_review_repository.save_review(_review("review-1", "exp-1"), db_path=db_path)
        oos_statistical_review_repository.save_review(_review("review-2", "exp-1"), db_path=db_path)

        reviews = oos_statistical_review_repository.list_reviews("exp-1", db_path=db_path)
        assert len(reviews) == 2
        assert {r.id for r in reviews} == {"review-1", "review-2"}

    def test_the_module_exposes_no_update_or_replace_function(self):
        """Structural proof: no function DEFINED IN this module (as
        opposed to merely imported, e.g. get_connection) can mutate an
        already-saved review."""
        functions_defined_here = {
            name
            for name in dir(oos_statistical_review_repository)
            if not name.startswith("_")
            and callable(getattr(oos_statistical_review_repository, name))
            and getattr(getattr(oos_statistical_review_repository, name), "__module__", None) == oos_statistical_review_repository.__name__
        }
        assert functions_defined_here == {"save_review", "get_review", "list_reviews"}


class TestListReviews:
    def test_lists_newest_first(self, tmp_path):
        db_path = tmp_path / "reviews.db"
        older = _review("review-1", "exp-1").model_copy(update={"created_at": datetime(2024, 1, 1, tzinfo=timezone.utc)})
        newer = _review("review-2", "exp-1").model_copy(update={"created_at": datetime(2024, 1, 2, tzinfo=timezone.utc)})
        oos_statistical_review_repository.save_review(older, db_path=db_path)
        oos_statistical_review_repository.save_review(newer, db_path=db_path)

        reviews = oos_statistical_review_repository.list_reviews("exp-1", db_path=db_path)
        assert [r.id for r in reviews] == ["review-2", "review-1"]

    def test_reviews_are_isolated_per_experiment(self, tmp_path):
        db_path = tmp_path / "reviews.db"
        oos_statistical_review_repository.save_review(_review("review-1", "exp-1"), db_path=db_path)
        oos_statistical_review_repository.save_review(_review("review-2", "exp-2"), db_path=db_path)

        assert [r.id for r in oos_statistical_review_repository.list_reviews("exp-1", db_path=db_path)] == ["review-1"]
        assert [r.id for r in oos_statistical_review_repository.list_reviews("exp-2", db_path=db_path)] == ["review-2"]

    def test_no_reviews_returns_an_empty_list(self, tmp_path):
        db_path = tmp_path / "reviews.db"
        assert oos_statistical_review_repository.list_reviews("exp-1", db_path=db_path) == []
