"""Tests for app/statistical_validation/episodes.py -- the documented
non-overlapping episode-grouping rule, in isolation from the engine
and from any real backtest data.
"""

from datetime import datetime, timedelta, timezone

from app.models.backtesting import BacktestSignal
from app.statistical_validation.episodes import episode_representatives, group_into_episodes

_FIVE_MIN = timedelta(minutes=5)


def _signal(minutes_offset: int) -> BacktestSignal:
    base = datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc)
    ts = base + timedelta(minutes=minutes_offset)
    return BacktestSignal(
        backtest_id="bt-1", experiment_id="exp-1", symbol="TSLA", timeframe="5m",
        signal_timestamp=ts, entry_timestamp=ts + _FIVE_MIN, entry_price=100.0,
        feature_values={"price.return_15m": -0.01}, outcomes=[],
    )


class TestEmptyAndSingle:
    def test_empty_input_yields_no_episodes(self):
        assert group_into_episodes([], bar_interval=_FIVE_MIN) == []

    def test_a_single_signal_is_its_own_episode(self):
        signals = [_signal(0)]
        episodes = group_into_episodes(signals, bar_interval=_FIVE_MIN)
        assert len(episodes) == 1
        assert episodes[0] == signals


class TestConsecutiveBarsFormOneEpisode:
    def test_three_consecutive_bars_form_a_single_three_signal_episode(self):
        signals = [_signal(0), _signal(5), _signal(10)]
        episodes = group_into_episodes(signals, bar_interval=_FIVE_MIN)
        assert len(episodes) == 1
        assert len(episodes[0]) == 3

    def test_episode_first_signal_is_the_earliest_one(self):
        signals = [_signal(10), _signal(0), _signal(5)]  # deliberately out of order
        episodes = group_into_episodes(signals, bar_interval=_FIVE_MIN)
        assert len(episodes) == 1
        assert episodes[0][0].signal_timestamp == signals[1].signal_timestamp  # offset 0, the earliest


class TestGapsStartNewEpisodes:
    def test_a_gap_of_more_than_one_bar_interval_starts_a_new_episode(self):
        signals = [_signal(0), _signal(20)]  # 20 minutes apart, not 5 -- not consecutive
        episodes = group_into_episodes(signals, bar_interval=_FIVE_MIN)
        assert len(episodes) == 2
        assert [len(e) for e in episodes] == [1, 1]

    def test_mixed_runs_and_gaps_produce_the_expected_episode_boundaries(self):
        # Two consecutive (episode 1), a gap, three consecutive (episode 2), a gap, one alone (episode 3).
        offsets = [0, 5, 30, 35, 40, 100]
        signals = [_signal(o) for o in offsets]
        episodes = group_into_episodes(signals, bar_interval=_FIVE_MIN)
        assert [len(e) for e in episodes] == [2, 3, 1]

    def test_a_gap_shorter_than_one_bar_interval_still_starts_a_new_episode(self):
        """The rule requires EXACTLY one bar-interval, not "close to
        it" -- a 3-minute gap (not a real bar spacing for a 5-minute
        timeframe, but a defensive case) must not be treated as
        consecutive."""
        base = datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc)
        first = _signal(0)
        second = BacktestSignal(
            backtest_id="bt-1", experiment_id="exp-1", symbol="TSLA", timeframe="5m",
            signal_timestamp=base + timedelta(minutes=3), entry_timestamp=base + timedelta(minutes=8),
            entry_price=100.0, feature_values={}, outcomes=[],
        )
        episodes = group_into_episodes([first, second], bar_interval=_FIVE_MIN)
        assert len(episodes) == 2


class TestEpisodeRepresentatives:
    def test_returns_exactly_one_signal_per_episode(self):
        offsets = [0, 5, 30, 35, 40, 100]
        signals = [_signal(o) for o in offsets]
        episodes = group_into_episodes(signals, bar_interval=_FIVE_MIN)

        representatives = episode_representatives(episodes)

        assert len(representatives) == 3  # matches the 3 episodes from the mixed-runs test above
        assert [r.signal_timestamp for r in representatives] == [
            signals[0].signal_timestamp, signals[2].signal_timestamp, signals[5].signal_timestamp,
        ]

    def test_representatives_of_an_empty_episode_list_is_empty(self):
        assert episode_representatives([]) == []
