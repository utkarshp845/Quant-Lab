"""Phase 3: Monte Carlo simulation of the spread's expiration outcome.

Phase 2 (probability_distribution.py) computed an *exact* answer by
integrating the normal distribution's CDF over price buckets in closed
form. This module computes an *approximate* answer instead: it draws
many random expiration prices from that same normal model, runs each
one through the exact same payoff formulas used everywhere else in
this app, and summarizes the resulting sample.

Because both phases share the same underlying model (normal, mean =
current price, std dev = expected move), Monte Carlo's expected value
should converge to Phase 2's closed-form expected value as the number
of simulations grows -- see test_monte_carlo.py, which checks exactly
that. This is the "correctness check" role Monte Carlo plays for now;
its real payoff comes later, once a future phase introduces a
distribution that has no closed-form CDF to integrate and Monte Carlo
becomes the *only* way to get these numbers.

IMPORTANT: every number here is a property of 100,000 random draws
from a simplified model, not a fact about the real world. The API and
UI both label these as "simulated" / "model output" for that reason.
"""

import numpy as np

from . import bear_put_spread as calc

DEFAULT_NUM_SIMULATIONS = 100_000
SAMPLE_PATH_PREVIEW_COUNT = 10


def run_simulation(
    *,
    underlying_price: float,
    expected_move: float,
    long_strike: float,
    short_strike: float,
    debit_share: float,
    num_simulations: int = DEFAULT_NUM_SIMULATIONS,
    seed: int | None = None,
) -> dict:
    """Simulate `num_simulations` expiration prices and summarize the P/L.

    Args:
        seed: pass a fixed integer for reproducible results (used by
            the test suite); leave as None for a fresh random draw
            each call, which is what the UI's "Run Simulation" button
            does.
    """
    if expected_move <= 0:
        raise ValueError(
            "Expected move must be greater than zero to run a simulation. "
            "Check that DTE and IV are both greater than zero."
        )
    if num_simulations < 1:
        raise ValueError("Number of simulations must be at least 1.")

    rng = np.random.default_rng(seed)

    # The one random step in this whole app: draw simulated expiration
    # prices from Normal(current price, expected move) -- the exact
    # same model Phase 2 integrates in closed form.
    simulated_prices = rng.normal(loc=underlying_price, scale=expected_move, size=num_simulations)

    # Run every simulated price through the same intrinsic-value /
    # payoff formulas used by the payoff calculator and Phase 2 --
    # vectorized with numpy instead of a 100,000-iteration Python
    # loop, but it is the identical formula, just applied elementwise.
    long_value = np.maximum(long_strike - simulated_prices, 0.0)
    short_value = np.maximum(short_strike - simulated_prices, 0.0)
    spread_value = long_value - short_value
    pl_per_share = spread_value - debit_share
    pl_per_contract = pl_per_share * calc.CONTRACT_MULTIPLIER

    is_profit = pl_per_share > 0
    is_max_loss = simulated_prices >= long_strike
    is_max_profit = simulated_prices <= short_strike

    winners = pl_per_contract[pl_per_contract > 0]
    losers = pl_per_contract[pl_per_contract < 0]

    expected_value_per_contract = float(np.mean(pl_per_contract))
    debit_per_contract = debit_share * calc.CONTRACT_MULTIPLIER

    percentiles = np.percentile(pl_per_contract, [5, 25, 50, 75, 95])

    sample_paths = [
        {"index": i + 1, "simulated_price": float(simulated_prices[i]), "pl_per_contract": float(pl_per_contract[i])}
        for i in range(min(SAMPLE_PATH_PREVIEW_COUNT, num_simulations))
    ]

    return {
        "num_simulations": num_simulations,
        "probability_of_profit": float(np.mean(is_profit)),
        "probability_of_max_loss": float(np.mean(is_max_loss)),
        "probability_of_max_profit": float(np.mean(is_max_profit)),
        "expected_value_per_contract": expected_value_per_contract,
        "expected_return_pct": (
            expected_value_per_contract / debit_per_contract if debit_per_contract != 0 else 0.0
        ),
        "median_pl_per_contract": float(percentiles[2]),
        "percentile_5_pl_per_contract": float(percentiles[0]),
        "percentile_25_pl_per_contract": float(percentiles[1]),
        "percentile_75_pl_per_contract": float(percentiles[3]),
        "percentile_95_pl_per_contract": float(percentiles[4]),
        "expected_gain_per_contract": float(np.mean(winners)) if winners.size > 0 else 0.0,
        "expected_loss_per_contract": float(np.mean(losers)) if losers.size > 0 else 0.0,
        "sample_paths": sample_paths,
        "histogram": _build_histogram(simulated_prices, pl_per_contract, long_strike, short_strike, debit_share),
    }


def _build_histogram(
    simulated_prices: np.ndarray,
    pl_per_contract: np.ndarray,
    long_strike: float,
    short_strike: float,
    debit_share: float,
    num_bins: int = 40,
) -> list[dict]:
    """Bucket the simulated prices into bins for a frequency histogram.

    Mirrors the spirit of Phase 2's price buckets (bear_put_spread.py /
    payoff_scenarios.py) but reports *empirical* relative frequency
    (how many simulated draws landed in each bin) rather than the
    theoretical probability -- this is what lets the UI show the
    simulated distribution next to Phase 2's exact one.
    """
    counts, edges = np.histogram(simulated_prices, bins=num_bins)
    total = len(simulated_prices)

    bins = []
    for i in range(len(counts)):
        lo, hi = float(edges[i]), float(edges[i + 1])
        midpoint = (lo + hi) / 2.0
        long_value = max(long_strike - midpoint, 0.0)
        short_value = max(short_strike - midpoint, 0.0)
        pl_share = (long_value - short_value) - debit_share
        bins.append(
            {
                "price_low": lo,
                "price_high": hi,
                "frequency": float(counts[i] / total) if total > 0 else 0.0,
                "count": int(counts[i]),
                "pl_per_contract": pl_share * calc.CONTRACT_MULTIPLIER,
                "is_profit": pl_share > 0,
            }
        )
    return bins
