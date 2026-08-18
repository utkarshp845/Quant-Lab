"""Tests for app/research/conditions.py -- operator evaluation for both
Outcome (evaluate(), unchanged by v0.1.24) and the new, feature-based
evaluate_feature_conditions() (v0.1.24). No database, no HTTP --
FeatureRecord instances are built by hand here (never through the real
Feature Engine, unlike tests/test_research_engine.py) since this file
tests the evaluator in isolation, not the engine's own integration with
it.
"""

import pytest

from app.models.features import (
    FeatureRecord,
    MarketContextFeatures,
    PriceFeatures,
    PricePositionFeatures,
    VolatilityFeatures,
    VolumeFeatures,
)
from app.models.research import FeatureCondition, FeatureConditionOperator
from app.research.conditions import evaluate, evaluate_feature_conditions


def _record(
    *,
    return_5m=None,
    vwap_distance=None,
    relative_volume=None,
    volatility_percentile=None,
    with_market_context=False,
) -> FeatureRecord:
    return FeatureRecord(
        symbol="TSLA",
        timestamp="2026-08-17T14:30:00Z",
        timeframe="5m",
        provider="csv",
        calculated_at="2026-08-17T14:30:05Z",
        price=PriceFeatures(return_5m=return_5m, return_15m=None, return_30m=None, return_60m=None),
        volume=VolumeFeatures(volume=1_000, relative_volume=relative_volume, volume_acceleration=None),
        volatility=VolatilityFeatures(
            realized_volatility=None, atr=None, volatility_ratio=None, volatility_percentile=volatility_percentile
        ),
        market_context=(
            MarketContextFeatures(
                spy_return_5m=None, spy_return_15m=None, spy_return_30m=None, spy_return_60m=None,
                qqq_return_5m=None, qqq_return_15m=None, qqq_return_30m=None, qqq_return_60m=None,
                relative_strength_spy_5m=None, relative_strength_spy_15m=None,
                relative_strength_spy_30m=None, relative_strength_spy_60m=None,
                relative_strength_qqq_5m=None, relative_strength_qqq_15m=None,
                relative_strength_qqq_30m=None, relative_strength_qqq_60m=None,
            )
            if with_market_context
            else None
        ),
        price_position=PricePositionFeatures(
            vwap_distance=vwap_distance, ma20_distance=None, ma50_distance=None, intraday_range_position=None
        ),
    )


class TestEvaluate:
    """Outcome's operator evaluation -- unchanged by v0.1.24."""

    @pytest.mark.parametrize(
        "value, operator, threshold, expected",
        [
            (-0.02, "<", -0.01, True),
            (-0.005, "<", -0.01, False),
            (-0.01, "<=", -0.01, True),
            (0.5, "==", 0.5, True),
            (0.5, "==", 0.51, False),
            (0.02, ">=", 0.01, True),
            (0.02, ">", 0.01, True),
            (0.01, ">", 0.01, False),
        ],
    )
    def test_every_outcome_operator(self, value, operator, threshold, expected):
        assert evaluate(value, operator, threshold) is expected

    def test_unsupported_operator_raises(self):
        with pytest.raises(ValueError, match="Unsupported operator"):
            evaluate(1.0, "!=", 0.5)

    def test_single_equals_is_also_accepted_as_an_alias_for_double_equals(self):
        """FeatureConditionOperator spells equality "=" (single);
        ConditionOperator spells it "==" (double) -- both must reach the
        identical comparison through this one shared function."""
        assert evaluate(0.5, "=", 0.5) is True
        assert evaluate(0.5, "==", 0.5) is True


