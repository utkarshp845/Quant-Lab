"""Unit tests for every pure calculation function.

Two kinds of tests live here:

1. The "graduation-test" example from the spec (Underlying $82, Long
   Put 85/5.10, Short Put 77/0.71, 30 DTE, IVs 44%/42%) -- this is the
   worked example a human can check by hand, used as a regression
   anchor for the whole calculation chain. It is tested TWICE, once
   for each debit convention this app computes:
     - Mid Debit (primary): drives Risk/Reward, Probability, Monte
       Carlo everywhere in the app.
     - Conservative Entry Debit (ask/bid): the "Execution Reality
       Check" -- this is the original spec's worked-example debit of
       $4.39, still exactly computed, just no longer the number that
       drives the rest of the analysis.
2. Focused edge-case tests for each function in isolation: underlying
   above/between/below both strikes, exact strike/breakeven prices,
   zero DTE, etc.
"""

import math

import pytest

from app.calculations import bear_put_spread as calc
from app.calculations.stats import normal_cdf

# ---------------------------------------------------------------------------
# Graduation-test example (see spec section 18)
# ---------------------------------------------------------------------------

UNDERLYING_PRICE = 82.00
DTE = 30

LONG_STRIKE = 85
LONG_BID = 5.00
LONG_ASK = 5.10
LONG_DELTA = -0.58
LONG_IV = 0.44

SHORT_STRIKE = 77
SHORT_BID = 0.71
SHORT_ASK = 0.85
SHORT_DELTA = -0.29
SHORT_IV = 0.42


class TestMidPrice:
    def test_mid_price_is_average_of_bid_and_ask(self):
        assert calc.mid_price(bid=5.00, ask=5.10) == pytest.approx(5.05)

    def test_mid_price_graduation_example_long_leg(self):
        assert calc.mid_price(LONG_BID, LONG_ASK) == pytest.approx(5.05)

    def test_mid_price_graduation_example_short_leg(self):
        assert calc.mid_price(SHORT_BID, SHORT_ASK) == pytest.approx(0.78)

    def test_mid_price_equals_bid_when_bid_equals_ask(self):
        assert calc.mid_price(bid=2.00, ask=2.00) == pytest.approx(2.00)


class TestGraduationExampleMidDebit:
    """The PRIMARY debit convention -- drives everything downstream."""

    def _mid_debit(self):
        long_mid = calc.mid_price(LONG_BID, LONG_ASK)
        short_mid = calc.mid_price(SHORT_BID, SHORT_ASK)
        return calc.debit_per_share(long_mid, short_mid)

    def test_debit(self):
        debit = self._mid_debit()
        assert debit == pytest.approx(4.27)
        assert calc.debit_per_contract(debit) == pytest.approx(427.0)

    def test_max_loss(self):
        debit = self._mid_debit()
        assert calc.max_loss_per_contract(debit) == pytest.approx(427.0)

    def test_max_profit(self):
        debit = self._mid_debit()
        width = calc.strike_width(LONG_STRIKE, SHORT_STRIKE)
        assert width == 8
        max_profit_share = calc.max_profit_per_share(width, debit)
        assert max_profit_share == pytest.approx(3.73)
        assert calc.max_profit_per_contract(max_profit_share) == pytest.approx(373.0)

    def test_breakeven(self):
        debit = self._mid_debit()
        assert calc.breakeven_price(LONG_STRIKE, debit) == pytest.approx(80.73)

    def test_spread_delta(self):
        assert calc.spread_delta(LONG_DELTA, SHORT_DELTA) == pytest.approx(-0.29)

    def test_average_iv(self):
        assert calc.average_iv(LONG_IV, SHORT_IV) == pytest.approx(0.43)

    def test_expected_move(self):
        avg_iv = calc.average_iv(LONG_IV, SHORT_IV)
        move = calc.expected_move(UNDERLYING_PRICE, avg_iv, DTE)
        assert move == pytest.approx(10.10, abs=0.01)

    def test_one_sigma_bounds(self):
        avg_iv = calc.average_iv(LONG_IV, SHORT_IV)
        move = calc.expected_move(UNDERLYING_PRICE, avg_iv, DTE)
        lower, upper = calc.one_sigma_bounds(UNDERLYING_PRICE, move)
        assert lower == pytest.approx(UNDERLYING_PRICE - move)
        assert upper == pytest.approx(UNDERLYING_PRICE + move)

    def test_z_score(self):
        debit = self._mid_debit()
        breakeven = calc.breakeven_price(LONG_STRIKE, debit)
        avg_iv = calc.average_iv(LONG_IV, SHORT_IV)
        move = calc.expected_move(UNDERLYING_PRICE, avg_iv, DTE)
        z = calc.z_score(breakeven, UNDERLYING_PRICE, move)
        assert z == pytest.approx(-0.126, abs=0.001)

    def test_probability_below_breakeven(self):
        debit = self._mid_debit()
        breakeven = calc.breakeven_price(LONG_STRIKE, debit)
        avg_iv = calc.average_iv(LONG_IV, SHORT_IV)
        move = calc.expected_move(UNDERLYING_PRICE, avg_iv, DTE)
        z = calc.z_score(breakeven, UNDERLYING_PRICE, move)
        prob = calc.probability_below_breakeven(z)
        assert prob == pytest.approx(0.450, abs=0.001)


