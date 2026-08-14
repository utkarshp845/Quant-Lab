"""Response models for the bear put spread analysis endpoint.

Each section mirrors one part of the UX flow (inputs -> trade
structure -> risk/reward -> volatility -> probability -> payoff) and
carries both the computed numbers AND the raw inputs/formula text that
produced them, so the frontend can render "formula, inputs, result"
side by side instead of just a bare number.
"""

from pydantic import BaseModel


class DebitBreakdown(BaseModel):
    """The PRIMARY debit (mid-price based) -- drives every downstream
    calculation in this app (Risk/Reward, Probability Engine, Monte
    Carlo, Trade Summary). See ExecutionRealityCheck for the separate,
    deliberately-pessimistic ask/bid-based debit."""

    long_put_bid: float
    long_put_ask: float
    long_put_mid: float
    short_put_bid: float
    short_put_ask: float
    short_put_mid: float
    debit_per_share: float
    debit_per_contract: float
    formula_mid_price: str = "Mid Price = (Bid + Ask) / 2"
    formula: str = "Mid Debit = Long Put Mid - Short Put Mid"


class ExecutionRealityCheck(BaseModel):
    """A deliberately pessimistic, ask/bid-based view of the same trade,
    for judging realistic entry cost and whether the trade survives
    the cost of actually crossing the bid/ask spread on both legs.
    Does not feed any other calculation in this app."""

    long_put_ask: float
    short_put_bid: float
    conservative_debit_per_share: float
    conservative_debit_per_contract: float
    conservative_max_loss_per_contract: float
    conservative_max_profit_per_contract: float
    conservative_breakeven: float
    slippage_cost_per_contract: float
    formula_debit: str = "Conservative Entry Debit = Long Put Ask - Short Put Bid"
    formula_slippage: str = "Slippage Cost = (Conservative Entry Debit - Mid Debit) x 100"


class RiskReward(BaseModel):
    strike_width: float
    max_loss_per_contract: float
    max_profit_per_share: float
    max_profit_per_contract: float
    breakeven: float
    formula_strike_width: str = "Strike Width = Long Put Strike - Short Put Strike"
    formula_max_loss: str = "Max Loss = Mid Debit x 100"
    formula_max_profit: str = "Max Profit = (Strike Width - Mid Debit) x 100"
    formula_breakeven: str = "Breakeven = Long Put Strike - Mid Debit"


class DeltaAnalysis(BaseModel):
    long_delta: float
    short_delta: float
    spread_delta: float
    formula: str = "Spread Delta = Long Put Delta - Short Put Delta"


class VolatilityAnalysis(BaseModel):
    average_iv: float
    expected_move: float
    lower_1sd: float
    upper_1sd: float
    formula_average_iv: str = "Average IV = (Long IV + Short IV) / 2"
    formula_expected_move: str = "Expected Move = Underlying Price x Average IV x sqrt(DTE / 365)"


class ProbabilityAnalysis(BaseModel):
    z_score: float
    probability_below_breakeven: float
    formula_z: str = "z = (Breakeven - Underlying Price) / Expected Move"
    formula_probability: str = "Probability = Normal CDF(z)"


class PayoffScenario(BaseModel):
    expiration_price: float
    long_put_value: float
    short_put_value: float
    spread_value: float
    pl_per_share: float
    pl_per_contract: float
    is_profit: bool | None = None
    label: str | None = None


class DistributionBucket(BaseModel):
    # None on price_low means this bucket is the open-ended lower tail
    # (probability integrates out to -infinity); None on price_high
    # means the open-ended upper tail (out to +infinity).
    price_low: float | None
    price_high: float | None
    representative_price: float
    probability: float
    pl_per_share: float
    pl_per_contract: float
    is_profit: bool
    label: str


class ProbabilityDistribution(BaseModel):
    buckets: list[DistributionBucket]
    expected_value_per_share: float
    expected_value_per_contract: float
    total_probability: float
    mean: float
    std_dev: float
    formula_bucket_probability: str = "P(bucket) = Normal CDF(z_high) - Normal CDF(z_low)"
    formula_expected_value: str = "EV = sum( P(bucket_i) x P/L(bucket_i) )"


class TradeSummary(BaseModel):
    symbol: str
    underlying_price: float
    dte: int
    long_put_strike: float
    short_put_strike: float
    debit_per_contract: float
    conservative_debit_per_contract: float
    max_loss_per_contract: float
    max_profit_per_contract: float
    breakeven: float
    spread_delta: float
    average_iv: float
    expected_move: float
    probability_below_breakeven: float
    expected_value_per_contract: float


class BearPutSpreadResponse(BaseModel):
    debit: DebitBreakdown
    execution_check: ExecutionRealityCheck
    risk_reward: RiskReward
    delta: DeltaAnalysis
    volatility: VolatilityAnalysis
    probability: ProbabilityAnalysis
    distribution: ProbabilityDistribution
    payoff_table: list[PayoffScenario]
    payoff_chart_points: list[PayoffScenario]
    summary: TradeSummary
