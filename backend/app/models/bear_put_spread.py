"""Input models for the bear put spread calculator.

These models are where we enforce that the inputs make financial
sense *before* any calculation runs. Pydantic raises a ValidationError
(which FastAPI turns into a 422 response with a clear message) if any
rule is broken, so invalid inputs never silently produce nonsense
output.

Note on IV: the ``iv`` field is a decimal fraction (0.44 means 44%),
matching how it is used directly in the expected-move formula. The
frontend is responsible for converting a user-typed percentage (44)
into this decimal form before sending it to the API, and converting
back for display.
"""

from pydantic import BaseModel, Field, model_validator


class UnderlyingInput(BaseModel):
    symbol: str = Field(..., min_length=1, description="Ticker symbol, e.g. AAPL")
    price: float = Field(..., gt=0, description="Current underlying price, must be > 0")
    dte: int = Field(..., ge=0, description="Days to expiration, must be >= 0")


class OptionLegInput(BaseModel):
    strike: float = Field(..., gt=0, description="Strike price, must be > 0")
    bid: float = Field(..., ge=0, description="Bid price, must be >= 0")
    ask: float = Field(..., ge=0, description="Ask price, must be >= 0")
    delta: float = Field(..., ge=-1, le=0, description="Put delta, must be between -1 and 0")
    iv: float = Field(..., ge=0, description="Implied volatility as a decimal (0.44 = 44%), must be >= 0")

    @model_validator(mode="after")
    def check_ask_gte_bid(self) -> "OptionLegInput":
        if self.ask < self.bid:
            raise ValueError("Ask must be greater than or equal to Bid.")
        return self


class BearPutSpreadRequest(BaseModel):
    underlying: UnderlyingInput
    long_put: OptionLegInput
    short_put: OptionLegInput

    @model_validator(mode="after")
    def check_strike_ordering(self) -> "BearPutSpreadRequest":
        if self.long_put.strike <= self.short_put.strike:
            raise ValueError(
                "Invalid strike ordering: for a bear put spread the long put "
                "strike must be strictly greater than the short put strike."
            )
        return self
