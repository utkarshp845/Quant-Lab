"""Builds a discretized probability distribution over expiration prices.

This is the Phase 2 "probability engine": instead of reporting a single
number ("44.5% probability of finishing below breakeven"), we build an
actual distribution -- a set of price buckets, each with a probability
mass and a P/L -- and combine them into an Expected Value.

MODEL: same simplifying assumption used everywhere else in this app
(see bear_put_spread.expected_move / z_score / probability_below_breakeven):
the underlying's price at expiration is treated as Normal(mean=current
price, std_dev=expected move). This is an educational simplification,
not a model of the option chain's real (risk-neutral) implied
distribution -- see the disclaimers surfaced with this data in the UI.

BUCKETING: prices are split into equal-width buckets spanning
+/- `num_std_devs` standard deviations around the current price. The
two outermost buckets are left OPEN-ENDED (probability integrates out
to -infinity / +infinity) so that the bucket probabilities always sum
to exactly 1.0 -- nothing is silently dropped off the tails.

Each bucket's probability is the exact area under the standard normal
curve between its two z-score edges:

    P(bucket) = NormalCDF(z_high) - NormalCDF(z_low)

This is the correct way to turn a continuous distribution into a
discrete one (integrating over the bucket), rather than sampling the
density at a single point, which is why we call `normal_cdf` twice per
bucket instead of computing a density.
"""

import math

from . import bear_put_spread as calc
from .stats import normal_cdf


def bucket_probability(z_low: float, z_high: float) -> float:
    """Probability mass between two z-scores under the standard normal curve."""
    return normal_cdf(z_high) - normal_cdf(z_low)


def build_probability_distribution(
    *,
    underlying_price: float,
    expected_move: float,
    long_strike: float,
    short_strike: float,
    debit_share: float,
    step: float,
    num_std_devs: float = 3.0,
) -> dict:
    """Build the full bucketed distribution and its Expected Value.

    Args:
        step: bucket width in dollars.
        num_std_devs: how many standard deviations out the finite part
            of the grid extends before the two tail buckets take over
            (3 standard deviations covers ~99.7% of a normal
            distribution, the familiar "68-95-99.7" rule).
    """
    if expected_move <= 0:
        raise ValueError(
            "Expected move must be greater than zero to build a probability "
            "distribution. Check that DTE and IV are both greater than zero."
        )
    if step <= 0:
        raise ValueError("Bucket step must be greater than zero.")

    n_steps = max(1, math.ceil((num_std_devs * expected_move) / step))
    edges = [underlying_price + i * step for i in range(-n_steps, n_steps + 1)]

    buckets = []
    total_probability = 0.0
    expected_value_share = 0.0

    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        is_first = i == 0
        is_last = i == len(edges) - 2

        z_low = -math.inf if is_first else (lo - underlying_price) / expected_move
        z_high = math.inf if is_last else (hi - underlying_price) / expected_move
        probability = bucket_probability(z_low, z_high)

        # The two open-ended tail buckets have no finite midpoint, so we
        # evaluate P/L at their one finite edge instead. Because this
        # spread's payoff is flat beyond each strike (see
        # payoff_scenarios.py), this is exact as long as that edge is
        # already past the relevant strike -- true for any reasonable
        # IV/DTE input, since the tail only starts after 3 standard
        # deviations of price movement.
        if is_first:
            representative_price = hi
        elif is_last:
            representative_price = lo
        else:
            representative_price = (lo + hi) / 2.0

        payoff = calc.payoff_at_expiration(long_strike, short_strike, representative_price, debit_share)

        if is_first:
            label = f"<= ${hi:.2f}"
        elif is_last:
            label = f">= ${lo:.2f}"
        else:
            label = f"${lo:.2f} - ${hi:.2f}"

        buckets.append(
            {
                "price_low": None if is_first else lo,
                "price_high": None if is_last else hi,
                "representative_price": representative_price,
                "probability": probability,
                "pl_per_share": payoff["pl_per_share"],
                "pl_per_contract": payoff["pl_per_contract"],
                "is_profit": payoff["pl_per_share"] > 0,
                "label": label,
            }
        )
        total_probability += probability
        expected_value_share += probability * payoff["pl_per_share"]

    return {
        "buckets": buckets,
        "expected_value_per_share": expected_value_share,
        "expected_value_per_contract": expected_value_share * calc.CONTRACT_MULTIPLIER,
        "total_probability": total_probability,
        "mean": underlying_price,
        "std_dev": expected_move,
    }
