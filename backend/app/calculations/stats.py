"""Statistical helper functions used by the options calculations.

Kept separate from bear_put_spread.py because this is generic math
(not specific to a bear put spread) and it is the one place we lean
on the Python standard library instead of writing the math from
scratch.
"""

import math


def normal_cdf(z: float) -> float:
    """Standard normal cumulative distribution function, CDF(z).

    Returns the probability that a standard normal random variable
    (mean 0, standard deviation 1) is less than or equal to ``z``.

    We use the identity:

        CDF(z) = 0.5 * (1 + erf(z / sqrt(2)))

    where ``erf`` is the Gauss error function. ``math.erf`` is part of
    the Python standard library, so this avoids adding a dependency
    like scipy just for one function, while still being a standard,
    well-known closed-form formula (not a hidden approximation table).
    """
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
