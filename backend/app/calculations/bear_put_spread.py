"""Pure calculation functions for a bear put (debit put) spread.

A bear put spread is built by:
  - BUYING a put at a higher strike (the "long put").
  - SELLING a put at a lower strike (the "short put").

Every function here does exactly one small, named calculation and has
a docstring stating the formula in plain terms. Nothing here reaches
out to a network, a database, or hides intermediate values -- the API
layer calls these in sequence and returns every intermediate number,
not just the final answer, so the whole chain of reasoning is visible
in the UI.

All "per share" values are option-contract-style dollars per share of
the underlying. Multiply by CONTRACT_MULTIPLIER to get per-contract
(i.e. per 100 shares) dollar values, since one equity option contract
covers 100 shares.

TWO DEBITS: this app computes the debit two different ways, because
they answer two different questions:

  - MID DEBIT (long mid price - short mid price) is what the market is
    "really" quoting the spread at, ignoring the bid/ask spread. It is
    the PRIMARY debit used throughout this app -- it drives Max
    Loss/Profit, Breakeven, the Probability Engine, and Monte Carlo --
    because it is the right number for theoretical comparison, EV
    modeling, and comparing many candidate trades against each other.
  - CONSERVATIVE ENTRY DEBIT (long ask - short bid) is what you would
    actually pay if you had to cross the full bid/ask spread on both
    legs at once. It is deliberately pessimistic, used only for the
    "Execution Reality Check": a realistic worst-case entry cost, and
    whether the trade still makes sense after slippage.

Both are computed with the exact same `debit_per_share` formula below,
just fed different input prices -- the formula itself does not care
which convention its two inputs came from.
"""

import math

from .stats import normal_cdf

CONTRACT_MULTIPLIER = 100


# ---------------------------------------------------------------------------
# Trade structure / cost
# ---------------------------------------------------------------------------


def mid_price(bid: float, ask: float) -> float:
    """Mid Price = (Bid + Ask) / 2.

    The midpoint between the quoted bid and ask -- a common stand-in
    for "fair value" when no trade has actually happened yet.
    """
    return (bid + ask) / 2.0


def debit_per_share(long_put_price: float, short_put_price: float) -> float:
    """Debit (per share) = Long Put Price - Short Put Price.

    We buy the long put and sell the short put, so the net cost to
    enter the spread is the difference between whatever two prices are
    passed in. This app calls this same formula twice, with two
    different price conventions -- see the module docstring above:

        Mid Debit         = debit_per_share(long_mid, short_mid)
        Conservative Debit = debit_per_share(long_ask, short_bid)
    """
    return long_put_price - short_put_price


def debit_per_contract(debit_share: float) -> float:
    """Debit (per contract) = Debit per share x 100 shares/contract."""
    return debit_share * CONTRACT_MULTIPLIER


# ---------------------------------------------------------------------------
# Risk / reward
# ---------------------------------------------------------------------------


def max_loss_per_contract(debit_share: float) -> float:
    """Max Loss (per contract) = Debit x 100.

    A debit spread's maximum possible loss is the amount paid to enter
    it: if the underlying finishes at or above the long put's strike,
    both puts expire worthless and the debit is lost in full.
    """
    return debit_share * CONTRACT_MULTIPLIER


def strike_width(long_strike: float, short_strike: float) -> float:
    """Strike Width = Long Put Strike - Short Put Strike."""
    return long_strike - short_strike


def max_profit_per_share(strike_width_value: float, debit_share: float) -> float:
    """Max Profit (per share) = Strike Width - Debit.

    The most the spread can ever be worth at expiration is the strike
    width (when the underlying finishes at or below the short strike).
    Subtracting what was paid (the debit) gives the maximum profit.
    """
    return strike_width_value - debit_share


def max_profit_per_contract(max_profit_share: float) -> float:
    """Max Profit (per contract) = Max Profit per share x 100."""
    return max_profit_share * CONTRACT_MULTIPLIER


def breakeven_price(long_strike: float, debit_share: float) -> float:
    """Breakeven = Long Put Strike - Debit.

    At expiration the spread's intrinsic value equals
    (Long Strike - Underlying Price) as long as the underlying is
    between the two strikes. Setting that equal to the debit paid and
    solving for the underlying price gives this formula.
    """
    return long_strike - debit_share


# ---------------------------------------------------------------------------
# Delta (approximate sensitivity)
# ---------------------------------------------------------------------------


def spread_delta(long_delta: float, short_delta: float) -> float:
    """Spread Delta = Long Put Delta - Short Put Delta.

    Put deltas are negative. Selling the short put means we are short
    its delta, i.e. we subtract it (subtracting a negative number
    makes the spread's net delta less negative than the long put alone).
    """
    return long_delta - short_delta


