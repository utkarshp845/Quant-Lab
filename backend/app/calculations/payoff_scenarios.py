"""Builds the payoff table and payoff chart data for a bear put spread.

This module does not introduce any new financial math -- it only
decides *which* expiration prices to evaluate and calls the pure
functions in bear_put_spread.py for each one. Keeping this separate
from bear_put_spread.py keeps that file limited to the actual
formulas, and keeps this "which prices are interesting" logic
somewhere it can be read on its own.

Key teaching point encoded here: a bear put spread's expiration
payoff is PIECEWISE LINEAR with exactly two breakpoints (the two
strikes):

    price >= long_strike            -> flat at max loss
    short_strike <= price <= long_strike -> straight line
    price <= short_strike           -> flat at max profit

Because it is piecewise *linear*, the chart only needs the value at
the breakpoints plus two outer padding points -- there is no need to
densely sample thousands of prices to get an accurate curve.
"""

import math

from . import bear_put_spread as calc


def generate_payoff_table(
    *,
    underlying_price: float,
    long_strike: float,
    short_strike: float,
    breakeven: float,
    debit_share: float,
) -> list[dict]:
    """Generate a table of expiration-price scenarios.

    Always includes the key reference prices (both strikes, breakeven,
    current underlying price) plus an evenly spaced grid of surrounding
    prices, so the shape of the payoff is visible above, below, and
    between the strikes.
    """
    width = long_strike - short_strike
    step = max(1.0, round(width / 4))

    range_low = math.floor((short_strike - width) / step) * step
    range_high = math.ceil((long_strike + width) / step) * step

    prices: set[float] = set()
    price = range_low
    while price <= range_high + 1e-9:
        prices.add(round(price, 2))
        price += step

    key_points = {
        round(short_strike, 2): "Short Put Strike",
        round(long_strike, 2): "Long Put Strike",
        round(breakeven, 2): "Breakeven",
        round(underlying_price, 2): "Current Price",
    }
    prices.update(key_points.keys())

    scenarios = []
    for p in sorted(prices):
        result = calc.payoff_at_expiration(long_strike, short_strike, p, debit_share)
        scenarios.append(
            {
                **result,
                "is_profit": result["pl_per_share"] > 0,
                "label": key_points.get(round(p, 2)),
            }
        )
    return scenarios


def generate_payoff_chart_points(
    *,
    long_strike: float,
    short_strike: float,
    debit_share: float,
    padding: float,
) -> list[dict]:
    """Generate the minimal set of points needed to draw the payoff line.

    The payoff is piecewise linear with breakpoints at the two strikes,
    so four points (an outer point below the short strike, the short
    strike itself, the long strike itself, and an outer point above the
    long strike) are sufficient to draw the exact shape.
    """
    xs = [
        short_strike - padding,
        short_strike,
        long_strike,
        long_strike + padding,
    ]
    return [
        calc.payoff_at_expiration(long_strike, short_strike, x, debit_share) for x in xs
    ]
