"""Unit tests for the Phase 3 Monte Carlo simulation.

All tests use a fixed seed for reproducibility. The most important
test in this file is the convergence check: at a large number of
simulations, Monte Carlo's expected value should land close to Phase
2's exact, closed-form expected value, since both use the same
underlying normal-distribution model.
"""

import pytest

from app.calculations import bear_put_spread as calc
from app.calculations import monte_carlo as mc
from app.calculations import probability_distribution as dist
from app.calculations.stats import normal_cdf

UNDERLYING_PRICE = 82.00
LONG_STRIKE = 85
LONG_ASK = 5.10
SHORT_STRIKE = 77
SHORT_BID = 0.71
DEBIT_SHARE = calc.debit_per_share(LONG_ASK, SHORT_BID)  # 4.39
AVG_IV = 0.43
DTE = 30
EXPECTED_MOVE = calc.expected_move(UNDERLYING_PRICE, AVG_IV, DTE)  # ~10.11

FIXED_SEED = 42


def _simulate(**overrides):
    params = dict(
        underlying_price=UNDERLYING_PRICE,
        expected_move=EXPECTED_MOVE,
        long_strike=LONG_STRIKE,
        short_strike=SHORT_STRIKE,
        debit_share=DEBIT_SHARE,
        num_simulations=20_000,
        seed=FIXED_SEED,
    )
    params.update(overrides)
    return mc.run_simulation(**params)


class TestRunSimulationShape:
    def test_returns_requested_num_simulations(self):
        result = _simulate(num_simulations=5000)
        assert result["num_simulations"] == 5000

    def test_probabilities_are_between_zero_and_one(self):
        result = _simulate()
        for key in ("probability_of_profit", "probability_of_max_loss", "probability_of_max_profit"):
            assert 0.0 <= result[key] <= 1.0

    def test_probability_of_max_loss_le_probability_of_profit_complement(self):
        # Max loss is a subset of "not profitable" (price >= long strike
        # implies P/L = -debit < 0), so it can't exceed 1 - P(profit).
        result = _simulate()
        assert result["probability_of_max_loss"] <= 1.0 - result["probability_of_profit"] + 1e-9

    def test_percentiles_are_non_decreasing(self):
        result = _simulate()
        ordered = [
            result["percentile_5_pl_per_contract"],
            result["percentile_25_pl_per_contract"],
            result["median_pl_per_contract"],
            result["percentile_75_pl_per_contract"],
            result["percentile_95_pl_per_contract"],
        ]
        assert ordered == sorted(ordered)

    def test_expected_gain_is_positive_and_expected_loss_is_negative(self):
        result = _simulate()
        assert result["expected_gain_per_contract"] > 0
        assert result["expected_loss_per_contract"] < 0

    def test_sample_paths_length_matches_preview_count_or_fewer(self):
        result = _simulate(num_simulations=100)
        assert len(result["sample_paths"]) == min(mc.SAMPLE_PATH_PREVIEW_COUNT, 100)

    def test_sample_paths_are_sequentially_indexed(self):
        result = _simulate()
        indices = [p["index"] for p in result["sample_paths"]]
        assert indices == list(range(1, len(indices) + 1))

    def test_histogram_frequencies_sum_to_approximately_one(self):
        result = _simulate()
        total_freq = sum(b["frequency"] for b in result["histogram"])
        assert total_freq == pytest.approx(1.0, abs=1e-9)

    def test_histogram_counts_sum_to_num_simulations(self):
        result = _simulate(num_simulations=5000)
        total_count = sum(b["count"] for b in result["histogram"])
        assert total_count == 5000

    def test_expected_return_pct_matches_ev_over_debit(self):
        result = _simulate()
        debit_per_contract = DEBIT_SHARE * calc.CONTRACT_MULTIPLIER
        expected = result["expected_value_per_contract"] / debit_per_contract
        assert result["expected_return_pct"] == pytest.approx(expected, abs=1e-9)


class TestReproducibility:
    def test_same_seed_gives_identical_results(self):
        a = _simulate(seed=123)
        b = _simulate(seed=123)
        assert a["expected_value_per_contract"] == b["expected_value_per_contract"]
        assert a["sample_paths"] == b["sample_paths"]

    def test_different_seeds_give_different_sample_paths(self):
        a = _simulate(seed=1)
        b = _simulate(seed=2)
        assert a["sample_paths"] != b["sample_paths"]


class TestConvergenceToClosedForm:
    def test_large_n_expected_value_converges_to_phase2_closed_form(self):
        closed_form = dist.build_probability_distribution(
            underlying_price=UNDERLYING_PRICE,
            expected_move=EXPECTED_MOVE,
            long_strike=LONG_STRIKE,
            short_strike=SHORT_STRIKE,
            debit_share=DEBIT_SHARE,
            step=2.0,
        )
        simulated = _simulate(num_simulations=100_000, seed=7)
        # Monte Carlo standard error at n=100,000 is small, but not
        # zero -- allow a reasonably generous absolute tolerance rather
        # than an exact match.
        assert simulated["expected_value_per_contract"] == pytest.approx(
            closed_form["expected_value_per_contract"], abs=15.0
        )

    def test_large_n_probability_of_max_profit_converges_to_closed_form_tail(self):
        # P(price <= short strike) has an exact closed form: the normal
        # CDF at the short strike's z-score. Compare the simulated
        # frequency against that directly (not against a bucket sum,
        # which would only approximate this same integral).
        z_short = (SHORT_STRIKE - UNDERLYING_PRICE) / EXPECTED_MOVE
        expected_prob = normal_cdf(z_short)
        simulated = _simulate(num_simulations=100_000, seed=7)
        assert simulated["probability_of_max_profit"] == pytest.approx(expected_prob, abs=0.01)


class TestValidation:
    def test_zero_expected_move_raises(self):
        with pytest.raises(ValueError, match="Expected move"):
            _simulate(expected_move=0)

    def test_negative_expected_move_raises(self):
        with pytest.raises(ValueError, match="Expected move"):
            _simulate(expected_move=-5)

    def test_zero_num_simulations_raises(self):
        with pytest.raises(ValueError, match="at least 1"):
            _simulate(num_simulations=0)

    def test_negative_num_simulations_raises(self):
        with pytest.raises(ValueError, match="at least 1"):
            _simulate(num_simulations=-10)
