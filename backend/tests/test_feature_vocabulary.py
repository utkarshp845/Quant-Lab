"""Tests for app/features/vocabulary.py (v0.1.24) -- the canonical
feature vocabulary Research's condition builder reads instead of a
hardcoded feature list. No database, no HTTP.
"""

import pytest

from app.features.vocabulary import (
    BOOLEAN_OPERATORS,
    FEATURE_VOCABULARY,
    NUMERIC_OPERATORS,
    FeatureCategory,
    FeatureValueType,
    get_feature_definition,
    get_feature_value,
    get_feature_vocabulary,
)
from app.models.features import (
    FEATURE_CONTRACT_VERSION,
    FeatureRecord,
    MarketContextFeatures,
    PriceFeatures,
    PricePositionFeatures,
    VolatilityFeatures,
    VolumeFeatures,
)


def _record(with_market_context=True) -> FeatureRecord:
    return FeatureRecord(
        symbol="TSLA",
        timestamp="2026-08-17T14:30:00Z",
        timeframe="5m",
        provider="csv",
        calculated_at="2026-08-17T14:30:05Z",
        price=PriceFeatures(return_5m=0.011, return_15m=0.02, return_30m=None, return_60m=None),
        volume=VolumeFeatures(volume=12_345, relative_volume=1.8, volume_acceleration=None),
        volatility=VolatilityFeatures(realized_volatility=0.35, atr=1.2, volatility_ratio=None, volatility_percentile=0.6),
        market_context=(
            MarketContextFeatures(
                spy_return_5m=0.002, spy_return_15m=None, spy_return_30m=None, spy_return_60m=None,
                qqq_return_5m=None, qqq_return_15m=None, qqq_return_30m=None, qqq_return_60m=None,
                relative_strength_spy_5m=0.009, relative_strength_spy_15m=None,
                relative_strength_spy_30m=None, relative_strength_spy_60m=None,
                relative_strength_qqq_5m=None, relative_strength_qqq_15m=None,
                relative_strength_qqq_30m=None, relative_strength_qqq_60m=None,
            )
            if with_market_context
            else None
        ),
        price_position=PricePositionFeatures(vwap_distance=0.004, ma20_distance=None, ma50_distance=None, intraday_range_position=0.7),
    )


class TestVocabularyLoading:
    def test_every_definition_has_a_unique_feature_id(self):
        ids = [f.feature_id for f in FEATURE_VOCABULARY]
        assert len(ids) == len(set(ids))

    def test_the_full_leaf_field_count_is_31_not_25(self):
        """See app/features/vocabulary.py's own module docstring: this
        vocabulary exposes every real leaf FeatureRecord field (4 PRICE
        + 3 VOLUME + 4 VOLATILITY + 16 MARKET CONTEXT + 4 PRICE
        POSITION = 31), not an arbitrary 25-feature subset."""
        assert len(FEATURE_VOCABULARY) == 31

    def test_get_feature_vocabulary_returns_a_defensive_copy(self):
        vocabulary = get_feature_vocabulary()
        vocabulary.clear()
        assert len(get_feature_vocabulary()) == 31

    def test_every_definition_has_the_five_required_fields_populated(self):
        for definition in FEATURE_VOCABULARY:
            assert definition.feature_id
            assert definition.name
            assert definition.description
            assert definition.value_type in (FeatureValueType.NUMERIC, FeatureValueType.BOOLEAN)
            assert len(definition.supported_operators) > 0

    def test_every_definition_carries_the_current_feature_contract_version(self):
        for definition in FEATURE_VOCABULARY:
            assert definition.version == FEATURE_CONTRACT_VERSION

    def test_feature_id_is_the_category_dot_field_convention(self):
        """Matches frontend/src/types/features.ts::FEATURE_METRIC_LABELS'
        existing dotted-key convention -- see the module docstring for
        why this vocabulary reuses it rather than inventing a second
        naming scheme."""
        for definition in FEATURE_VOCABULARY:
            assert definition.feature_id == f"{definition.category.value}.{definition.feature_id.split('.', 1)[1]}"
            assert definition.feature_id.startswith(f"{definition.category.value}.")

    def test_only_market_context_features_are_flagged_market_context_only(self):
        for definition in FEATURE_VOCABULARY:
            expected = definition.category == FeatureCategory.MARKET_CONTEXT
            assert definition.market_context_only is expected

    def test_market_context_category_has_exactly_sixteen_entries(self):
        market_context = [f for f in FEATURE_VOCABULARY if f.category == FeatureCategory.MARKET_CONTEXT]
        assert len(market_context) == 16

    @pytest.mark.parametrize(
        "category, expected_count",
        [
            (FeatureCategory.PRICE, 4),
            (FeatureCategory.VOLUME, 3),
            (FeatureCategory.VOLATILITY, 4),
            (FeatureCategory.MARKET_CONTEXT, 16),
            (FeatureCategory.PRICE_POSITION, 4),
        ],
    )
    def test_each_category_has_the_expected_count(self, category, expected_count):
        assert len([f for f in FEATURE_VOCABULARY if f.category == category]) == expected_count