class TestGraduationExampleConservativeDebit:
    """The SECONDARY, ask/bid-based debit -- the Execution Reality
    Check. This is the original spec's worked-example debit ($4.39);
    it's still computed exactly, just no longer drives the rest of the
    app's analysis (Mid Debit does -- see TestGraduationExampleMidDebit)."""

    def test_debit(self):
        debit = calc.debit_per_share(LONG_ASK, SHORT_BID)
        assert debit == pytest.approx(4.39)
        assert calc.debit_per_contract(debit) == pytest.approx(439.0)

    def test_max_loss(self):
        debit = calc.debit_per_share(LONG_ASK, SHORT_BID)
        assert calc.max_loss_per_contract(debit) == pytest.approx(439.0)

    def test_max_profit(self):
        debit = calc.debit_per_share(LONG_ASK, SHORT_BID)
        width = calc.strike_width(LONG_STRIKE, SHORT_STRIKE)
        max_profit_share = calc.max_profit_per_share(width, debit)
        assert max_profit_share == pytest.approx(3.61)
        assert calc.max_profit_per_contract(max_profit_share) == pytest.approx(361.0)

    def test_breakeven(self):
        debit = calc.debit_per_share(LONG_ASK, SHORT_BID)
        assert calc.breakeven_price(LONG_STRIKE, debit) == pytest.approx(80.61)

    def test_slippage_cost_vs_mid_debit(self):
        conservative_debit = calc.debit_per_share(LONG_ASK, SHORT_BID)
        long_mid = calc.mid_price(LONG_BID, LONG_ASK)
        short_mid = calc.mid_price(SHORT_BID, SHORT_ASK)
        mid_debit = calc.debit_per_share(long_mid, short_mid)
        slippage_per_contract = calc.debit_per_contract(conservative_debit) - calc.debit_per_contract(mid_debit)
        assert slippage_per_contract == pytest.approx(12.0)


# ---------------------------------------------------------------------------
# normal_cdf
# ---------------------------------------------------------------------------


class TestNormalCdf:
    def test_zero_is_half(self):
        assert normal_cdf(0.0) == pytest.approx(0.5)

    def test_symmetry(self):
        assert normal_cdf(1.0) + normal_cdf(-1.0) == pytest.approx(1.0)

    def test_known_values(self):
        # Standard normal table values.
        assert normal_cdf(1.0) == pytest.approx(0.8413, abs=1e-4)
        assert normal_cdf(-1.0) == pytest.approx(0.1587, abs=1e-4)
        assert normal_cdf(1.96) == pytest.approx(0.975, abs=1e-3)

    def test_extreme_values_bounded(self):
        assert normal_cdf(10) == pytest.approx(1.0, abs=1e-6)
        assert normal_cdf(-10) == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Intrinsic value / payoff -- underlying above / between / below strikes,
# and exactly at each strike / at breakeven.
# ---------------------------------------------------------------------------


