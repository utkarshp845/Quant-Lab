"""Round-trip tests for app/storage/research_notebook_repository.py --
same isolated-throwaway-database convention as every other repository
test in this app."""

from datetime import datetime, timezone

import pytest

from app.models.research_notebook import (
    ConclusionCreateRequest,
    ConclusionState,
    Conclusion,
    Observation,
    ObservationCreateRequest,
    ResearchDecision,
    ResearchDecisionCreateRequest,
)
from app.storage import research_notebook_repository as repo


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test_research_notebook.db")


def test_observation_round_trip(db_path):
    request = ObservationCreateRequest(
        symbol="tsla",
        description="Sharp premarket gap down.",
        observed_start=datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc),
        observed_end=datetime(2026, 2, 1, 9, 30, tzinfo=timezone.utc),
        referenced_bar_timestamps=[datetime(2026, 2, 1, 9, 5, tzinfo=timezone.utc)],
        referenced_feature_ids=["price.return_15m", "volume.relative_volume"],
    )
    observation = Observation.new(request)
    repo.save_observation(observation, db_path=db_path)

    fetched = repo.get_observation(observation.id, db_path=db_path)
    assert fetched == observation


def test_get_observation_missing_returns_none(db_path):
    assert repo.get_observation("nonexistent", db_path=db_path) is None


def test_list_observations_filters_by_symbol_and_orders_newest_first(db_path):
    tsla = Observation.new(
        ObservationCreateRequest(
            symbol="TSLA", description="a",
            observed_start=datetime(2026, 1, 1, tzinfo=timezone.utc), observed_end=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        )
    )
    nvda = Observation.new(
        ObservationCreateRequest(
            symbol="NVDA", description="b",
            observed_start=datetime(2026, 1, 1, tzinfo=timezone.utc), observed_end=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        )
    )
    repo.save_observation(tsla, db_path=db_path)
    repo.save_observation(nvda, db_path=db_path)

    assert [o.id for o in repo.list_observations(symbol="tsla", db_path=db_path)] == [tsla.id]
    assert {o.id for o in repo.list_observations(db_path=db_path)} == {tsla.id, nvda.id}


def test_decision_round_trip_and_design_group_ordering(db_path):
    first = ResearchDecision.new(
        ResearchDecisionCreateRequest(
            design_group_id="dg-1", decision="Proposed candidates A/B/C",
            reason="Enumerated conceptually valid definitions.", outcome_data_available=False,
        )
    )
    repo.save_decision(first, db_path=db_path)
    second = ResearchDecision.new(
        ResearchDecisionCreateRequest(
            design_group_id="dg-1", decision="Selected Candidate C",
            reason="Largest viable sample.", selection_criteria=["sample_size"],
            outcome_data_available=False, resulting_experiment_id="exp-1",
        )
    )
    repo.save_decision(second, db_path=db_path)

    history = repo.list_decisions("dg-1", db_path=db_path)
    assert [d.id for d in history] == [first.id, second.id]  # oldest first -- a readable history
    assert history[1].resulting_experiment_id == "exp-1"


def test_decisions_scoped_to_own_design_group(db_path):
    repo.save_decision(
        ResearchDecision.new(
            ResearchDecisionCreateRequest(design_group_id="dg-a", decision="x", reason="y", outcome_data_available=True)
        ),
        db_path=db_path,
    )
    assert repo.list_decisions("dg-b", db_path=db_path) == []


def test_conclusion_round_trip_and_newest_first(db_path):
    def _conclusion(experiment_id: str, state: ConclusionState) -> Conclusion:
        return Conclusion.new(
            experiment_id,
            ConclusionCreateRequest(
                state=state, statement="s", references_hypothesis="h", references_sample="n=63",
                references_baseline="b", references_outcomes="o", references_statistical_validation="p=0.25",
                limitations="single symbol",
            ),
        )

    first = _conclusion("exp-1", ConclusionState.NEEDS_MORE_DATA)
    repo.save_conclusion(first, db_path=db_path)
    second = _conclusion("exp-1", ConclusionState.INCONCLUSIVE)
    repo.save_conclusion(second, db_path=db_path)

    history = repo.list_conclusions("exp-1", db_path=db_path)
    assert [c.id for c in history] == [second.id, first.id]  # newest first -- history[0] is "current"


def test_conclusions_scoped_to_own_experiment(db_path):
    repo.save_conclusion(
        Conclusion.new(
            "exp-a",
            ConclusionCreateRequest(
                state=ConclusionState.SUPPORTED, statement="s", references_hypothesis="h",
                references_sample="n", references_baseline="b", references_outcomes="o",
                references_statistical_validation="p", limitations="l",
            ),
        ),
        db_path=db_path,
    )
    assert repo.list_conclusions("exp-b", db_path=db_path) == []
