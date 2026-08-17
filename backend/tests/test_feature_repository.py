"""Tests for app/storage/feature_repository.py -- the only module that
writes SQL for `historical_features`. Every test uses an explicit
db_path pointing at a pytest tmp_path file, never the real
config.get_database_path() default -- same isolation convention as
tests/test_historical_bar_repository.py.
"""

from datetime import date, datetime, timedelta, timezone

from app.models.features import (
    FeatureRecord,
    MarketContextFeatures,
    PriceFeatures,
    PricePositionFeatures,
    VolatilityFeatures,
    VolumeFeatures,
)
from app.storage.feature_repository import get_features, save_features


def _record(
    symbol="TSLA",
    timestamp="2026-01-05T14:30:00Z",
    timeframe="5m",
    provider="csv",
    calculated_at="2026-08-17T12:00:00Z",
    with_market_context=True,
) -> FeatureRecord:
    return FeatureRecord(
        symbol=symbol,
        timestamp=timestamp,
        timeframe=timeframe,
        provider=provider,
        calculated_at=calculated_at,
        price=PriceFeatures(return_5m=0.01, return_15m=0.02, return_30m=None, return_60m=None),
        volume=VolumeFeatures(volume=1000, relative_volume=1.5, volume_acceleration=None),
        volatility=VolatilityFeatures(realized_volatility=0.2, atr=1.1, volatility_ratio=None, volatility_percentile=None),
        market_context=(
            MarketContextFeatures(
                spy_return_5m=0.005,
                spy_return_15m=None,
                spy_return_30m=None,
                spy_return_60m=None,
                qqq_return_5m=-0.002,
                qqq_return_15m=None,
                qqq_return_30m=None,
                qqq_return_60m=None,
                relative_strength_spy_5m=0.005,
                relative_strength_spy_15m=None,
                relative_strength_spy_30m=None,
                relative_strength_spy_60m=None,
                relative_strength_qqq_5m=0.012,
                relative_strength_qqq_15m=None,
                relative_strength_qqq_30m=None,
                relative_strength_qqq_60m=None,
            )
            if with_market_context
            else None
        ),
        price_position=PricePositionFeatures(
            vwap_distance=0.001, ma20_distance=None, ma50_distance=None, intraday_range_position=0.7
        ),
    )


