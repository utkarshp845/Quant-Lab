"""Tests for app/statistical_validation/v2/power.py -- the closed-form
minimum-detectable-effect-size formula, in isolation.
"""

import pytest

from app.statistical_validation.v2.power import minimum_detectable_effect_size


class TestMinimumDetectableEffectSize:
    def test_matches_the_hand_computed_formula_for_equal_group_sizes(self):
        # d_min = (1.959964 + 0.841621) * sqrt(1/65 + 1/65)
        expected = (1.959964 + 0.841621) * (2 / 65) ** 0.5
        assert minimum_detectable_effect_size(65, 65) == pytest.approx(expected)

    def test_a_larger_baseline_sample_lowers_the_detectable_effect_size(self):
        """Holding the conditioned sample size fixed, a much larger
        baseline sample should let the study detect a SMALLER true
        effect (more statistical power from the larger comparison
        group)."""
        small_baseline = minimum_detectable_effect_size(65, 100)
        large_baseline = minimum_detectable_effect_size(65, 10_000)
        assert large_baseline < small_baseline

    def test_a_higher_target_power_requires_a_larger_detectable_effect(self):
        at_80_power = minimum_detectable_effect_size(65, 900, power=0.80)
        at_90_power = minimum_detectable_effect_size(65, 900, power=0.90)
        assert at_90_power > at_80_power

    def test_a_stricter_alpha_requires_a_larger_detectable_effect(self):
        at_05 = minimum_detectable_effect_size(65, 900, alpha=0.05)
        at_01 = minimum_detectable_effect_size(65, 900, alpha=0.01)
        assert at_01 > at_05

    def test_unsupported_alpha_raises_a_clear_error(self):
        with pytest.raises(ValueError, match="Unsupported alpha"):
            minimum_detectable_effect_size(65, 900, alpha=0.10)

    def test_unsupported_power_raises_a_clear_error(self):
        with pytest.raises(ValueError, match="Unsupported power"):
            minimum_detectable_effect_size(65, 900, power=0.95)

    def test_non_positive_sample_sizes_raise(self):
        with pytest.raises(ValueError, match="positive"):
            minimum_detectable_effect_size(0, 900)
        with pytest.raises(ValueError, match="positive"):
            minimum_detectable_effect_size(65, -1)
