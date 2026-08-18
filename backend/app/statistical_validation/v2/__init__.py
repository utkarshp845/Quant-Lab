"""Statistical Validation V2 -- corrects the dependence problem V1 left
unaddressed (see app/statistical_validation/engine.py's V1 report,
Limitations: "baseline's own ~4,381 observations overlap heavily...
this wasn't addressed in v1 per scope"). V1 treated the conditioned
side correctly (episode-level, non-overlapping) but the baseline side
naively (every eligible bar as an independent observation, despite
adjacent bars' forward-return windows sharing most of their own bars).

V2 does NOT modify V1 (app/statistical_validation/episodes.py,
baseline.py, resampling.py, engine.py, or app/models/
statistical_validation.py) -- it imports and reuses V1's episode
definition and unconditional-baseline construction UNCHANGED, and adds
two independent, clearly-labeled dependence-aware corrections for the
baseline side only:

  Method A (app/statistical_validation/v2/baseline.py): subsamples V1's
  own baseline down to non-overlapping forward-return windows -- once
  non-overlapping, V1's OWN, UNMODIFIED bootstrap/permutation functions
  apply directly (no new statistical machinery needed for this method).

  Method B (app/statistical_validation/v2/resampling.py): a moving
  block bootstrap over the FULL, overlapping baseline series, resampling
  contiguous blocks rather than individual points -- the standard
  technique for building a valid bootstrap distribution from serially
  dependent data without discarding most of the sample.

See app/statistical_validation/v2/engine.py for the full orchestration
and app/models/statistical_validation_v2.py for the report shape.
"""