class TestSaveAndGetFeatures:
    def test_a_saved_record_round_trips_exactly(self, tmp_path):
        db_path = tmp_path / "features.db"
        record = _record()

        saved = save_features([record], db_path=db_path)
        loaded = get_features(symbol="TSLA", timeframe="5m", provider="csv", start=date(2026, 1, 1), end=date(2026, 1, 31), db_path=db_path)

        assert saved == 1
        assert loaded == [record]

    def test_a_record_with_no_market_context_round_trips_as_none(self, tmp_path):
        db_path = tmp_path / "features.db"
        record = _record(symbol="MCL", with_market_context=False)

        save_features([record], db_path=db_path)
        loaded = get_features(symbol="MCL", timeframe="5m", provider="csv", start=date(2026, 1, 1), end=date(2026, 1, 31), db_path=db_path)

        assert loaded == [record]
        assert loaded[0].market_context is None

    def test_empty_list_is_a_no_op_not_an_error(self, tmp_path):
        db_path = tmp_path / "features.db"
        assert save_features([], db_path=db_path) == 0

    def test_returns_empty_list_not_an_error_when_nothing_stored(self, tmp_path):
        db_path = tmp_path / "features.db"
        loaded = get_features(symbol="TSLA", timeframe="5m", provider="csv", start=date(2026, 1, 1), end=date(2026, 1, 31), db_path=db_path)
        assert loaded == []

    def test_recomputing_replaces_the_existing_row_rather_than_duplicating(self, tmp_path):
        """A recompute (bug fix, formula change) must overwrite the
        stored row for that symbol/timeframe/provider/timestamp, not
        create a second one -- see the schema comment in
        app/storage/db.py."""
        db_path = tmp_path / "features.db"
        first = _record()
        save_features([first], db_path=db_path)

        second = _record()
        second = second.model_copy(update={"price": PriceFeatures(return_5m=0.99, return_15m=None, return_30m=None, return_60m=None)})
        save_features([second], db_path=db_path)

        loaded = get_features(symbol="TSLA", timeframe="5m", provider="csv", start=date(2026, 1, 1), end=date(2026, 1, 31), db_path=db_path)

        assert len(loaded) == 1  # not 2
        assert loaded[0].price.return_5m == 0.99

    def test_multiple_symbols_do_not_bleed_into_each_others_results(self, tmp_path):
        db_path = tmp_path / "features.db"
        save_features([_record(symbol="TSLA"), _record(symbol="NVDA")], db_path=db_path)

        tsla = get_features(symbol="TSLA", timeframe="5m", provider="csv", start=date(2026, 1, 1), end=date(2026, 1, 31), db_path=db_path)
        nvda = get_features(symbol="NVDA", timeframe="5m", provider="csv", start=date(2026, 1, 1), end=date(2026, 1, 31), db_path=db_path)

        assert len(tsla) == 1 and tsla[0].symbol == "TSLA"
        assert len(nvda) == 1 and nvda[0].symbol == "NVDA"

    def test_multiple_timeframes_do_not_bleed_into_each_others_results(self, tmp_path):
        db_path = tmp_path / "features.db"
        save_features([_record(timeframe="5m"), _record(timeframe="1h")], db_path=db_path)

        five_min = get_features(symbol="TSLA", timeframe="5m", provider="csv", start=date(2026, 1, 1), end=date(2026, 1, 31), db_path=db_path)
        one_hour = get_features(symbol="TSLA", timeframe="1h", provider="csv", start=date(2026, 1, 1), end=date(2026, 1, 31), db_path=db_path)

        assert len(five_min) == 1 and five_min[0].timeframe == "5m"
        assert len(one_hour) == 1 and one_hour[0].timeframe == "1h"

    def test_multiple_providers_do_not_bleed_into_each_others_results(self, tmp_path):
        db_path = tmp_path / "features.db"
        save_features([_record(provider="alpaca"), _record(provider="massive")], db_path=db_path)

        alpaca = get_features(symbol="TSLA", timeframe="5m", provider="alpaca", start=date(2026, 1, 1), end=date(2026, 1, 31), db_path=db_path)
        massive = get_features(symbol="TSLA", timeframe="5m", provider="massive", start=date(2026, 1, 1), end=date(2026, 1, 31), db_path=db_path)

        assert len(alpaca) == 1 and len(massive) == 1

    def test_date_range_retrieval_excludes_records_outside_the_window(self, tmp_path):
        db_path = tmp_path / "features.db"
        save_features(
            [
                _record(timestamp="2026-01-01T14:30:00Z"),
                _record(timestamp="2026-01-10T14:30:00Z"),
                _record(timestamp="2026-01-20T14:30:00Z"),
            ],
            db_path=db_path,
        )

        loaded = get_features(symbol="TSLA", timeframe="5m", provider="csv", start=date(2026, 1, 5), end=date(2026, 1, 15), db_path=db_path)

        assert [r.timestamp.day for r in loaded] == [10]

    def test_results_are_ordered_oldest_first(self, tmp_path):
        db_path = tmp_path / "features.db"
        save_features(
            [
                _record(timestamp="2026-01-15T14:30:00Z"),
                _record(timestamp="2026-01-05T14:30:00Z"),
                _record(timestamp="2026-01-10T14:30:00Z"),
            ],
            db_path=db_path,
        )

        loaded = get_features(symbol="TSLA", timeframe="5m", provider="csv", start=date(2026, 1, 1), end=date(2026, 1, 31), db_path=db_path)

        assert [r.timestamp.day for r in loaded] == [5, 10, 15]

    def test_symbol_lookup_is_case_insensitive(self, tmp_path):
        db_path = tmp_path / "features.db"
        save_features([_record(symbol="TSLA")], db_path=db_path)

        loaded = get_features(symbol="tsla", timeframe="5m", provider="csv", start=date(2026, 1, 1), end=date(2026, 1, 31), db_path=db_path)

        assert len(loaded) == 1
