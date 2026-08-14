"""API routes for the bear put spread calculator.

This layer does no math of its own -- it validates the request
(via the pydantic models), calls the pure functions in
app.calculations, and assembles the results into the response model.
Every calculation call is visible here in sequence, so tracing "how
did we get this number" only ever requires reading this one function
top to bottom.
"""

from fastapi import APIRouter, HTTPException

from app.calculations import bear_put_spread as calc
from app.calculations import monte_carlo as mc
from app.calculations import payoff_scenarios
from app.calculations import probability_distribution as dist
from app.models.bear_put_spread import BearPutSpreadRequest
from app.models.monte_carlo import MonteCarloRequest, MonteCarloResult
from app.models.response import (
    BearPutSpreadResponse,
    DebitBreakdown,
    DeltaAnalysis,
    DistributionBucket,
    ExecutionRealityCheck,
    PayoffScenario,
    ProbabilityAnalysis,
    ProbabilityDistribution,
    RiskReward,
    TradeSummary,
    VolatilityAnalysis,
)

router = APIRouter()


def _analyze(request: BearPutSpreadRequest) -> BearPutSpreadResponse:
    underlying = request.underlying
    long_put = request.long_put
    short_put = request.short_put

    # --- Trade structure / cost -------------------------------------------------
    # PRIMARY debit: mid-price based. This is what drives every
    # calculation below (Risk/Reward, Probability, Monte Carlo) --
    # see the module docstring in calculations/bear_put_spread.py.
    long_mid = calc.mid_price(long_put.bid, long_put.ask)
    short_mid = calc.mid_price(short_put.bid, short_put.ask)
    debit_share = calc.debit_per_share(long_mid, short_mid)
    debit_contract = calc.debit_per_contract(debit_share)

    # SECONDARY debit: conservative, ask/bid based. Used only for the
    # Execution Reality Check below -- never feeds another calculation.
    conservative_debit_share = calc.debit_per_share(long_put.ask, short_put.bid)
    conservative_debit_contract = calc.debit_per_contract(conservative_debit_share)

    # --- Risk / reward (mid-debit based) ---------------------------------------
    width = calc.strike_width(long_put.strike, short_put.strike)
    max_loss = calc.max_loss_per_contract(debit_share)
    max_profit_share = calc.max_profit_per_share(width, debit_share)
    max_profit_contract = calc.max_profit_per_contract(max_profit_share)
    breakeven = calc.breakeven_price(long_put.strike, debit_share)

    # --- Execution Reality Check (conservative-debit based) ---------------------
    conservative_max_loss = calc.max_loss_per_contract(conservative_debit_share)
    conservative_max_profit_share = calc.max_profit_per_share(width, conservative_debit_share)
    conservative_max_profit_contract = calc.max_profit_per_contract(conservative_max_profit_share)
    conservative_breakeven = calc.breakeven_price(long_put.strike, conservative_debit_share)
    slippage_cost = conservative_debit_contract - debit_contract

    # --- Delta ----------------------------------------------------------------
    net_delta = calc.spread_delta(long_put.delta, short_put.delta)

    # --- Volatility -------------------------------------------------------------
    avg_iv = calc.average_iv(long_put.iv, short_put.iv)
    exp_move = calc.expected_move(underlying.price, avg_iv, underlying.dte)
    lower_1sd, upper_1sd = calc.one_sigma_bounds(underlying.price, exp_move)

    # --- Probability --------------------------------------------------------
    z = calc.z_score(breakeven, underlying.price, exp_move)
    probability = calc.probability_below_breakeven(z)

    # --- Probability distribution + expected value ---------------------------
    distribution = dist.build_probability_distribution(
        underlying_price=underlying.price,
        expected_move=exp_move,
        long_strike=long_put.strike,
        short_strike=short_put.strike,
        debit_share=debit_share,
        step=max(1.0, round(width / 4)),
    )

    # --- Payoff table + chart -------------------------------------------------
    table = payoff_scenarios.generate_payoff_table(
        underlying_price=underlying.price,
        long_strike=long_put.strike,
        short_strike=short_put.strike,
        breakeven=breakeven,
        debit_share=debit_share,
    )
    chart_points = payoff_scenarios.generate_payoff_chart_points(
        long_strike=long_put.strike,
        short_strike=short_put.strike,
        debit_share=debit_share,
        padding=max(width, exp_move, 1.0),
    )

    return BearPutSpreadResponse(
        debit=DebitBreakdown(
            long_put_bid=long_put.bid,
            long_put_ask=long_put.ask,
            long_put_mid=long_mid,
            short_put_bid=short_put.bid,
            short_put_ask=short_put.ask,
            short_put_mid=short_mid,
            debit_per_share=debit_share,
            debit_per_contract=debit_contract,
        ),
        execution_check=ExecutionRealityCheck(
            long_put_ask=long_put.ask,
            short_put_bid=short_put.bid,
            conservative_debit_per_share=conservative_debit_share,
            conservative_debit_per_contract=conservative_debit_contract,
            conservative_max_loss_per_contract=conservative_max_loss,
            conservative_max_profit_per_contract=conservative_max_profit_contract,
            conservative_breakeven=conservative_breakeven,
            slippage_cost_per_contract=slippage_cost,
        ),
        risk_reward=RiskReward(
            strike_width=width,
            max_loss_per_contract=max_loss,
            max_profit_per_share=max_profit_share,
            max_profit_per_contract=max_profit_contract,
            breakeven=breakeven,
        ),
        delta=DeltaAnalysis(
            long_delta=long_put.delta,
            short_delta=short_put.delta,
            spread_delta=net_delta,
        ),
        volatility=VolatilityAnalysis(
            average_iv=avg_iv,
            expected_move=exp_move,
            lower_1sd=lower_1sd,
            upper_1sd=upper_1sd,
        ),
        probability=ProbabilityAnalysis(
            z_score=z,
            probability_below_breakeven=probability,
        ),
        distribution=ProbabilityDistribution(
            buckets=[DistributionBucket(**b) for b in distribution["buckets"]],
            expected_value_per_share=distribution["expected_value_per_share"],
            expected_value_per_contract=distribution["expected_value_per_contract"],
            total_probability=distribution["total_probability"],
            mean=distribution["mean"],
            std_dev=distribution["std_dev"],
        ),
        payoff_table=[PayoffScenario(**row) for row in table],
        payoff_chart_points=[PayoffScenario(**row) for row in chart_points],
        summary=TradeSummary(
            symbol=underlying.symbol,
            underlying_price=underlying.price,
            dte=underlying.dte,
            long_put_strike=long_put.strike,
            short_put_strike=short_put.strike,
            debit_per_contract=debit_contract,
            conservative_debit_per_contract=conservative_debit_contract,
            max_loss_per_contract=max_loss,
            max_profit_per_contract=max_profit_contract,
            breakeven=breakeven,
            spread_delta=net_delta,
            average_iv=avg_iv,
            expected_move=exp_move,
            probability_below_breakeven=probability,
            expected_value_per_contract=distribution["expected_value_per_contract"],
        ),
    )