class TestIntrinsicValueAndPayoff:
    LONG_STRIKE = 85
    SHORT_STRIKE = 77
    DEBIT = 4.27  # the graduation example's primary (mid) debit

    def test_underlying_above_both_strikes(self):
        # Both puts finish out-of-the-money -> spread worth 0 -> max loss.
        price = 90
        long_val = calc.long_put_intrinsic_value(self.LONG_STRIKE, price)
        short_val = calc.short_put_intrinsic_value(self.SHORT_STRIKE, price)
        assert long_val == 0
        assert short_val == 0
        spread_val = calc.spread_intrinsic_value(long_val, short_val)
        pl = calc.payoff_pl_per_share(spread_val, self.DEBIT)
        assert pl == pytest.approx(-self.DEBIT)
        assert calc.payoff_pl_per_contract(pl) == pytest.approx(-427.0)

    def test_underlying_between_strikes(self):
        price = 80
        long_val = calc.long_put_intrinsic_value(self.LONG_STRIKE, price)
        short_val = calc.short_put_intrinsic_value(self.SHORT_STRIKE, price)
        assert long_val == 5  # 85 - 80
        assert short_val == 0  # short put OTM
        spread_val = calc.spread_intrinsic_value(long_val, short_val)
        assert spread_val == 5
        pl = calc.payoff_pl_per_share(spread_val, self.DEBIT)
        assert pl == pytest.approx(5 - self.DEBIT)

    def test_underlying_below_both_strikes(self):
        # Both puts finish in-the-money -> spread worth exactly the width -> max profit.
        price = 70
        long_val = calc.long_put_intrinsic_value(self.LONG_STRIKE, price)
        short_val = calc.short_put_intrinsic_value(self.SHORT_STRIKE, price)
        assert long_val == 15  # 85 - 70
        assert short_val == 7  # 77 - 70
        spread_val = calc.spread_intrinsic_value(long_val, short_val)
        assert spread_val == 8  # equals strike width
        pl = calc.payoff_pl_per_share(spread_val, self.DEBIT)
        assert pl == pytest.approx(8 - self.DEBIT)
        assert pl == pytest.approx(3.73)

    def test_underlying_exactly_at_long_strike(self):
        price = self.LONG_STRIKE
        long_val = calc.long_put_intrinsic_value(self.LONG_STRIKE, price)
        assert long_val == 0

    def test_underlying_exactly_at_short_strike(self):
        price = self.SHORT_STRIKE
        short_val = calc.short_put_intrinsic_value(self.SHORT_STRIKE, price)
        assert short_val == 0
        long_val = calc.long_put_intrinsic_value(self.LONG_STRIKE, price)
        assert long_val == self.LONG_STRIKE - self.SHORT_STRIKE  # equals width

    def test_underlying_exactly_at_breakeven(self):
        breakeven = calc.breakeven_price(self.LONG_STRIKE, self.DEBIT)
        result = calc.payoff_at_expiration(
            self.LONG_STRIKE, self.SHORT_STRIKE, breakeven, self.DEBIT
        )
        assert result["pl_per_share"] == pytest.approx(0.0, abs=1e-9)
        assert result["pl_per_contract"] == pytest.approx(0.0, abs=1e-7)

    def test_payoff_at_expiration_matches_manual_chain(self):
        result = calc.payoff_at_expiration(self.LONG_STRIKE, self.SHORT_STRIKE, 80, self.DEBIT)
        assert result["long_put_value"] == 5
        assert result["short_put_value"] == 0
        assert result["spread_value"] == 5
        assert result["pl_per_share"] == pytest.approx(5 - self.DEBIT)
        assert result["pl_per_contract"] == pytest.approx((5 - self.DEBIT) * 100)


# ---------------------------------------------------------------------------
# Edge cases: zero DTE, z-score undefined, spread delta sign handling
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_zero_dte_gives_zero_expected_move(self):
        move = calc.expected_move(underlying_price=82, avg_iv_value=0.43, dte=0)
        assert move == 0.0

    def test_zero_expected_move_z_score_raises(self):
        with pytest.raises(ValueError):
            calc.z_score(breakeven=80.61, underlying_price=82, expected_move_value=0)

    def test_spread_delta_both_negative(self):
        # Both put deltas negative; subtracting a less-negative short delta
        # from a more-negative long delta should net to a smaller-magnitude
        # negative number.
        result = calc.spread_delta(long_delta=-0.58, short_delta=-0.29)
        assert result == pytest.approx(-0.29)
        assert -1 < result < 0

    def test_strike_width_computation(self):
        assert calc.strike_width(long_strike=85, short_strike=77) == 8

    def test_average_iv_symmetric_inputs(self):
        assert calc.average_iv(0.30, 0.30) == pytest.approx(0.30)

    def test_debit_can_be_negative_if_short_bid_exceeds_long_ask(self):
        # The pure function does not forbid this -- it is the input
        # validation layer's job to catch nonsensical quotes. This test
        # documents that the arithmetic itself has no hidden guard.
        debit = calc.debit_per_share(long_put_price=1.00, short_put_price=1.50)
        assert debit == pytest.approx(-0.50)
