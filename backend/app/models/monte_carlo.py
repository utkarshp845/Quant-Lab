"""Request/response models for the Phase 3 Monte Carlo simulation endpoint.

Kept separate from models/response.py because this is a distinct,
explicitly-triggered endpoint (not part of the auto-recomputing main
analysis), with its own request shape (adds num_simulations / seed).
"""

from pydantic import BaseModel, Field, field_validator

from app.models.bear_put_spread import BearPutSpreadRequest


class MonteCarloRequest(BearPutSpreadRequest):
    num_simulations: int = Field(
        default=100_000,
        ge=100,
        le=200_000,
        description="How many random expiration prices to simulate.",
    )
    seed: int | None = Field(
        default=None,
        description="Optional fixed seed for reproducible results (used by tests).",
    )

    @field_validator("num_simulations")
    @classmethod
    def check_num_simulations(cls, v: int) -> int:
        if v < 100:
            raise ValueError("num_simulations must be at least 100.")
        return v


class SamplePath(BaseModel):
    index: int
    simulated_price: float
    pl_per_contract: float


class HistogramBin(BaseModel):
    price_low: float
    price_high: float
    frequency: float
    count: int
    pl_per_contract: float
    is_profit: bool


class MonteCarloResult(BaseModel):
    num_simulations: int
    probability_of_profit: float
    probability_of_max_loss: float
    probability_of_max_profit: float
    expected_value_per_contract: float
    expected_return_pct: float
    median_pl_per_contract: float
    percentile_5_pl_per_contract: float
    percentile_25_pl_per_contract: float
    percentile_75_pl_per_contract: float
    percentile_95_pl_per_contract: float
    expected_gain_per_contract: float
    expected_loss_per_contract: float
    closed_form_expected_value_per_contract: float
    sample_paths: list[SamplePath]
    histogram: list[HistogramBin]
    formula_expected_return: str = "Expected Return = Expected Value / Debit per Contract"
