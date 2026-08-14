"""Tests for input validation (spec section 20 + section 19's invalid-input cases).

These exercise the pydantic models directly, confirming that bad
financial inputs are rejected with a clear error rather than silently
flowing into the calculations.
"""

import pytest
from pydantic import ValidationError

from app.models.bear_put_spread import BearPutSpreadRequest, OptionLegInput, UnderlyingInput

VALID_UNDERLYING = {"symbol": "XYZ", "price": 82.0, "dte": 30}
VALID_LONG = {"strike": 85, "bid": 5.00, "ask": 5.10, "delta": -0.58, "iv": 0.44}
VALID_SHORT = {"strike": 77, "bid": 0.71, "ask": 0.85, "delta": -0.29, "iv": 0.42}


def make_request(underlying=None, long_put=None, short_put=None) -> dict:
    return {
        "underlying": {**VALID_UNDERLYING, **(underlying or {})},
        "long_put": {**VALID_LONG, **(long_put or {})},
        "short_put": {**VALID_SHORT, **(short_put or {})},
    }


class TestValidRequestPasses:
    def test_graduation_example_is_valid(self):
        req = BearPutSpreadRequest(**make_request())
        assert req.long_put.strike == 85
        assert req.short_put.strike == 77


class TestStrikeOrdering:
    def test_long_strike_must_exceed_short_strike(self):
        with pytest.raises(ValidationError, match="strike ordering"):
            BearPutSpreadRequest(**make_request(long_put={"strike": 77}, short_put={"strike": 85}))

    def test_equal_strikes_rejected(self):
        with pytest.raises(ValidationError, match="strike ordering"):
            BearPutSpreadRequest(**make_request(long_put={"strike": 80}, short_put={"strike": 80}))


class TestUnderlyingValidation:
    def test_dte_must_be_non_negative(self):
        with pytest.raises(ValidationError):
            UnderlyingInput(symbol="XYZ", price=82, dte=-1)

    def test_zero_dte_is_allowed(self):
        u = UnderlyingInput(symbol="XYZ", price=82, dte=0)
        assert u.dte == 0

    def test_price_must_be_positive(self):
        with pytest.raises(ValidationError):
            UnderlyingInput(symbol="XYZ", price=0, dte=30)
        with pytest.raises(ValidationError):
            UnderlyingInput(symbol="XYZ", price=-10, dte=30)

    def test_missing_symbol_rejected(self):
        with pytest.raises(ValidationError):
            UnderlyingInput(symbol="", price=82, dte=30)

    def test_missing_field_rejected(self):
        with pytest.raises(ValidationError):
            UnderlyingInput(symbol="XYZ", dte=30)  # missing price


class TestOptionLegValidation:
    def test_negative_bid_rejected(self):
        with pytest.raises(ValidationError):
            OptionLegInput(strike=85, bid=-1, ask=5.10, delta=-0.58, iv=0.44)

    def test_negative_ask_rejected(self):
        with pytest.raises(ValidationError):
            OptionLegInput(strike=85, bid=5.00, ask=-1, delta=-0.58, iv=0.44)

    def test_negative_strike_rejected(self):
        with pytest.raises(ValidationError):
            OptionLegInput(strike=-85, bid=5.00, ask=5.10, delta=-0.58, iv=0.44)

    def test_negative_iv_rejected(self):
        with pytest.raises(ValidationError):
            OptionLegInput(strike=85, bid=5.00, ask=5.10, delta=-0.58, iv=-0.10)

    def test_ask_below_bid_rejected(self):
        with pytest.raises(ValidationError, match="Ask must be"):
            OptionLegInput(strike=85, bid=5.10, ask=5.00, delta=-0.58, iv=0.44)

    def test_ask_equal_bid_allowed(self):
        leg = OptionLegInput(strike=85, bid=5.00, ask=5.00, delta=-0.58, iv=0.44)
        assert leg.ask == leg.bid

    def test_delta_must_be_between_neg1_and_0(self):
        with pytest.raises(ValidationError):
            OptionLegInput(strike=85, bid=5.00, ask=5.10, delta=0.5, iv=0.44)
        with pytest.raises(ValidationError):
            OptionLegInput(strike=85, bid=5.00, ask=5.10, delta=-1.5, iv=0.44)

    def test_delta_boundary_values_allowed(self):
        leg_zero = OptionLegInput(strike=85, bid=5.00, ask=5.10, delta=0, iv=0.44)
        leg_neg_one = OptionLegInput(strike=85, bid=5.00, ask=5.10, delta=-1, iv=0.44)
        assert leg_zero.delta == 0
        assert leg_neg_one.delta == -1

    def test_missing_required_field_rejected(self):
        with pytest.raises(ValidationError):
            OptionLegInput(strike=85, bid=5.00, ask=5.10, delta=-0.58)  # missing iv
