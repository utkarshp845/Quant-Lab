# Build Log

The chronological, detailed record of every change made to Pandey Quant
Lab — newest first. `README.md` describes **current** behavior only
(and stays intentionally short); this file is where the history and the
"why" behind each step lives.

**How to keep this updated:** every time a new version ships, prepend a
new entry at the top (right after this header) using the format below.
Pull the date and PR number from `git log --merges` / `gh pr view`
rather than guessing — this file is only useful if it stays accurate.

```
## vX.Y.Z — <short title> (YYYY-MM-DD, PR #N)

What shipped, in a paragraph or two: the problem it solved, the key
design decision, and what it explicitly did NOT do if that matters.

**Files:** the handful of files/directories that matter most.
**Tests:** what was added, and the full-suite pass count at merge.
```

---

## Milestone — First real OOS experiment (2026-08-18)

No code changed for this entry — it records an EVENT, not a shipped
version: the first real, end-to-end run of OOS Evaluation v1
(v0.1.31/v0.1.32) against real market data, through the actual HTTP
API, on a hypothesis defined and frozen entirely from development data
before any holdout contact. See `EXPERIMENTS.md` for the full record
(hypothesis, exact conditions/thresholds, development statistics
including Statistical Validation V1/V2, frozen provenance, OOS
results, comparison table, verdict, and integrity check) — kept as its
own file, not folded into this changelog, because it is a scientific
log entry, not a software change, and because
`backend/data/historical_bars.db` (where the real experiment/
partition/evaluation rows actually live) is gitignored local data, so
this is the only durable, version-controlled record of what ran.

