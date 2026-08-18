"""Persistent/response shapes for OOS Evidence Accumulation V1
(app/oos_evidence/, app/storage/oos_evidence_repository.py,
app/api/oos_evidence.py): letting an already-FROZEN experiment
accumulate MORE THAN ONE independent OOS evaluation period over time,
without ever touching the hypothesis, the ExperimentFreezeSnapshot, any
prior OOSEvaluationResult, or development data.

An "OOS period" (OOSPeriod below) is a REGISTRATION -- a link between
one frozen experiment and one already-existing, independently-created
OOSPartition (app/models/oos_partition.py, still created the normal
way via the existing, UNMODIFIED `POST /oos/partitions`). Nothing new
is invented for the underlying dataset split: a period's own
development/holdout windows, symbol/timeframe/provider, and id are all
simply the referenced OOSPartition's own fields (`id` here IS
`oos_partition_id` -- the same "no second id minted for one concept"
precedent app/models/oos_evaluation.py::OOSEvaluationResult.frozen_snapshot_id
already sets for `experiment_id`). What this module adds is the LINK
itself (which partitions are registered as evaluation periods for
WHICH frozen experiment) plus the read-only evidence-accumulation
summary built on top of every period's own COMPLETED evaluation.

Kept a leaf module, matching every other app/models/*.py file in this
codebase: only pydantic, the stdlib, and this app's own sibling leaf
models (app.models.backtesting, app.models.oos_evaluation) are
imported here -- never app.oos_evidence, app.storage, or app.api.
"""

from datetime import datetime

from pydantic import BaseModel

from app.models.backtesting import BacktestResults
from app.models.oos_evaluation import OOSEvaluationStatus


class OOSPeriodLinkRequest(BaseModel):
    """Body of POST /research/experiments/{id}/oos-periods -- the ONLY
    input this feature accepts to register an additional OOS evaluation
    period: which already-created, independent OOSPartition (created
    via the existing, unmodified POST /oos/partitions -- never a new
    partition-creation path) to link. Nothing about the frozen
    hypothesis, and nothing about the underlying dataset split itself
    (symbol/timeframe/provider/development window/holdout window) is
    accepted here -- all of that is read from the referenced
    OOSPartition and the experiment's own ExperimentFreezeSnapshot, at
    the API route (app/api/oos_evidence.py), never re-entered by a
    caller."""

    oos_partition_id: str


class OOSPeriod(BaseModel):
    """The persisted link record (app/storage/oos_evidence_repository.py,
    table `experiment_oos_periods`): one row per (experiment_id,
    oos_partition_id) pair -- a partition may, in principle, be
    registered for more than one experiment (it is an independently
    created, reusable dataset-split definition, exactly like every
    other OOSPartition), but never registered twice for the SAME
    experiment (app/oos_evidence/period.py::validate_new_period()).

    `id` == `oos_partition_id` -- the referenced OOSPartition's own,
    already-deterministic id (app/models/oos_partition.py::
    compute_partition_id()); no second id is minted for the same
    concept. `symbol`/`timeframe`/`provider`/`oos_start`/`oos_end` are
    copied from that partition at registration time (`oos_start`/
    `oos_end` == the partition's own `holdout_start`/`holdout_end`,
    renamed to this feature's own "OOS period" vocabulary) so a reader
    of this row never has to join back to `oos_partitions` to see what
    period it actually covers -- the same "self-describing row" pattern
    app/models/oos_evaluation.py::OOSEvaluationResult already applies.
    Every field here is set once, at registration, and never updated
    again -- there is no edit endpoint, matching this app's universal
    "no mutation after creation except an explicit, narrow status
    field" rule (and this record has no status field at all)."""

    id: str
    experiment_id: str
    oos_partition_id: str
    symbol: str
    timeframe: str
    provider: str
    oos_start: datetime
    oos_end: datetime
    label: str | None
    registered_at: datetime


class OOSEvidencePeriodResult(BaseModel):
    """One row of OOSEvidenceSummary.per_period_results below -- one
    COMPLETED OOSEvaluationResult's own identity and results, verbatim
    (never re-aggregated across periods here -- see OOSEvidenceSummary's
    own docstring for what IS combined across periods, and how). If the
    SAME OOS partition was evaluated more than once (only possible for
    an experiment's originally frozen-time-linked partition, via the
    pre-existing, unmodified OOS Evaluation v1 re-run behavior -- an
    OOS Evidence Accumulation period, once COMPLETED, can never be
    re-evaluated, see app/oos_evidence/evaluation.py), each of those
    COMPLETED runs still appears here as its own row: this list mirrors
    evaluations, not periods, one-for-one, so no evidence is ever
    silently collapsed away."""

    evaluation_id: str
    oos_partition_id: str
    oos_start: datetime
    oos_end: datetime
    status: OOSEvaluationStatus
    signal_count: int
    episode_count: int
    results: BacktestResults | None
    evaluated_at: datetime


class OOSEvidenceSummary(BaseModel):
    """The read-only evidence-accumulation model for one frozen
    experiment (app/oos_evidence/aggregation.py::build_evidence_summary()) --
    aggregates every COMPLETED OOS evaluation ever run for
    `experiment_id`, across every OOS period (the experiment's
    originally frozen-time-linked partition, if any, AND every
    additional period registered later), all against the SAME immutable
    `hypothesis_hash`. A FAILED evaluation contributes only to
    `failed_evaluation_count` -- nothing else here is computed from one.

    Deliberately keeps two different sample sizes visible side by
    side, NEVER conflating them (this feature's own explicit
    instruction: "do not simply pool all raw signals and pretend they
    are independent"): `total_raw_signals` is every individual
    qualifying signal, pooled across every period's own evaluation --
    a real count, but NOT a count of independent observations (a
    research condition that stays true for several consecutive bars
    produces several highly-correlated raw signals -- the exact
    clustering app/statistical_validation/episodes.py already
    documents and corrects for, for one backtest at a time).
    `total_independent_episodes` is the non-overlapping, one-per-onset
    count that SAME module's group_into_episodes() computes -- reused
    UNMODIFIED here, PER PERIOD (never pooled across periods first --
    see app/oos_evidence/aggregation.py's own docstring for why two
    different periods' signals must never be grouped into one episode
    together), then summed. A caller comparing the two numbers directly
    sees, honestly, how much smaller the truly-independent evidence is
    than the raw signal count.

    `mean_return`/`median_return`/`win_rate`/`std_dev_return`/
    `mean_mfe`/`mean_mae` are computed over the pooled RAW signal set
    (via app.backtesting.aggregation.aggregate_results(), reused
    UNMODIFIED -- the identical function Backtesting v1 and OOS
    Evaluation v1 already use for one run's own signals) -- ordinary,
    honestly-labeled descriptive statistics, explicitly NOT a
    significance claim: no p-value, confidence interval, standard
    error, or significant/not-significant verdict is computed anywhere
    in this module. That is OOS Statistical Review's job, a later,
    separate step this feature deliberately does not implement (this
    feature's own explicit instruction: "do not perform formal
    statistical significance testing in V1").
    """

    experiment_id: str
    hypothesis_hash: str
    oos_period_count: int
    completed_evaluation_count: int
    failed_evaluation_count: int
    total_raw_signals: int
    total_independent_episodes: int
    mean_return: float | None
    median_return: float | None
    win_rate: float | None
    std_dev_return: float | None
    mean_mfe: float | None
    mean_mae: float | None
    earliest_oos_start: datetime | None
    latest_oos_end: datetime | None
    per_period_results: list[OOSEvidencePeriodResult]
