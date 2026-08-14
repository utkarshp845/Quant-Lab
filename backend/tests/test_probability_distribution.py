"""Unit tests for the Phase 2 probability engine.

Covers: bucket probabilities summing to 1.0, correct EV arithmetic,
open-ended tail bucket handling, and the same graduation-example
inputs used throughout the rest of the suite.
"""

import math

import pytest

from app.calculations import bear_put_spread as calc
from app.calculations import probability_distribution as dist

UNDERLYING_PRICE = 82.00
LONG_STRIKE = 85
LONG_ASK = 5.10
SHORT_STRIKE = 77
SHORT_BID = 0.71
DEBIT_SHARE = calc.debit_per_share(LONG_ASK, SHORT_BID)  # 4.39
AVG_IV = 0.43
DTE = 30
EXPECTED_MOVE = calc.expected_move(UNDERLYING_PRICE, AVG_IV, DTE)  # ~10.11


class TestBucketProbability:
    def test_symmetric_bucket_around_zero(self):
        # P(-1 <= z <= 1) should be the familiar ~68.27%
        p = dist.bucket_probability(-1.0, 1.0)
        assert p == pytest.approx(0.6827, abs=1e-3)

    def test_open_lower_tail_to_zero(self):
        # P(z <= 0) is exactly 50%
        p = dist.bucket_probability(-math.inf, 0.0)
        assert p == pytest.approx(0.5, abs=1e-9)

    def test_open_upper_tail_from_zero(self):
        p = dist.bucket_probability(0.0, math.inf)
        assert p == pytest.approx(0.5, abs=1e-9)

    def test_full_range_sums_to_one(self):
        p = dist.bucket_probability(-math.inf, math.inf)
        assert p == pytest.approx(1.0, abs=1e-9)


class TestBuildProbabilityDistribution:
    def _build(self, **overrides):
        params = dict(
            underlying_price=UNDERLYING_PRICE,
            expected_move=EXPECTED_MOVE,
            long_strike=LONG_STRIKE,
            short_strike=SHORT_STRIKE,
            debit_share=DEBIT_SHARE,
            step=2.0,
        )
        params.update(overrides)
        return dist.build_probability_distribution(**params)

    def test_total_probability_sums_to_one(self):
        result = self._build()
        assert result["total_probability"] == pytest.approx(1.0, abs=1e-9)

    def test_bucket_probabilities_sum_matches_total(self):
        result = self._build()
        summed = sum(b["probability"] for b in result["buckets"])
        assert summed == pytest.approx(result["total_probability"], abs=1e-12)

    def test_first_and_last_buckets_are_open_ended(self):
        result = self._build()
        buckets = result["buckets"]
        assert buckets[0]["price_low"] is None
        assert buckets[0]["price_high"] is not None
        assert buckets[-1]["price_high"] is None
        assert buckets[-1]["price_low"] is not None

    def test_interior_buckets_have_both_edges(self):
        result = self._build()
        for b in result["buckets"][1:-1]:
            assert b["price_low"] is not None
            assert b["price_high"] is not None
            assert b["price_high"] > b["price_low"]

    def test_representative_prices_are_increasing(self):
        result = self._build()
        prices = [b["representative_price"] for b in result["buckets"]]
        assert prices == sorted(prices)

    def test_is_profit_matches_pl_sign(self):
        result = self._build()
        for b in result["buckets"]:
            assert b["is_profit"] == (b["pl_per_share"] > 0)

    def test_expected_value_matches_manual_sum(self):
        result = self._build()
        manual_ev_share = sum(b["probability"] * b["pl_per_share"] for b in result["buckets"])
        assert result["expected_value_per_share"] == pytest.approx(manual_ev_share, abs=1e-9)

    def test_expected_value_per_contract_is_100x_per_share(self):
        result = self._build()
        assert result["expected_value_per_contract"] == pytest.approx(
            result["expected_value_per_share"] * 100, abs=1e-6
        )

    def test_far_tail_buckets_are_flat_at_max_profit_or_loss(self):
        # With 3 std devs and this example's inputs, both tails extend
        # well past the strikes, so the tail buckets' P/L should equal
        # the flat max profit / max loss exactly.
        result = self._build(num_std_devs=3.0)
        width = calc.strike_width(LONG_STRIKE, SHORT_STRIKE)
        max_profit_share = calc.max_profit_per_share(width, DEBIT_SHARE)
        max_loss_share = -DEBIT_SHARE

        lower_tail = result["buckets"][0]
        upper_tail = result["buckets"][-1]
        assert lower_tail["pl_per_share"] == pytest.approx(max_profit_share, abs=1e-9)
        assert upper_tail["pl_per_share"] == pytest.approx(max_loss_share, abs=1e-9)

    def test_labels_are_human_readable(self):
        result = self._build()
        buckets = result["buckets"]
        assert buckets[0]["label"].startswith("<=")
        assert buckets[-1]["label"].startswith(">=")
        assert "-" in buckets[len(buckets) // 2]["label"]

    def test_mean_and_std_dev_echoed_back(self):
        result = self._build()
        assert result["mean"] == UNDERLYING_PRICE
        assert result["std_dev"] == EXPECTED_MOVE

    def test_zero_expected_move_raises(self):
        with pytest.raises(ValueError, match="Expected move"):
            self._build(expected_move=0)

    def test_negative_expected_move_raises(self):
        with pytest.raises(ValueError, match="Expected move"):
            self._build(expected_move=-1)

    def test_zero_step_raises(self):
        with pytest.raises(ValueError, match="step"):
            self._build(step=0)

    def test_negative_step_raises(self):
        with pytest.raises(ValueError, match="step"):
            self._build(step=-2)

    def test_wider_step_produces_fewer_buckets(self):
        narrow = self._build(step=1.0)
        wide = self._build(step=5.0)
        assert len(wide["buckets"]) < len(narrow["buckets"])
        # Both should still fully account for probability regardless of step.
        assert narrow["total_probability"] == pytest.approx(1.0, abs=1e-9)
        assert wide["total_probability"] == pytest.approx(1.0, abs=1e-9)