@router.post("/bear-put-spread", response_model=BearPutSpreadResponse)
def analyze_bear_put_spread(request: BearPutSpreadRequest) -> BearPutSpreadResponse:
    """Run the full bear put spread analysis on the given inputs.

    A ValueError from the calculation layer (e.g. a z-score that is
    undefined because DTE=0 makes the expected move zero) is a valid,
    anticipated input combination -- not a server bug -- so it is
    reported back as a 422 with a clear message rather than a 500.
    """
    try:
        return _analyze(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class PayoffAtPriceRequest(BearPutSpreadRequest):
    expiration_price: float


@router.post("/bear-put-spread/payoff-at-price", response_model=PayoffScenario)
def payoff_at_price(request: PayoffAtPriceRequest) -> PayoffScenario:
    """Compute the spread's P/L for one arbitrary expiration price.

    This backs the "payoff calculator" section, where the user types
    in a hypothetical expiration price and sees the resulting P/L,
    separate from the fixed scenario table. Uses the primary (mid)
    debit, matching every other calculation in this app.
    """
    long_mid = calc.mid_price(request.long_put.bid, request.long_put.ask)
    short_mid = calc.mid_price(request.short_put.bid, request.short_put.ask)
    debit_share = calc.debit_per_share(long_mid, short_mid)
    result = calc.payoff_at_expiration(
        long_strike=request.long_put.strike,
        short_strike=request.short_put.strike,
        expiration_price=request.expiration_price,
        debit_share=debit_share,
    )
    return PayoffScenario(**result, is_profit=result["pl_per_share"] > 0)


@router.post("/bear-put-spread/monte-carlo", response_model=MonteCarloResult)
def monte_carlo_simulation(request: MonteCarloRequest) -> MonteCarloResult:
    """Phase 3: simulate many random expiration prices and summarize the outcomes.

    This is a separate, explicitly-triggered endpoint (not part of the
    auto-recomputing main analysis) since a 100,000-path simulation is
    too expensive to re-run on every keystroke. It reuses the exact
    same debit/expected-move calculations as the main endpoint (the
    primary, mid-price debit), then hands off to
    app.calculations.monte_carlo for the random sampling.
    """
    underlying = request.underlying
    long_put = request.long_put
    short_put = request.short_put

    long_mid = calc.mid_price(long_put.bid, long_put.ask)
    short_mid = calc.mid_price(short_put.bid, short_put.ask)
    debit_share = calc.debit_per_share(long_mid, short_mid)
    avg_iv = calc.average_iv(long_put.iv, short_put.iv)
    exp_move = calc.expected_move(underlying.price, avg_iv, underlying.dte)

    # The Phase 2 closed-form EV, computed with the same bucket step
    # convention used elsewhere, so the UI can show it side by side
    # with the Monte Carlo estimate as a convergence / sanity check.
    width = calc.strike_width(long_put.strike, short_put.strike)
    try:
        closed_form = dist.build_probability_distribution(
            underlying_price=underlying.price,
            expected_move=exp_move,
            long_strike=long_put.strike,
            short_strike=short_put.strike,
            debit_share=debit_share,
            step=max(1.0, round(width / 4)),
        )
        result = mc.run_simulation(
            underlying_price=underlying.price,
            expected_move=exp_move,
            long_strike=long_put.strike,
            short_strike=short_put.strike,
            debit_share=debit_share,
            num_simulations=request.num_simulations,
            seed=request.seed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return MonteCarloResult(
        **result,
        closed_form_expected_value_per_contract=closed_form["expected_value_per_contract"],
    )
