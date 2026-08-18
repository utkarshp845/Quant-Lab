"""Statistical Validation V1 -- app/statistical_validation/episodes.py
(non-overlapping episode grouping), baseline.py (unconditional-baseline
construction via the unmodified Backtesting v1 engine), resampling.py
(bootstrap confidence intervals, a permutation test, Cohen's d), and
engine.py (the orchestrator that assembles a StatisticalValidationReport,
app/models/statistical_validation.py).

Consumes Backtesting v1's already-persisted output; modifies nothing in
app/backtesting/, app/features/, or app/research/. See engine.py's
module docstring for the full design.
"""
