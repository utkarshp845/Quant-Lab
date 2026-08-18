"""Persistent/response shapes for Experiment Freeze & Provenance v1
(v0.1.30, app/research/lifecycle.py, app/storage/
experiment_freeze_repository.py, app/api/experiment_freeze.py): the
immutable record captured the instant a Research Experiment (app/
models/research.py::Experiment) freezes, and the read views built on
top of it.

Imports app.models.research directly (FeatureCondition, Outcome,
ExperimentLifecycleState) rather than re-defining equivalent shapes --
"the exact fields should follow the existing Experiment model rather
than duplicating concepts unnecessarily" is this feature's own
instruction. This is a leaf-to-leaf import (app/models/research.py is
itself a leaf module: pydantic, the stdlib, and app.models.features
only) -- this file still never imports app.research, app.oos, or
app.api.

ExperimentFreezeSnapshot deliberately duplicates a subset of
Experiment's own field VALUES at the moment of freezing, not by
reference -- that is the entire point (spec section 5: "do not rely
solely on the current mutable Experiment row"). Nothing about the
`experiments` row is relied upon after this snapshot is taken to answer
"what hypothesis was evaluated" -- even though nothing in this codebase
can currently mutate those fields on the live row either (see
Experiment's own docstring), the snapshot is what makes that a
STRUCTURAL guarantee rather than an incidental one dependent on no
future code path ever adding a mutation endpoint.
"""

from datetime import date, datetime

from pydantic import BaseModel

from app.models.oos_partition import OOSPartition
from app.models.research import ExperimentLifecycleState, FeatureCondition, Outcome


class OOSPartitionLinkRequest(BaseModel):
    """Body of POST /research/experiments/{id}/oos-partition."""

    oos_partition_id: str


class ExperimentFreezeSnapshot(BaseModel):
    """The immutable record persisted exactly once, at freeze time --
    app/storage/experiment_freeze_repository.py's `experiment_id` is
    its PRIMARY KEY (one row per experiment, ever; there is no
    re-freeze operation). Every field here is a VALUE copied at freeze
    time, not a live reference -- see the module docstring.

    Field names deliberately mirror Experiment's own
    (symbol/timeframe/provider/start_date/end_date/conditions/outcome/
    feature_contract_version/name/hypothesis) rather than inventing
    parallel names for the same concepts (spec section 2). `start_date`/
    `end_date` are the experiment's DEVELOPMENT range -- unchanged
    naming from Experiment, not renamed to "development_start"/
    "development_end" (the OOS partition's own naming, app/models/
    oos_partition.py) specifically because these values came FROM an
    Experiment, not a partition -- the OOS partition, if any, is
    `oos_partition_id`, a separate concept (see the module docstring
    on hypothesis_hash's own exclusion of it, and
    app/research/lifecycle.py::canonicalize_hypothesis()).

    `hypothesis_hash` is the deterministic provenance hash (requirement
    4 -- see app/research/lifecycle.py::compute_hypothesis_hash()) of
    exactly the fields below that define the hypothesis's RESEARCH
    MEANING: symbol/timeframe/provider/start_date/end_date/
    feature_contract_version/conditions/outcome. `name`/`hypothesis`
    (free-text) and `oos_partition_id` are captured here for
    provenance/forensic completeness (section 7: "which OOS partition
    was reserved") but are NOT part of that hash -- see
    canonicalize_hypothesis()'s own docstring for why.
    """

    experiment_id: str
    hypothesis_hash: str
    name: str
    hypothesis: str
    symbol: str
    timeframe: str
    provider: str
    start_date: date
    end_date: date
    feature_contract_version: str
    conditions: list[FeatureCondition]
    outcome: Outcome
    oos_partition_id: str | None
    experiment_created_at: datetime
    frozen_at: datetime


class ExperimentProvenance(BaseModel):
    """The read view GET /research/experiments/{id}/provenance returns
    -- everything requirement 7 asks reconstructing an evaluated
    hypothesis to be able to answer, in one response: what data was
    used (symbol/timeframe/provider/start_date/end_date), what
    hypothesis was tested (conditions/outcome), what feature contract
    was used (feature_contract_version), and which OOS partition was
    reserved (`oos_partition`, the full linked OOSPartition object, not
    just its id -- fetched live from app/storage/
    oos_partition_repository.py at read time, app/oos/ is never
    modified to produce this; None if no partition was ever
    associated). `lifecycle_state`/`archived_at` are included so a
    caller never has to make a second call to
    GET /research/experiments/{id} just to know whether this provenance
    describes an experiment that has since moved on to OOS_EVALUATED or
    ARCHIVED.
    """

    experiment_id: str
    lifecycle_state: ExperimentLifecycleState
    hypothesis_hash: str
    name: str
    hypothesis: str
    symbol: str
    timeframe: str
    provider: str
    start_date: date
    end_date: date
    feature_contract_version: str
    conditions: list[FeatureCondition]
    outcome: Outcome
    oos_partition: OOSPartition | None
    experiment_created_at: datetime
    frozen_at: datetime
    archived_at: datetime | None