# ---------------------------------------------------------------------------
# Volatility (simplified, educational approximations)
# ---------------------------------------------------------------------------


def average_iv(long_iv: float, short_iv: float) -> float:
    """Average IV = (Long IV + Short IV) / 2.

    IMPORTANT: this is a simplified educational approximation, not the
    true implied volatility of the spread as a combined position. It
    just averages the two legs' independently-quoted IVs.
    """
    return (long_iv + short_iv) / 2.0


def expected_move(underlying_price: float, avg_iv_value: float, dte: int) -> float:
    """Expected 1-standard-deviation move.

        Expected Move = Underlying Price x Average IV x sqrt(DTE / 365)

    This treats implied volatility as an annualized standard deviation
    of returns and scales it down to the number of days left using the
    square-root-of-time rule. It assumes lognormal-ish, symmetric
    price dispersion -- a simplification, not an exact model.
    """
    return underlying_price * avg_iv_value * math.sqrt(dte / 365.0)


def one_sigma_bounds(underlying_price: float, expected_move_value: float) -> tuple[float, float]:
    """Lower/Upper 1-sigma boundaries = Underlying Price -/+ Expected Move."""
    return underlying_price - expected_move_value, underlying_price + expected_move_value


# ---------------------------------------------------------------------------
# Probability (simplified normal-distribution approximation)
# ---------------------------------------------------------------------------


def z_score(breakeven: float, underlying_price: float, expected_move_value: float) -> float:
    """z = (Breakeven - Underlying Price) / Expected Move.

    This expresses the breakeven price as a number of standard
    deviations away from the current price, under the simplified
    assumption that price changes are normally distributed with a
    standard deviation equal to the expected move.
    """
    if expected_move_value == 0:
        raise ValueError(
            "Expected move is zero, so a z-score cannot be computed. "
            "Check that DTE and IV are both greater than zero."
        )
    return (breakeven - underlying_price) / expected_move_value


def probability_below_breakeven(z: float) -> float:
    """Approximate probability of finishing below breakeven = Normal CDF(z).

    EDUCATIONAL APPROXIMATION ONLY. This assumes prices are normally
    distributed around the current price with the expected move as
    the standard deviation. Real markets exhibit volatility skew/smile,
    fat tails, and other effects this does not capture. This is not
    the option chain's true implied probability of profit.
    """
    return normal_cdf(z)


# ---------------------------------------------------------------------------
# Payoff at expiration
# ---------------------------------------------------------------------------


def long_put_intrinsic_value(long_strike: float, expiration_price: float) -> float:
    """Long Put Intrinsic Value = max(Long Strike - Expiration Price, 0)."""
    return max(long_strike - expiration_price, 0.0)


def short_put_intrinsic_value(short_strike: float, expiration_price: float) -> float:
    """Short Put Intrinsic Value = max(Short Strike - Expiration Price, 0)."""
    return max(short_strike - expiration_price, 0.0)


def spread_intrinsic_value(long_intrinsic: float, short_intrinsic: float) -> float:
    """Spread Intrinsic Value = Long Put Intrinsic - Short Put Intrinsic.

    This is what the spread would be worth if it expired right now at
    the given price: the value we own (long put) minus the value we
    owe (short put, which we are obligated on as the seller).
    """
    return long_intrinsic - short_intrinsic


def payoff_pl_per_share(spread_intrinsic: float, debit_share: float) -> float:
    """P/L per share = Spread Intrinsic Value - Debit."""
    return spread_intrinsic - debit_share


def payoff_pl_per_contract(pl_share: float) -> float:
    """P/L per contract = P/L per share x 100."""
    return pl_share * CONTRACT_MULTIPLIER


def payoff_at_expiration(
    long_strike: float,
    short_strike: float,
    expiration_price: float,
    debit_share: float,
) -> dict:
    """Convenience wrapper chaining the payoff steps above for one price.

    Returned as a dict of every intermediate value (not just the final
    P/L) so callers -- including the API layer -- can display the full
    chain of reasoning rather than a single black-box number.
    """
    long_val = long_put_intrinsic_value(long_strike, expiration_price)
    short_val = short_put_intrinsic_value(short_strike, expiration_price)
    spread_val = spread_intrinsic_value(long_val, short_val)
    pl_share = payoff_pl_per_share(spread_val, debit_share)
    pl_contract = payoff_pl_per_contract(pl_share)
    return {
        "expiration_price": expiration_price,
        "long_put_value": long_val,
        "short_put_value": short_val,
        "spread_value": spread_val,
        "pl_per_share": pl_share,
        "pl_per_contract": pl_contract,
    }
