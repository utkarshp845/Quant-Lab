"""Post-hoc detectable-effect-size analysis for Statistical Validation
V2 -- "a simple post-hoc sensitivity/power analysis," per this
feature's own spec, not a general-purpose power-analysis library. NOT
a test of whether H0 is true: this only describes what magnitude of
TRUE effect a sample of this size could realistically have detected --
see app/statistical_validation/v2/engine.py's own report assembly for
where this is explicitly kept separate from the actual hypothesis-test
result (app/models/statistical_validation_v2.py::PowerAnalysisResultV2).

Uses the standard closed-form, normal-approximation formula for the
minimum detectable effect size (Cohen's d) of a two-sample mean-
difference test:

    d_min = (z_(1-alpha/2) + z_power) * sqrt(1/n1 + 1/n2)

No scipy dependency added for this -- consistent with this app's
existing judgment (app/calculations/stats.py's own docstring: "avoids
adding a dependency like scipy just for one function"). The two
z-quantiles this needs are fixed, well-known, textbook constants for
the conventional alpha=0.05 (two-sided) / power=0.80 or 0.90
combinations -- a small, explicit lookup rather than a general inverse-
normal-CDF routine this module has no other use for.
"""

# Standard normal quantiles (z-scores), tabulated to 6 decimal places --
# not derived here. z_(1 - alpha/2) for two-sided alpha; z_power for
# the target power (1 - beta).
_Z_ALPHA_TWO_SIDED: dict[float, float] = {0.05: 1.959964, 0.01: 2.575829}
_Z_POWER: dict[float, float] = {0.80: 0.841621, 0.90: 1.281552}


def minimum_detectable_effect_size(n1: int, n2: int, *, alpha: float = 0.05, power: float = 0.80) -> float:
    """The smallest population Cohen's d a two-sample mean-difference
    test with `n1`/`n2` independent observations per group could
    reliably detect at the given two-sided significance level and
    target power, under the standard normal approximation. Raises
    ValueError for an `alpha`/`power` combination this module does not
    carry a tabulated quantile for (rather than silently interpolating
    or approximating one) -- see _Z_ALPHA_TWO_SIDED/_Z_POWER above for
    the supported set.
    """
    if n1 < 1 or n2 < 1:
        raise ValueError(f"n1 and n2 must be positive, got n1={n1}, n2={n2}.")
    try:
        z_alpha = _Z_ALPHA_TWO_SIDED[alpha]
    except KeyError:
        raise ValueError(f"Unsupported alpha {alpha!r}. Supported: {sorted(_Z_ALPHA_TWO_SIDED)}") from None
    try:
        z_power = _Z_POWER[power]
    except KeyError:
        raise ValueError(f"Unsupported power {power!r}. Supported: {sorted(_Z_POWER)}") from None

    return (z_alpha + z_power) * ((1.0 / n1 + 1.0 / n2) ** 0.5)