class TestEvaluateFeatureConditions:
    def test_a_single_condition_that_is_true_returns_its_value(self):
        record = _record(return_5m=-0.02)
        conditions = [FeatureCondition(feature_id="price.return_5m", operator=FeatureConditionOperator.LTE, value=-0.01)]

        result = evaluate_feature_conditions(conditions, record)

        assert result == {"price.return_5m": pytest.approx(-0.02)}

    def test_a_single_condition_that_is_false_returns_none(self):
        record = _record(return_5m=-0.005)
        conditions = [FeatureCondition(feature_id="price.return_5m", operator=FeatureConditionOperator.LTE, value=-0.01)]

        assert evaluate_feature_conditions(conditions, record) is None

    def test_a_referenced_feature_that_is_none_never_satisfies_any_condition(self):
        """A None feature value (insufficient history, a missing bar,
        ...) can never make a condition true -- not even a
        superficially-always-true one like ">= -999"."""
        record = _record(return_5m=None)
        conditions = [FeatureCondition(feature_id="price.return_5m", operator=FeatureConditionOperator.GTE, value=-999.0)]

        assert evaluate_feature_conditions(conditions, record) is None

    def test_market_context_symbol_not_configured_is_none_not_a_crash(self):
        """record.market_context is None entirely (this symbol isn't
        market-context-eligible) -- referencing a market_context
        feature_id must return None (via get_feature_value), same as
        any other undefined value, never an AttributeError."""
        record = _record(with_market_context=False)
        conditions = [
            FeatureCondition(feature_id="market_context.relative_strength_spy_5m", operator=FeatureConditionOperator.GT, value=0.0)
        ]

        assert evaluate_feature_conditions(conditions, record) is None

    def test_all_conditions_true_is_a_genuine_and_not_the_first_condition_alone(self):
        record = _record(return_5m=-0.02, relative_volume=1.8)
        conditions = [
            FeatureCondition(feature_id="price.return_5m", operator=FeatureConditionOperator.LTE, value=-0.01),
            FeatureCondition(feature_id="volume.relative_volume", operator=FeatureConditionOperator.GT, value=1.5),
        ]

        result = evaluate_feature_conditions(conditions, record)

        assert result == {"price.return_5m": pytest.approx(-0.02), "volume.relative_volume": pytest.approx(1.8)}

    def test_one_false_condition_among_several_true_ones_returns_none_not_a_partial_dict(self):
        record = _record(return_5m=-0.02, relative_volume=1.2)  # relative_volume fails the > 1.5 condition below
        conditions = [
            FeatureCondition(feature_id="price.return_5m", operator=FeatureConditionOperator.LTE, value=-0.01),
            FeatureCondition(feature_id="volume.relative_volume", operator=FeatureConditionOperator.GT, value=1.5),
        ]

        assert evaluate_feature_conditions(conditions, record) is None

    def test_between_is_inclusive_on_both_ends(self):
        conditions = [
            FeatureCondition(feature_id="volatility.volatility_percentile", operator=FeatureConditionOperator.BETWEEN, value=0.5, value_max=0.7)
        ]

        assert evaluate_feature_conditions(conditions, _record(volatility_percentile=0.5)) is not None
        assert evaluate_feature_conditions(conditions, _record(volatility_percentile=0.7)) is not None
        assert evaluate_feature_conditions(conditions, _record(volatility_percentile=0.6)) is not None

    def test_between_excludes_values_outside_the_bounds(self):
        conditions = [
            FeatureCondition(feature_id="volatility.volatility_percentile", operator=FeatureConditionOperator.BETWEEN, value=0.5, value_max=0.7)
        ]

        assert evaluate_feature_conditions(conditions, _record(volatility_percentile=0.49)) is None
        assert evaluate_feature_conditions(conditions, _record(volatility_percentile=0.71)) is None

    def test_the_dod_style_three_condition_and_example(self):
        """The Definition-of-Done shape from this integration's spec
        ("Price vs VWAP > 0 AND RSI 14 between 50 and 70 AND Volume
        Ratio > 1.5"), expressed with this codebase's REAL feature
        vocabulary -- there is no RSI feature here (see
        app/features/vocabulary.py's own module docstring), so
        volatility percentile (a real 0..1-fraction feature) stands in
        for the "between" example, and relative_volume stands in for
        "Volume Ratio"."""
        record = _record(vwap_distance=0.004, volatility_percentile=0.6, relative_volume=1.8)
        conditions = [
            FeatureCondition(feature_id="price_position.vwap_distance", operator=FeatureConditionOperator.GT, value=0),
            FeatureCondition(feature_id="volatility.volatility_percentile", operator=FeatureConditionOperator.BETWEEN, value=0.5, value_max=0.7),
            FeatureCondition(feature_id="volume.relative_volume", operator=FeatureConditionOperator.GT, value=1.5),
        ]

        result = evaluate_feature_conditions(conditions, record)

        assert result == {
            "price_position.vwap_distance": pytest.approx(0.004),
            "volatility.volatility_percentile": pytest.approx(0.6),
            "volume.relative_volume": pytest.approx(1.8),
        }