class TestOperatorsByType:
    def test_every_numeric_feature_offers_the_full_operator_set(self):
        for definition in FEATURE_VOCABULARY:
            if definition.value_type == FeatureValueType.NUMERIC:
                assert set(definition.supported_operators) == set(NUMERIC_OPERATORS)

    def test_numeric_operators_include_between(self):
        assert "between" in NUMERIC_OPERATORS

    def test_boolean_operators_are_equality_only(self):
        assert BOOLEAN_OPERATORS == ("=",)

    def test_every_current_feature_is_numeric(self):
        """No boolean feature exists in this app's real contract today
        -- see the module docstring for why BOOLEAN exists in the type
        system anyway."""
        assert all(f.value_type == FeatureValueType.NUMERIC for f in FEATURE_VOCABULARY)


class TestGetFeatureDefinition:
    def test_a_known_feature_id_returns_its_definition(self):
        definition = get_feature_definition("price_position.vwap_distance")
        assert definition.name == "Distance from VWAP"
        assert definition.category == FeatureCategory.PRICE_POSITION

    def test_an_unknown_feature_id_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown feature_id"):
            get_feature_definition("not_a_real_feature")


class TestGetFeatureValue:
    def test_reads_a_price_feature(self):
        assert get_feature_value(_record(), "price.return_5m") == pytest.approx(0.011)

    def test_reads_a_volume_feature_including_the_never_null_volume_itself(self):
        assert get_feature_value(_record(), "volume.volume") == 12_345
        assert get_feature_value(_record(), "volume.relative_volume") == pytest.approx(1.8)

    def test_reads_a_volatility_feature(self):
        assert get_feature_value(_record(), "volatility.volatility_percentile") == pytest.approx(0.6)

    def test_reads_a_price_position_feature(self):
        assert get_feature_value(_record(), "price_position.vwap_distance") == pytest.approx(0.004)

    def test_reads_a_market_context_feature_when_present(self):
        assert get_feature_value(_record(), "market_context.relative_strength_spy_5m") == pytest.approx(0.009)

    def test_a_none_leaf_value_reads_back_as_none(self):
        assert get_feature_value(_record(), "price.return_30m") is None

    def test_market_context_none_entirely_reads_every_market_context_feature_as_none(self):
        """record.market_context is None (symbol not configured for
        market context at all) -- distinct from a present-but-None leaf
        value, but both must read back as None here, never an
        AttributeError."""
        record = _record(with_market_context=False)
        assert get_feature_value(record, "market_context.relative_strength_spy_5m") is None
        assert get_feature_value(record, "market_context.spy_return_5m") is None

    def test_an_unknown_feature_id_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown feature_id"):
            get_feature_value(_record(), "not_a_real_feature")

    def test_every_vocabulary_entry_is_actually_readable_off_a_real_record(self):
        """No feature_id in the vocabulary silently fails to resolve --
        every one of the 31 entries must read SOMETHING (a value or an
        honest None) off a fully-populated FeatureRecord, never raise."""
        record = _record()
        for definition in FEATURE_VOCABULARY:
            get_feature_value(record, definition.feature_id)  # must not raise