In brief: real TSLA 5-minute bars were fetched from Alpaca (4,387
bars, 2026-06-01 through 2026-08-14 — no synthetic data), split into a
development period (June 1 – July 31) and a holdout period (August 1 –
14) via a real `OOSPartition`. A single hypothesis
("short-term decline + high relative volume predicts continued
weakness") was defined, its thresholds chosen once from domain
convention before inspecting this dataset's own distribution, and run
through Research/Backtesting/Statistical Validation V1+V2 on
development data only — the properly-adjusted (episode-level,
dependence-corrected) result was statistically negligible (Cohen's d ≈
−0.01, p ≈ 0.93–1.00). The hypothesis was frozen as-is rather than
modified, linked to the partition, and evaluated exactly once against
holdout via `POST /research/experiments/{id}/oos-evaluate` with no
request body — 7 raw signals (4 independent episodes), mean return
−0.48%. Verdict: **INCONCLUSIVE** (development evidence was already
null; the holdout sample is far too small for an independent
conclusion) — recommendation: collect more holdout data before
revisiting. Every integrity check (hash continuity, snapshot
immutability, append-only evaluation, zero writes to development/
holdout bars or to `historical_features` for the holdout range, no
post-freeze mutation, holdout touched exactly once) passed.

**Files:** `EXPERIMENTS.md` (new).

## v0.1.32 — OOS Evaluation V1 Audit (2026-08-18)

An adversarial validation audit of v0.1.31 (holdout access tracing,
feature warm-up line-by-line review, boundary-condition construction,
backtest-semantics/provenance/repeatability/failure-safety proofs, and
one additional real-data validation) -- no new features, no hypothesis
optimization.

Found and fixed ONE genuine defect: `app/oos_evaluation/warmup.py::
warmup_range()`'s read range was bounded only by `holdout_start`, not
by the partition's own `development_end`. A partition's validator
(app/models/oos_partition.py) only requires `development_end <
holdout_start`, not adjacency -- a partition MAY declare a gap between
the two (e.g. to deliberately wall off a known data-quality window).
Confirmed by construction: a partition with a 22-day gap, real bars
seeded ONLY in the gap (none near development_end), still fully warmed
up a 50-bar SMA50 feature at the first holdout bar -- proving the gap
bars, not the partition's own declared development data, were what
warmed it up. NOT a holdout leak (gap bars are still strictly
pre-holdout -- no future information was ever used), but a genuine
violation of this feature's own "use the minimum required DEVELOPMENT
context" instruction. Fixed by clamping `end = min(holdout_start - 1
microsecond, development_end)` (and counting the calendar buffer
backward from that `end`, not unconditionally from `holdout_start`) --
a no-op for every adjacent partition (development_end already set to
`holdout_start - 1 microsecond`), which is every example partition
elsewhere in this codebase's own tests and README, so no observable
behavior change for the common case. Two stale docstring claims
("nothing calls get_holdout_bars() with the flag set to True", "nothing
in app/research/ calls into this module yet") were also corrected --
both were literally true when OOS / Holdout Partition Framework v1
shipped and became false once Experiment Freeze & Provenance v1 /
OOS Evaluation v1 actually wired those seams up; left uncorrected they
would mislead a future reader auditing the same leakage boundary.

Every other audited property held up under adversarial testing: sole
holdout access path (`get_holdout_bars(..., confirm_oos_validation_use=True)`
appears exactly once in the pipeline, traced and asserted), no
fetch-then-slice pattern (every bar read is an explicitly-bounded
`get_bars_in_range()` call, never `get_bars()`), zero writes anywhere
in `evaluate_oos()` (proven by comparing every table's row count before
and after a full run), boundary conditions (a signal on the final
holdout bar produces nothing, insufficient future bars omit rather than
fabricate an outcome, an outcome exactly at `holdout_end` is included,
none ever exceeds it, the bar exactly at `holdout_start` is holdout-
only, never double-counted as warm-up), immunity to a tampered live
`experiments` row (conditions/symbol/timeframe/feature_contract_version/
outcome threshold+horizon mutated via raw SQL after freezing --
evaluation results proven unaffected, since it reads the immutable
snapshot exclusively), provenance independence (mutating the live row
after an evaluation was already persisted cannot change that
evaluation's own stored fields), deterministic re-runs (identical hash/
signal timestamps/outcomes/aggregate results, a new record each time,
the prior one untouched), and failure safety (a forced pipeline failure
-- both at the bare-engine level and through the real HTTP route --
persists a FAILED result with full provenance, never advances the
lifecycle past FROZEN, never leaves partial signals, and a subsequent
successful evaluation still works and correctly advances to
OOS_EVALUATED afterward).

One additional real-data validation was run against a COPY of this
lab's actual `backend/data/historical_bars.db` (10 real Alpaca TSLA
daily bars, 2026-08-03 through 2026-08-14 -- the only pre-existing data
in that file; it had no `experiments`/`oos_partitions` tables at all,
so there was no pre-existing hypothesis to worry about reusing): a
brand-new, deliberately trivial condition
(`volume.volume_acceleration > 0`, never iterated on, frozen
immediately with no prior DRAFT-mode run against this data at all) was
partitioned (6 development / 4 holdout real daily bars), frozen, and
evaluated -- 2 real signals found (Aug 11, Aug 12), forward returns
+1.49%/+4.47%, status completed, lifecycle correctly advanced to
OOS_EVALUATED. The real `backend/data/historical_bars.db` file itself
was never touched (verified: still exactly 10 bars, still only the
`historical_bars` table, after the audit).

**Files:** `app/oos_evaluation/warmup.py` (defect fix: `warmup_range()`
gained a required `development_end` parameter and now clamps `end` to
it), `app/oos_evaluation/engine.py` (updated call site + docstring),
`app/oos/access.py` / `app/oos/partition.py` (stale-docstring
corrections only, zero behavior change).
**Tests:** `tests/test_oos_evaluation_audit.py` (16 new tests) +
2 new regression tests in `tests/test_oos_evaluation_warmup.py`
(the gap scenario, and a no-op confirmation for the adjacent case).
Full suite: 1072 passed.

## v0.1.31 — OOS Evaluation v1 (2026-08-18)

The actual OOS-evaluation operation v0.1.29 (OOS / Holdout Partition
Framework) and v0.1.30 (Experiment Freeze & Provenance) were both built
toward: given a FROZEN (or already OOS_EVALUATED, for a re-run)
experiment linked to an OOS partition, runs its frozen hypothesis
against the partition's HOLDOUT data and persists an immutable,
append-only result. Orchestrates the existing Feature Engine, Research
condition evaluation, and Backtesting v1 engine -- all three reused
completely UNMODIFIED, never a second implementation.

`app/oos_evaluation/engine.py::evaluate_oos()` is the pipeline: load
the live Experiment (lifecycle_state only -- every research-defining
fact comes from the immutable ExperimentFreezeSnapshot, never the
mutable row, per v0.1.30's own requirement) -> re-validate partition
linkage against the SNAPSHOT's fields (new
app.research.lifecycle.validate_snapshot_partition_linkage(), factored
out of v0.1.30's validate_partition_linkage() via a shared helper,
zero behavior change to the existing function) -> read a bounded
DEVELOPMENT-side warm-up range (`app/oos_evaluation/warmup.py`, pure --
reuses PUBLISHED window-size constants directly from
app/features/{volatility,price_position,price,timeframes}.py, never
re-derives them) -> read holdout bars via
`app.oos.access.get_holdout_bars(..., confirm_oos_validation_use=True)`
-- THE SOLE holdout access path, no "fetch everything, slice later"
step anywhere -- -> compute features
(app.features.engine.compute_features(), unmodified) over warm-up +
holdout bars together, then discard every record computed at a
warm-up-bar timestamp before anything downstream sees it -> run
app.backtesting.engine.run_backtest() (unmodified) with `bars` = 
holdout bars ONLY (never warm-up bars) and a single window converted
from the frozen Outcome's own horizon_minutes (never a different
horizon, never more than one window) -> OOSEvaluationResult +
OOSSignal rows (the latter reusing
app.models.backtesting.BacktestWindowOutcome/BacktestResults verbatim,
relabeling BacktestSignal into OOSSignal with zero new math).

A precondition failure (DRAFT/ARCHIVED experiment, no/incomplete
provenance, no/nonexistent/incompatible linked partition) raises before
any holdout data is touched and persists nothing. A failure DURING the
pipeline itself is instead caught and persisted as a FAILED
OOSEvaluationResult (mirroring Research's/Backtesting's own "a run
failing is a normal, persisted status, not a 500" convention) --
either way, FROZEN -> OOS_EVALUATED only advances on a COMPLETED
result, and only when the experiment was still exactly FROZEN going
in (a re-run of an already-OOS_EVALUATED experiment produces a new
evaluation row with identical analytics but leaves the lifecycle
untouched, since OOS_EVALUATED -> OOS_EVALUATED is not a valid
transition).

New append-only `oos_evaluations`/`oos_evaluation_signals` tables --
`oos_evaluations.id` is a random id (like experiments/backtests, unlike
oos_partitions' deterministic hash) specifically so re-running the same
frozen experiment creates a SECOND row, never replacing the first (no
UPDATE/REPLACE/DELETE exists anywhere in
app/storage/oos_evaluation_repository.py for either table).

New HTTP surface, request body completely ignored on the POST route
(FastAPI never even parses one into anything the handler reads) --
every research-defining fact comes from the frozen snapshot and linked
partition, never the caller, verified directly by a test that posts an
adversarial body (different symbol/conditions/horizon/etc.) and
confirms zero effect.

Real-data validation: `scripts/run_oos_evaluation.py --demo` seeds a
deterministic, seeded random-walk synthetic dataset (no live provider
access in this sandboxed environment), freezes ONE un-optimized
condition (`price.return_15m <= -0.5%` -> 30-minute forward return,
chosen once, never tuned against the data), and runs the full pipeline
against it -- executed for real: 17,280 development bars + 1,441
holdout bars seeded, 3 signals found in the 5-day holdout window, mean
forward return -0.20%, evaluation persisted, lifecycle correctly
advanced to OOS_EVALUATED.

**Files:** `app/models/oos_evaluation.py` (new), `app/oos_evaluation/`
(new package: `warmup.py`, `engine.py`), `app/research/lifecycle.py`
(additive: validate_snapshot_partition_linkage(), factored via a
shared `_validate_linkage()` helper -- validate_partition_linkage()'s
own behavior unchanged), `app/storage/db.py` (oos_evaluations/
oos_evaluation_signals tables), `app/storage/
oos_evaluation_repository.py` (new), `app/api/oos_evaluation.py` (new),
`app/main.py`, `scripts/run_oos_evaluation.py` (new).
**Tests:** `tests/test_oos_evaluation_warmup.py`,
`tests/test_oos_evaluation_engine.py`,
`tests/test_oos_evaluation_repository.py`,
`tests/test_oos_evaluation_api.py` (39 new tests) -- DRAFT/ARCHIVED
rejection, missing/incomplete/incompatible partition-linkage rejection,
the holdout-authorization gate re-asserted directly, a
development-only condition never producing a signal despite the
Feature Engine's own warm-up computation genuinely satisfying it there,
every signal/entry/outcome falling strictly within holdout, an early
signal's values provably unaffected by a change far later in holdout,
deterministic re-runs, append-only persistence, and the full HTTP flow
including the adversarial-request-body-is-ignored proof. Full suite:
1054 passed.

## v0.1.30 — Experiment Freeze & Provenance v1 (2026-08-18)

Makes a Research Experiment (v0.1.20) explicitly versioned and capable
of being frozen before OOS evaluation: a hypothesis whose definition
must not change after the freeze point. Adds a SECOND, independent
lifecycle axis to `Experiment` -- `lifecycle_state`
(DRAFT/FROZEN/OOS_EVALUATED/ARCHIVED), orthogonal to the existing
`status` (draft/running/completed/failed, which still tracks whether a
development-side RUN has executed -- unchanged, unaffected). State
machine (`app/research/lifecycle.py::validate_transition()`):
DRAFT->FROZEN, FROZEN->OOS_EVALUATED, FROZEN->ARCHIVED,
OOS_EVALUATED->ARCHIVED; every other transition rejected
(InvalidLifecycleTransitionError, 409 over HTTP). FROZEN->OOS_EVALUATED
is infrastructure only -- `research_repository.mark_oos_evaluated()`
exists and is directly tested, but no route calls it, since the actual
OOS-evaluation operation is out of this feature's explicit scope.

Freezing (`POST /research/experiments/{id}/freeze`) computes a
deterministic `hypothesis_hash` (SHA-256 of a canonical JSON encoding
of exactly symbol/timeframe/provider/development date range/feature
contract version/conditions (order-independent)/outcome -- excludes
id/created_at/name/hypothesis-text/oos_partition_id, none of which
affect research meaning) and persists an immutable
`ExperimentFreezeSnapshot` -- a VALUE copy, not a live reference, in a
new `experiment_freeze_snapshots` table (PRIMARY KEY experiment_id, one
row ever, since freezing is one-way) -- in the same operation as the
lifecycle-state write. No mutation endpoint exists for any
research-defining field, frozen or not (there never was one); the one
new pre-freeze-mutable field this adds, `oos_partition_id`, is rejected
with a 409 the instant lifecycle_state leaves DRAFT, enforced at both
the API route and independently in
`research_repository.set_oos_partition()`'s own WHERE clause.

OOS partition linkage reuses `app.oos.partition.classify_range()`
UNMODIFIED (app/oos/ itself is untouched) -- a DRAFT experiment may
associate a partition (`POST .../oos-partition`), validated for
symbol/timeframe/provider compatibility and that the experiment's
ENTIRE date range falls inside the partition's development window
(which, by classify_range()'s own construction, also rejects any range
touching the holdout window at all); freezing re-validates this
unconditionally regardless of what association-time already checked.

`app/models/research.py::Experiment` gained
lifecycle_state/oos_partition_id/hypothesis_hash/frozen_at/archived_at,
all additive with safe defaults (lifecycle_state='draft') -- every
existing DRAFT-experiment behavior, and every existing test, is
unaffected. The `experiments` table's legacy pre-v0.1.24 rebuild
migration (`_drop_legacy_not_null_columns()`) was extended to carry
these five new columns through its CREATE-TEMP-TABLE-and-copy step too
(a real gap the regression test `TestNewWritesAgainstAMigratedDatabase`
would otherwise have silently missed, since it predates these columns
existing at all) -- verified against that same authentic pre-v0.1.24
fixture.

**Files:** `app/models/research.py` (ExperimentLifecycleState +
Experiment's 5 new fields), `app/models/experiment_freeze.py` (new:
ExperimentFreezeSnapshot, ExperimentProvenance, OOSPartitionLinkRequest),
`app/research/lifecycle.py` (new: transitions, hash, partition linkage,
snapshot construction), `app/storage/db.py` (experiments columns +
experiment_freeze_snapshots table + migrations), `app/storage/
research_repository.py` (set_oos_partition/freeze_experiment/
mark_oos_evaluated/mark_archived), `app/storage/
experiment_freeze_repository.py` (new), `app/api/experiment_freeze.py`
(new), `app/main.py`.
**Tests:** `tests/test_experiment_lifecycle.py`,
`tests/test_experiment_freeze_repository.py`,
`tests/test_experiment_freeze_api.py` (72 new tests) -- every
valid/invalid transition, deterministic/ordering-independent hash and
its exclusions, hash sensitivity to every research-defining field,
partition-linkage compatibility/containment/holdout-leakage rejection,
immutable snapshot persistence surviving the live row reaching
ARCHIVED, provenance retrieval, and the full HTTP flow (including 409s
for invalid transitions and post-freeze mutation attempts). Full suite:
1015 passed.

## v0.1.29 — OOS / Holdout Partition Framework v1 (2026-08-18)

The first step toward reproducible out-of-sample validation: a way to
explicitly split an existing symbol/timeframe/provider's already-stored
bars into a development window and a later, non-overlapping holdout
window, plus a real technical boundary against accidentally reading the
holdout side while iterating on a hypothesis. Deliberately scoped to
partitioning and provenance only -- no OOS statistical test, optimizer,
ML, strategy construction, or paper trading reads a holdout window
through anything shipped here; Research, Feature Engine, Backtesting,
and Statistical Validation are all left completely unmodified.

`OOSPartition` (`app/models/oos_partition.py`) holds
symbol/timeframe/provider, the four boundary timestamps, `created_at`,
and a `id` that is a deterministic SHA-256 hash of every identity field
(not a random uuid4, unlike Experiment/Backtest -- creating "the same
partition" twice must yield the same id) -- `development_end` strictly
before `holdout_start` is enforced by a pydantic validator both on the
create request and on the model itself, so an overlapping, touching, or
inverted range can never even be constructed. `app/oos/partition.py`
adds the pure classify_range()/require_development_range() logic (the
seam a future Research/Backtesting/Statistical-Validation integration
would call through -- not wired in yet, since this task's scope
explicitly excludes modifying those engines). `app/oos/access.py` is
the actual consumption boundary: get_development_bars() is
unrestricted; get_holdout_bars() requires
`confirm_oos_validation_use=True` explicitly, raising HoldoutAccessError
(403 over HTTP) otherwise -- a deliberateness guard against the
accidental case, explicitly NOT a guarantee against a caller who sets
that flag on purpose outside a real future OOS-validation operation
(documented, not pretended away). New `oos_partitions` SQLite table
(`app/storage/db.py`), repository (`app/storage/
oos_partition_repository.py`, idempotent save via INSERT OR IGNORE on
the deterministic id), and HTTP routes (`app/api/oos_partitions.py`) --
no bars are ever duplicated onto the new table, only date-range
references into the existing `historical_bars` table, read via a new,
purely additive `historical_bar_repository.get_bars_in_range()`
(timestamp-bounded, alongside the existing, unmodified date-bounded
`get_bars()`).

**Files:** `app/models/oos_partition.py`, `app/oos/` (new package:
`partition.py`, `access.py`), `app/storage/oos_partition_repository.py`,
`app/storage/db.py`, `app/storage/historical_bar_repository.py`
(additive `get_bars_in_range()` only), `app/api/oos_partitions.py`,
`app/main.py`.
**Tests:** `tests/test_oos_partition_model.py`,
`tests/test_oos_partition_logic.py`,
`tests/test_oos_partition_repository.py`,
`tests/test_oos_partitions_api.py` (41 new tests) -- valid partition
construction, overlapping/inverted/touching ranges rejected, boundary
timestamps, deterministic identity, metadata persistence, idempotent
re-save, range classification, the development-only guard, and the
holdout confirmation gate at both the function and HTTP layer. Full
suite: 943 passed.

## v0.1.28 — Statistical Validation v2 (2026-08-18)

Corrects the one weakness V1's own report flagged against itself: V1's
unconditional baseline treated ~4,381 heavily overlapping per-bar
forward-return windows as independent observations, while the
conditioned side correctly used 65 non-overlapping episodes. New
`app/statistical_validation/v2/` -- purely additive: does not modify
V1 (`app/statistical_validation/{episodes,baseline,resampling,
engine}.py`, `app/models/statistical_validation.py`), the Feature
Engine, Research, or Backtesting engines, and reuses V1's episode
definition and unconditional-baseline construction completely
unchanged.

Two independent, clearly-labeled dependence corrections for the
baseline side (conditioned side keeps V1's episode-level treatment
exactly, per this version's own explicit requirement):

- **Method A -- non-overlapping windows** (`app/statistical_validation/
  v2/baseline.py`): greedily subsamples V1's own baseline to entries
  whose forward-return windows never overlap (handles real gaps in the
  underlying data via timestamp arithmetic, not an assumption of dense
  spacing). Once non-overlapping, V1's OWN, unmodified bootstrap/
  permutation functions (`app.statistical_validation.resampling`)
  apply directly -- no new statistical machinery for this method.
- **Method B -- moving block bootstrap** (`app/statistical_validation/
  v2/resampling.py`): resamples the FULL, chronologically-ordered,
  overlapping baseline series in contiguous blocks (length = 4x the
  horizon, a fixed, documented rule -- long enough to span more than
  one mechanically-overlapping cluster, short enough to leave many
  valid start positions in a multi-thousand-observation series) rather
  than individual points -- the standard technique (Kunsch 1989;
  Politis & Romano) for a valid bootstrap distribution from serially
  dependent data. Its hypothesis test uses the "H0-centered bootstrap
  test" construction (Hall & Wilson 1991): both samples are shifted to
  share one common mean before resampling, since naively permuting
  individual points in an autocorrelated series -- what a plain
  permutation test does -- would destroy real autocorrelation and
  produce an invalidly narrow, falsely-confident null distribution.

`app/statistical_validation/v2/power.py` adds a simple, closed-form
post-hoc minimum-detectable-effect-size calculation (no scipy
dependency -- two fixed, tabulated normal quantiles, consistent with
this app's existing judgment on when a dependency is worth adding) --
explicitly a statement about what this sample size COULD have
detected, never evidence that H0 is true.

Run for real against the same TSLA experiment (`return_15m <= -0.5%
AND relative_volume > 1.5x`, 65 episodes, 5-bar primary horizon):
Method A gives p=0.2526 (n_baseline=946, non-overlapping), Method B
gives p=0.4140 (n_baseline=4,381, block-resampled, block_length=20) --
both agree the effect does not clearly stand out from baseline once
dependence is handled correctly on both sides (`conclusion_changes_
materially: False`), pushing V1's already-cautious p=0.2612 finding
further toward "not distinguishable from noise," not away from it.
Power analysis: minimum detectable Cohen's d at 80% power is ~0.36;
the observed |d|=0.15 is below that threshold, so the null result is
unsurprising on power grounds alone (recorded as a sensitivity
statement, not interpreted as confirming no effect exists).

**Files:** `app/models/statistical_validation_v2.py`,
`app/statistical_validation/v2/{baseline,resampling,power,engine}.py`,
`scripts/run_statistical_validation_v2.py`.
**Tests:** `tests/test_statistical_validation_v2_*.py` (46 new tests --
non-overlapping selection incl. real gaps and out-of-order input,
moving-block-bootstrap determinism plus a hand-verified periodic-series
exact-mean case, H0-shift p-value invariance to a common additive
shift, the power formula against a hand-computed value, and a full
synthetic Feature-Engine-to-Research-to-Backtesting-to-Statistical-
Validation-V2 pipeline) -- 902 passing at merge (was 856; zero existing
files modified, confirmed via `git diff --stat` showing only new
files).

## v0.1.27 — Statistical Validation v1 (2026-08-18)

The layer after Backtesting v1: does an already-run backtest's
conditioned population actually look different from TSLA's own
unconditional behavior, by more than random variation would explain?
New `app/statistical_validation/` -- modifies nothing in
`app/backtesting/`, `app/features/`, or `app/research/`, and adds no
database table; every report is recomputed on demand from the same
bars/features/signals a real backtest already used.

The central correction this version makes: a research condition that
stays true for several consecutive bars produces one raw signal PER
BAR, not one per onset (confirmed identical to Research v1's own
engine behavior during the Backtesting v1 audit) -- those signals are
correlated, not independent draws. `app/statistical_validation/
episodes.py` formalizes the non-overlapping "episode" rule used
informally in Baseline Analysis V1 (a signal joins the previous episode
only if it is exactly one bar-interval later; each episode's first
signal is its representative), and every confidence interval, p-value,
and effect size in this version uses that episode-level sample --
while still reporting the raw signal count alongside it, never hiding
it.

`app/statistical_validation/baseline.py` builds the unconditional
baseline by calling the real, UNMODIFIED `run_backtest()` with a
trivial always-true control condition (`volume.volume >= 0`) rather
than re-deriving forward-return math a second time -- the baseline
gets the identical next-bar-open entry rule, window definitions, and
insufficient-future-data exclusions a real backtest already used.
`app/statistical_validation/resampling.py` (numpy-vectorized, always
seeded via a caller-supplied `numpy.random.Generator` -- same seed and
data reproduce an identical report every time, extending this app's
existing "deterministic and reproducible" rule to randomized inference)
provides a percentile bootstrap CI for the mean-return and win-rate
differences at every horizon, and, at one designated PRIMARY horizon
(5 bars by default, treated as the single pre-specified hypothesis --
15/30/60 are always labeled exploratory, never silently promoted), a
two-sided permutation test and Cohen's d -- computed on the raw signal
population too, side by side, so the report shows exactly how much the
inference changes once clustering is corrected for. The episode-level
result is always the authoritative one.

Run for real against the existing TSLA experiment (`return_15m <=
-0.5% AND relative_volume > 1.5x`, 124 raw signals / 65 episodes): the
episode-level 5-bar permutation test returned p=0.2612 (not small by
any conventional threshold) and Cohen's d=-0.14 ("negligible") -- the
5-bar effect that looked most coherent in Baseline Analysis V1's raw
view does not clearly survive once clustering is corrected for. A
sobering, honest result -- reported as such, not adjusted or re-tested
until it looked better.

**Files:** `app/models/statistical_validation.py`,
`app/statistical_validation/{episodes,baseline,resampling,engine}.py`,
`scripts/run_statistical_validation.py`.
**Tests:** `tests/test_statistical_validation_*.py` (43 new tests --
episode-grouping rule in isolation, bootstrap/permutation determinism
and structural correctness, hand-verified Cohen's d, and a full
synthetic Feature-Engine-to-Research-to-Backtesting-to-Statistical-
Validation pipeline including stale-data detection) -- 856 passing at
merge (was 813; zero existing files modified).

## v0.1.26 — USER_GUIDE.md + Backtesting v1 audit (2026-08-18)

A validation audit of the v0.1.25 Backtesting v1 implementation against
data-integrity, look-ahead, feature/research-consistency, signal-
correctness, outcome-calculation, result-integrity, testing, and
architecture criteria — no code changes were needed (no FAILURES
found); two non-critical, pre-existing-and-inherited-from-Research-v1
behaviors were confirmed and documented rather than "fixed": (1)
persistent conditions produce one signal per bar they hold true, not
one per transition, so consecutive/overlapping signals are expected,
not a bug; (2) forward outcome windows are bar-count, not
calendar-time, so a window can silently span a session boundary.

Followed by a real, end-to-end run against live Alpaca data (not
synthetic fixtures): 4,387 real 5-minute TSLA bars fetched and saved
(0 rejected, 87 flagged), features computed, a grounded hypothesis
picked by querying the actual persisted feature distribution first
(not an arbitrary threshold), run through both Research v1 (124
events) and Backtesting v1 (windows 5/15/30/60, real aggregate stats
per window, including a window with fewer measurable signals than the
others — confirming the "insufficient future data is simply absent,
never fabricated" guarantee on real data). Both audit caveats above
were directly observed in the real signal data (two signals five
minutes apart, and a 60-bar window's outcome landing on the next
calendar day).

Added `USER_GUIDE.md` — a new, third top-level doc (alongside README's
"how it's built" and this file's "what changed, when") for "how do I
actually do X": fetching/saving historical data, computing features,
creating and running a Research experiment, and — the most detailed
section, since Backtesting v1 has no frontend UI yet — creating and
running a Backtest and reading its results correctly. Every example in
it is real output from the run above, not invented numbers.

**Files:** `USER_GUIDE.md` (new), `README.md` (pointer to it),
`.claude/launch.json` (fixed to point at this worktree's own
`backend/venv`/`.env` instead of the main checkout's — was silently
broken for any worktree).

## v0.1.25 — Backtesting v1 (2026-08-18)

An event-based historical backtester layered on top of Research v1 and
Feature Engine v1, without duplicating either: a `Backtest` selects an
existing `Experiment` by id — its conditions (evaluated via the exact
same `app/research/conditions.py::evaluate_feature_conditions()`
Research itself uses, imported, not reimplemented) and already-persisted
`FeatureRecord`s are what get walked, never redefined or recomputed.
Answers exactly one question: "when this research condition occurred
historically, what happened afterward?"

The engine (`app/backtesting/engine.py::run_backtest()`) walks bars
strictly oldest-first. A condition true at bar `t` (using bar `t`'s own
already-computed feature values only) generates a signal but is never
acted on at bar `t`'s own close — entry happens at bar `t+1`'s **open**,
the first price genuinely available once the signal is fully known. A
condition true on the dataset's last bar produces no signal at all (no
next bar to enter at). For each of several configurable forward windows
— bar counts, not minutes, since a Backtest already runs against a fixed
timeframe and needs no unit conversion — defaulting to **5/15/30/60
bars**, the engine computes forward return (close-to-close from entry),
MFE (best paper gain from any bar's high), and MAE (worst paper drawdown
from any bar's low); a window whose outcome bar falls outside the
dataset is simply absent from that signal's outcomes, never fabricated,
and a signal with zero measurable windows is not persisted at all.

Every individual signal is persisted (`backtest_signals` — signal/entry
timestamps, entry price, the feature values that fired, one outcome per
measurable window), not just the aggregate (`GET
/backtests/{id}/signals`) — `BacktestResults` aggregates per window
(signal count, win rate — a win is `forward_return > 0` — mean/median/
std-dev/best/worst return, mean MFE/MAE), `None` rather than a fabricated
`0.0` for a window with too few or zero measurable signals.
`feature_contract_version` is captured from the referenced Experiment at
Backtest-creation time, the same reproducibility guarantee Experiment
itself already makes; `POST /backtests/{id}/run` deletes and re-inserts
signals on every run (re-running against an unchanged dataset always
produces identical results, same convention as Research v1's own
`replace_events()`).

Deliberately does NOT implement (same discipline as Research v1's own
scope statement): position sizing, a held book, or capital tracking over
time, portfolio construction, parameter optimization, ML, live/paper
trading, or advanced execution simulation (slippage, partial fills,
spread cost) — Backtesting v1 measures what happened after a signal, it
does not simulate an account.

**Files:** `app/models/backtesting.py`, `app/backtesting/engine.py`,
`app/backtesting/aggregation.py`, `app/storage/backtest_repository.py`,
`app/api/backtesting.py`, `app/storage/db.py` (new `backtests`/
`backtest_signals` tables), `app/main.py` (router mount).
**Tests:** `tests/test_backtest_engine.py`, `test_backtest_aggregation.py`,
`test_backtest_models.py`, `test_backtest_repository.py`,
`test_backtest_api.py` (77 new tests — hand-verified forward-return/MFE/
MAE arithmetic, next-bar-open entry, explicit no-look-ahead proofs
(perturbing/truncating future bars never changes an earlier signal;
last-bar condition produces no signal), chronological ordering,
per-window aggregation incl. zero/one-signal None handling,
feature-contract-version reproducibility, persistence round-trips, and
the full create → run → inspect HTTP flow) — 813 passing at merge (was
736; +1 pre-existing pipeline test updated for the two new tables).

## v0.1.24 — Feature ↔ Research integration (2026-08-18)

Made the Feature Engine's own vocabulary the single source of truth
for Research conditions, closing the gap where Research hardcoded a
tiny, separate metric language ("{N}m_return" only, parsed by regex)
instead of referencing the 31 features the Feature Engine already
computes and persists. New `app/features/vocabulary.py` exposes every
leaf `FeatureRecord` field (not an arbitrary 25 — the real count is 31:
4 PRICE + 3 VOLUME + 4 VOLATILITY + 16 MARKET CONTEXT + 4 PRICE
POSITION) as a `FeatureDefinition` (stable `feature_id` in
`{category}.{field}` form, name, type, description, supported
operators, contract version) via a new `GET /features/vocabulary`
route. `Condition` (metric/operator/threshold, one per experiment)
became `FeatureCondition` (feature_id/operator/value(/value_max), a
LIST per experiment, AND-combined) — `FeatureConditionOperator` adds
`between` to the numeric set and restricts a boolean feature to `=`
only (no boolean feature exists yet; the type system is ready for one).
The condition builder's feature and operator dropdowns are now
populated entirely from the backend vocabulary — the frontend hardcodes
none of it, and the previously-permanently-disabled "+ Add condition"
button is real now (AND only, per spec — no OR/nesting).

The engine (`run_experiment()`) now evaluates conditions against
already-computed, already-persisted `FeatureRecord`s (fetched via
`feature_repository.get_features()`, matched to bars by exact
timestamp) instead of recomputing a trailing return directly off bars
— "Do NOT recalculate features inside Research" is now true in code,
not just policy. `Experiment.feature_contract_version` (captured once
at creation from `FEATURE_CONTRACT_VERSION`) is requirement 6's
reproducibility guarantee: a run only evaluates against FeatureRecords
whose own contract version matches, so a future feature-formula change
can never silently alter what an old experiment measures — it just
finds no data until re-run against freshly computed features.

A real end-to-end run against this worktree's own pre-existing database
(two real experiments, created before this version) surfaced a genuine
bug pure unit tests never would have: the additive-only ALTER TABLE
migration pattern this codebase already used for `historical_bars`
doesn't work when the OLD column (`experiments.condition_json`,
`experiment_events.condition_value`) is itself `NOT NULL` and current
code no longer populates it — every new INSERT violated that
constraint. Fixed with a real table rebuild
(`db.py::_drop_legacy_not_null_columns()`, SQLite's own documented
procedure for a constraint change) that runs once per database, after
a separate data migration losslessly converts every pre-existing
experiment's old `"{N}m_return"` condition into the equivalent
`price.return_{N}m` FeatureCondition (a real, deterministic mapping,
not a guess — confirmed against this worktree's real experiments).

**Files:** `app/features/vocabulary.py` (new), `app/models/research.py`
(`FeatureCondition`/`FeatureConditionOperator`, `Experiment.conditions`/
`feature_contract_version`), `app/research/conditions.py`
(`evaluate_feature_conditions`), `app/research/engine.py`,
`app/research/metrics.py` (trailing-return math removed, subsumed by
`app/features/price.py`), `app/storage/db.py` (schema + migration +
rebuild), `app/storage/research_repository.py`, `app/api/research.py`,
`app/api/features.py` (`GET /features/vocabulary`),
`frontend/src/types/{features,research}.ts`,
`frontend/src/components/research/ConditionBuilder.tsx` (rewritten),
`ExperimentForm.tsx`/`ExperimentResultsView.tsx`/`ExperimentCompare.tsx`.
**Tests:** 62 new (`test_feature_vocabulary.py`,
`test_research_conditions.py`, plus substantial rewrites of
`test_research_models.py`/`test_research_repository.py`/
`test_research_engine.py`/`test_research_api.py`/`test_research_metrics.py`)
— vocabulary loading/lookup, condition validation (between-shape,
boolean-vs-numeric), multi-condition AND evaluation (including the
DoD's literal 3-condition example), feature-contract-version
reproducibility, missing-feature-record handling, and two regression
tests for the legacy-schema migration bug found live. Full suite: 736
passed. Verified live end-to-end (not just tests): built the DoD's
exact 3-condition experiment through the real UI against the real
backend, ran it, and confirmed a separate trivially-true condition
finds 228 real signals with correct aggregate statistics.

## v0.1.23 — Auto-ingest failure escalation + real-data verification (2026-08-17)

Two things, both closing gaps a real end-to-end run (not just tests)
surfaced. First: verified the full pipeline against a real Alpaca
account for the first time ever -- 2 years of daily + ~60 days of 5m
bars backfilled (v0.1.22's script) into both this worktree's and the
main checkout's databases, and the real-time WebSocket relay
(v0.1.12/13) confirmed actually streaming live TSLA quotes into the UI
(price/bid/ask/volume/timestamp all changing between two screenshots
8 seconds apart). No code changed for this half -- it was already
built, just never exercised against a real account before.

Second: a real gap in `app/ingestion/auto_ingest.py` this exposed --
before now, a pair failing forever (a stale credential, not a
transient blip) logged at the exact same WARNING level, cycle after
cycle, as a five-minute network hiccup, with nothing distinguishing
"ignore this" from "this has been broken for two days." Added
`PairFailureTracker`: loop-level state (deliberately NOT inside the
still-stateless `run_ingestion_cycle()`) that watches consecutive
failures per symbol/timeframe pair and escalates to one ERROR log line
once `AUTO_INGEST_FAILURE_ALERT_THRESHOLD` (default 3) consecutive
cycles have failed, then logs one INFO "recovered" line the first time
that pair succeeds again. Also documented, in `.env.example`, that
`AUTO_INGEST_TIMEFRAMES` needs `5m` added explicitly if intraday data
just backfilled should actually stay current -- auto-ingest only
refreshes whichever timeframes are listed, and the default is `1d`
only.

**Files:** `app/ingestion/auto_ingest.py` (`PairFailureTracker`,
wired into `run_ingestion_loop`), `app/config.py`
(`get_auto_ingest_failure_alert_threshold`), `.env.example`.
**Tests:** `tests/test_auto_ingest.py` — 8 new tests (14 total in that
file): threshold-crossing escalation (exactly one ERROR, not one per
failure past it), continued failure re-alerting every cycle,
below-threshold and reset-before-threshold silence, recovery-after-
escalation logging exactly one INFO line, no recovery line when never
escalated, independent tracking per pair, and one end-to-end test
through the real async loop. Full suite: 672 passed.

## v0.1.22 — Historical backfill script (2026-08-17)

Closed the actual reason this project's data was low-to-nonexistent:
nothing had ever bulk-loaded it. `app/ingestion/auto_ingest.py` (the
one existing unattended pulling mechanism) is built to *stay fresh* --
a small trailing window re-fetched on a timer -- not to backfill years
of history in one run, and it was off by default with no credentials
configured anywhere. Added a real, deep backfill path instead of
stretching auto-ingest to do a job its design doesn't fit:
`app/ingestion/backfill.py::run_backfill()` splits a wide date range
into bounded chunks (`_date_chunks()`), reuses the exact same
`fetch_normalized_bars -> validate_bars -> save_validated_bars`
pipeline every other ingestion path already uses (no new, could-drift
way to ask a provider for bars), retries a 429 with exponential
backoff before giving up on just that chunk, and isolates one chunk's
failure from the rest of the run the same way auto_ingest isolates one
pair's. Re-running the same command is always safe -- storage's
existing `UNIQUE(provider, symbol, timeframe, timestamp)` dedup makes
an already-saved chunk a fast no-op, so there's no separate "resume
point" to track. `scripts/backfill_historical_data.py` is the thin,
untested (like the other manual scripts) CLI wrapper around it.
Real-time streaming needed no new code at all -- `GET
/market-data/stream` (v0.1.12/13) was already fully built for Alpaca
and Massive; it only ever lacked credentials to authenticate with.

**Files:** `app/ingestion/backfill.py` (new), `scripts/backfill_historical_data.py` (new).
**Tests:** `tests/test_backfill.py` (14 tests) — chunk boundaries (gap-
free, non-overlapping, single-day/single-chunk edge cases), multi-
symbol/timeframe coverage, dedup on re-run, quarantine on invalid
OHLCV, one-chunk-failure isolation, 429 retry-then-succeed and
retry-exhausted, chunk-completion callback ordering. Full suite: 664
passed.

## v0.1.21 — Feature Engine v1 (2026-08-17, PR #23)

A deterministic feature-computation layer on top of `historical_bars`:
PRICE (4 trailing returns), VOLUME (volume, time-of-day-aware relative
volume, volume acceleration), VOLATILITY (realized volatility, ATR,
volatility ratio/percentile), MARKET CONTEXT (SPY/QQQ returns +
relative strength, TSLA/NVDA by default), and PRICE POSITION
(VWAP/SMA20/SMA50 distance, intraday range position) — one
`FeatureRecord` per bar, persisted to a new `historical_features` table.
No session/VWAP/volatility concept existed anywhere in this codebase
before this; built from scratch using stdlib `zoneinfo` (NY-local
calendar-day sessions) rather than a new dependency. Every trailing
window verifies exact timestamp contiguity (not just array position) so
a missing bar yields `None`, never a value computed across a gap.
Market-context eligibility is an explicit caller-supplied set, not a
hardcoded rule in the pure engine — MCL gets nothing unless configured
in.

**Files:** `app/features/` (new package), `app/models/features.py`,
`app/storage/feature_repository.py` + `historical_features` table,
`app/api/features.py`.
**Tests:** 123 new tests (10 files) — every feature calculation,
insufficient-history/missing-bar/zero-denominator/timestamp-alignment/
no-look-ahead behavior, TSLA/NVDA context, MCL exclusion, persistence,
reproducibility. Full suite: 650 passed. Caught and fixed a real bug
(`session_lookback_start_index` returning the wrong window boundary)
before it reached the volatility tests.

## v0.1.20 — Research v1 (2026-08-17, PR #22)

A hypothesis-testing engine: define a `Condition` (`"{N}m_return"` +
operator + threshold) and an `Outcome` (`"forward_return"` + horizon +
operator + threshold) as an `Experiment`, run it against
`historical_bars`, and get back every qualifying `ExperimentEvent` plus
aggregate `ExperimentResults` (success rate, average/median/min/max/
std-dev — `None`, not `0.0`, when data is insufficient). The engine is
pure computation (no I/O); the condition at each bar only ever reads
bars up to and including that bar, which is the entire no-look-ahead
guarantee. Re-running an experiment deletes and re-inserts its events
rather than appending, so results stay reproducible. Explicitly not
backtesting (no simulated P&L/position/capital) and not ML.

**Files:** `app/research/` (new package), `app/models/research.py`,
`app/storage/research_repository.py` + `experiments`/`experiment_events`
tables, `app/api/research.py`.
**Tests:** 88 new tests (5 files) — condition/outcome math, signal/event
creation, success/failure classification, aggregate stats, no-look-ahead,
date-range/symbol filtering, persistence, reproducibility. Full suite:
527 passed.

## v0.1.19 — Raw-ingestion storage (2026-08-16, PR #21)

Closed a gap a verification pass found in the shipped v0.1.18 pipeline:
normalization happened *inside* ingestion, so the original provider
JSON/CSV text was parsed and discarded, never persisted. Added a new
`raw_ingestions` table (one row per fetch/upload *request*, not per bar
— preserves each source's own field names/vocabulary, not this app's
canonical schema) and a `persist_raw_ingestion_safely()` hook at the two
places the original payload actually exists, before anything parses it.
Never raises — a raw-storage failure can't turn an otherwise-successful
fetch into a failed one. Nothing in the existing validate/normalize/
store pipeline changed.

**Files:** `app/storage/raw_ingestion_repository.py`, `raw_ingestions`
table, small hooks in `alpaca_provider.py`/`massive_provider.py`/
`historical_comparison.py`.
**Tests:** raw-row-per-request, source-specific field preservation, raw
survives even when the bar it contains is later quarantined, raw
storage is deliberately not deduplicated (unlike `historical_bars`).

## v0.1.18 — Bar validation, quarantine, and auto-ingestion (2026-08-16, PR #20)

Added an explicit validation step between normalize and store: hard
rules (impossible OHLCV values, in-batch duplicates) reject a bar to a
new `quarantined_bars` audit table; soft rules (out-of-order arrival,
unusual gaps, extreme price moves) flag a bar but still store it. Also
added an opt-in (`AUTO_INGEST_ENABLED`) background loop that re-pulls a
configured symbol/timeframe list on an interval, reusing the exact same
fetch/validate/save path a manual "Save to Database" click already
used — off by default so a fresh checkout, test run, or CI never makes
an outbound provider call.

**Files:** `app/ingestion/bar_validation.py`, `quarantined_bars` table,
`app/ingestion/auto_ingest.py`, `app/config.py` `AUTO_INGEST_*` getters,
`app/main.py` lifespan hook.

## v0.1.17 — Persistent storage for historical market data (2026-08-16, PR #19)

A SQLite-backed storage layer (`backend/data/historical_bars.db`) for
fetched historical bars: `POST /market-data/history/save` (a deliberate
manual action on bars already fetched, never a second provider call) and
`GET /market-data/{symbol}/history/stored` (reads back with no provider
contacted at all). Keyed on `(provider, symbol, timeframe, timestamp)`
since different providers can genuinely disagree on OHLCV for "the same"
bar.

**Files:** `app/storage/db.py` (new), `app/storage/historical_bar_repository.py`,
`app/api/historical_storage.py`.

## v0.1.16 — Historical market data + CSV comparison (2026-08-16, PR #18)

First route to call `get_historical_data()` directly rather than as a
quote route's volume side-lookup: `GET /market-data/{symbol}/history`
(TSLA/NVDA, 1m–1d, translated per-provider). Added a CSV-vs-provider
comparison route that diffs an uploaded bar file against a live fetch,
reporting row-count/timestamp/OHLC/volume differences without
auto-correcting either side. Fixed a real bug along the way: Massive's
pagination silently dropped its query string when `httpx` was called
with `params={}` on a URL that already had one.

**Files:** `app/api/historical_data.py`, `app/api/historical_comparison.py`,
`app/ingestion/ohlcv_csv.py`.

## v0.1.15 — Massive free-tier polling fallback (2026-08-16, PR #17)

Massive accounts without WebSocket entitlement now fall back to polling
the free-tier minute-bar REST endpoint (30s interval) instead of failing
outright — derives a quote from the latest bar (no bid/ask, since a bar
has none). Tries the WebSocket first, falls back only on a fatal
WS error.

**Files:** `app/streaming/massive_stream.py` (`MassivePollingQuoteStream`,
`MassiveStream`).

## v0.1.14 — dev.sh fixes (2026-08-16, PR #16)

Found by a real session: `--reload`'s worker child wasn't reaped on
`stop` (leaving a zombie process holding the port), and `backend/.env`
wasn't being loaded automatically. Fixed both.

**Files:** `scripts/dev.sh`.

## v0.1.13 — Real-time TSLA streaming via Massive WebSocket (2026-08-15, PR #15)

Second streaming provider, same shape as Alpaca's (v0.1.12): one
upstream WebSocket connection per symbol, `"Q"`/`"T"` message
normalization, fatal-vs-retryable auth-failure classification (a real
account confirmed `"auth_failed"` means "plan doesn't include WebSocket
access"). `StreamHub` and the reconnect/backoff loop were generalized
(`ReconnectingQuoteStream`, `QuoteStream` protocol) to be provider-
agnostic rather than Alpaca-specific.

**Files:** `app/streaming/massive_stream.py`, `app/streaming/base.py`.

## v0.1.12 — Real-time TSLA streaming via Alpaca WebSocket (2026-08-15, PR #14)

This app's first server-push route: `GET /market-data/stream`
(WebSocket) relays one upstream Alpaca connection per symbol — shared
across every connected browser tab, since Alpaca allows only one live
connection per API key — with reconnect/backoff and state replay for a
late-joining client.

**Files:** `app/streaming/alpaca_stream.py`, `app/streaming/hub.py`,
`app/api/market_data_stream.py`, `frontend/src/hooks/useQuoteStream.ts`.

## v0.1.11 — Normalized LiveQuote + standalone TSLA panel (2026-08-15, PR #13)

Flattened `Quote` (the provider contract) into `LiveQuote`, the actual
HTTP response shape (`symbol/price/bid/ask/volume/timestamp/provider`),
assembled by the route rather than a provider. Added a best-effort
second `get_historical_data()` call for volume, since neither Alpaca's
nor Massive's quote endpoints return cumulative daily volume — a
failure there never fails the quote itself. Added a standalone TSLA
`LiveQuotePanel` instance on `CalculatorPage`, independent of any CSV
import, as a minimal always-visible proof the Provider → Backend →
Normalized Data → UI pipeline actually works.

**Files:** `app/models/market_data.py` (`LiveQuote`), `app/api/market_data.py`,
`frontend/src/components/LiveQuotePanel.tsx`.

## v0.1.10 — Cross-validate Alpaca vs. Massive (2026-08-15, PR #12)

An opt-in script (`scripts/cross_validate_providers.py`, not run by
pytest) that diffs real Alpaca vs. Massive bars/quotes for TSLA/NVDA
against real accounts, to surface where two "equivalent" data sources
genuinely disagree.

**Files:** `scripts/cross_validate_providers.py`.

## v0.1.9 — Live equity quote in the UI (2026-08-15, PR #12)

First frontend use of the provider layer for live (non-CSV) data:
`GET /market-data/{symbol}/quote`, rendered next to a CSV-imported
chain's underlying price as a staleness reference — never an input the
calculator applies automatically.

**Files:** `app/api/market_data.py`, `frontend/src/components/LiveQuotePanel.tsx`.

## v0.1.8 — SchwabProvider (2026-08-15, PR #11)

Real equity historical bars/quotes via Schwab's Trader API —
architecturally the odd one out among the three providers: OAuth2, not
a static key. A 30-minute access token refreshes itself transparently;
a 7-day refresh token needs an interactive login only a human can do
(`scripts/schwab_oauth_bootstrap.py` walks through it once; re-run
roughly weekly).

**Files:** `app/providers/schwab_provider.py`, `scripts/schwab_oauth_bootstrap.py`.

## v0.1.7 — MassiveProvider fixes from real-account testing (2026-08-15, PR #10)

Bugs a real-account test run surfaced and fixed: a millisecond-vs-
nanosecond timestamp mismatch, and case-sensitivity issues in bid/ask
field parsing.

**Files:** `app/providers/massive_provider.py`.

## v0.1.6 — MassiveProvider (2026-08-15, PR #9)

Real equity historical bars/quotes via Massive's (Polygon.io) REST API,
a single `MASSIVE_API_KEY` sent as a bearer token. `get_chain()`
(options) remains a placeholder — a separate, larger integration.

**Files:** `app/providers/massive_provider.py`.

## v0.1.5 — scripts/dev.sh (2026-08-15, PR #8)

One command to start/stop/restart/status both backend and frontend dev
servers together, PID-tracked, logs under gitignored `.dev/`. A dev
convenience wrapper around the same `uvicorn --reload`/`npm run dev`
commands, not a production deployment mechanism.

**Files:** `scripts/dev.sh`.

## v0.1.4 — AlpacaProvider (2026-08-15, PR #7)

Real equity historical bars/quotes via Alpaca's Market Data API v2
(default feed `iex`, the free tier). `get_chain()` (options) remains a
placeholder.

**Files:** `app/providers/alpaca_provider.py`.

## v0.1.3 — Config-driven provider selection (2026-08-15, PR #7)

`MARKET_DATA_PROVIDER` env var + `registry.py` maps a provider name to
its class, plus placeholder Alpaca/Massive/Schwab provider classes
(`get_chain`/`get_historical_data`/`get_latest_quote` all
`NotImplementedError` at this point) — the seam v0.1.4/6/8 later filled
in with real implementations.

**Files:** `app/config.py`, `app/providers/registry.py`.

## v0.1.2 — MarketDataProvider interface + CSVProvider (2026-08-15, PR #6)

The abstraction every future data source implements:
`MarketDataProvider` (an ABC with optional capability methods) and one
normalized result shape (`NormalizedChainResult`) regardless of how the
data arrived. `CSVProvider` — a thin wrapper around the existing v0.1.1
CSV pipeline — is the first, fully working implementation, proving the
interface before any live source existed.

**Files:** `app/providers/base.py`, `app/providers/csv_provider.py`.

## v0.1.1 — CSV market data import (2026-08-14, PR #2)

A second way to fill in the calculator's inputs besides typing them by
hand: upload a CSV export of an options chain, pick a long/short put
from a table, and get an instant client-side Spread Builder preview.
Includes a small in-file scanner (every valid long/short combination,
filterable by DTE/delta/max loss). No live data — nothing is fetched
automatically; the file never leaves your machine except to your own
local backend. No financial formula is duplicated: the CSV path
produces a normal `BearPutSpreadRequest` posted to the same endpoint
manual entry uses (proven identical by
`test_csv_import_api.py::test_csv_derived_request_matches_manual_entry`).

**Files:** `app/ingestion/` (new package), `app/models/option_chain.py`,
`app/api/csv_import.py`, `frontend/src/components/CsvImportWorkflow.tsx`.

## v0.1 — Initial release: transparent bear put spread calculator (2026-08-14, PR #1)

The foundation: manually-entered bear put spread analysis (debit — both
Mid and Conservative Entry conventions — max loss/profit, breakeven,
spread delta, average IV, expected move, breakeven probability),
Phase 2's closed-form probability engine (bucketed distribution +
Expected Value, probabilities proven to sum to exactly 1.0), and
Phase 3's Monte Carlo simulation (up to 100,000 simulated paths through
the identical payoff formulas, as a cross-check against Phase 2's exact
answer). Every formula is visible in the UI, not hidden behind a single
"Calculate" button — the principle every later version was built to
preserve.

**Files:** `app/calculations/`, `app/models/bear_put_spread.py`,
`app/models/response.py`, `frontend/src/calculations/`,
`frontend/src/components/` (calculator + probability/Monte Carlo
sections).
