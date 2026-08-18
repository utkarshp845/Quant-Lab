"""The explicit development-vs-holdout bar-reading boundary (v0.1.29) --
requirement 2's "the holdout dataset may only be consumed by an
explicitly designated future OOS-validation operation" turned into an
actual function signature.

Reads bars for a partition's two windows through
app/storage/historical_bar_repository.py::get_bars_in_range() -- never
duplicating a bar, only scoping an existing query to one window at a
time (requirement 4). No bars are stored, copied, or cached anywhere by
this module.

WHAT IS ACTUALLY ENFORCED HERE, and what is not (spec: "if the
architecture cannot technically prevent a particular leakage path yet,
document it clearly rather than pretending it is prevented"):

  - get_development_bars() and get_holdout_bars() each query ONLY their
    own window -- a single call through this module can never return a
    mix of development and holdout bars (requirement 3, "mixing
    development and holdout bars"), because the two functions are
    separate and each is bounded by exactly one of the partition's two
    windows.
  - get_holdout_bars() additionally requires `confirm_oos_validation_use`
    to be passed as literal True -- calling it with no arguments, or by
    forwarding a variable that happens to be falsy/unset, raises
    HoldoutAccessError immediately rather than silently returning
    holdout data. This is a REAL technical guard against the accidental
    case the spec asks for (a plain `get_holdout_bars(partition)` call,
    the kind a research script would write while iterating on a
    hypothesis, fails loudly) -- it is NOT a guard against a caller who
    deliberately writes `confirm_oos_validation_use=True` outside the
    "explicitly designated future OOS-validation operation" the spec
    reserves that flag for. Python has no access-control mechanism that
    could make that distinction (any caller can pass any boolean); the
    flag's only job is to make holdout access an unmistakably deliberate
    act in the CODE that calls it, readable in review, not to
    cryptographically enforce who is allowed to set it.
  - UPDATE (OOS Evaluation v1, v0.1.31): the "future OOS-validation
    operation" this module anticipated now exists --
    app/oos_evaluation/engine.py::evaluate_oos() is the ONE caller that
    passes `confirm_oos_validation_use=True`, and it is the only place
    in this codebase that does. Nothing else calls get_holdout_bars()
    with the flag set to True.
"""

from pathlib import Path

from app.models.market_data import HistoricalBar
from app.models.oos_partition import OOSPartition
from app.storage import historical_bar_repository


class HoldoutAccessError(ValueError):
    """Raised by get_holdout_bars() when `confirm_oos_validation_use`
    was not explicitly True -- see the module docstring above."""


def get_development_bars(partition: OOSPartition, *, db_path: str | Path | None = None) -> list[HistoricalBar]:
    """Bars within `partition`'s development window only -- freely
    usable for feature computation, research, experiment creation,
    backtesting, statistical validation, and hypothesis iteration (spec
    section 2). No confirmation flag: development access is the
    default, unrestricted path, matching every existing engine's
    current (unmodified) behavior of reading historical_bar_repository
    directly."""
    return historical_bar_repository.get_bars_in_range(
        symbol=partition.symbol,
        timeframe=partition.timeframe,
        provider=partition.provider,
        start=partition.development_start,
        end=partition.development_end,
        db_path=db_path,
    )


def get_holdout_bars(
    partition: OOSPartition,
    *,
    confirm_oos_validation_use: bool,
    db_path: str | Path | None = None,
) -> list[HistoricalBar]:
    """Bars within `partition`'s holdout window -- reserved for a
    future, explicitly designated OOS-validation operation (spec
    section 2). Raises HoldoutAccessError unless
    `confirm_oos_validation_use=True` is passed explicitly -- see the
    module docstring for exactly what that does and does not guard
    against."""
    if confirm_oos_validation_use is not True:
        raise HoldoutAccessError(
            f"Holdout bars for partition {partition.id!r} were requested without "
            "confirm_oos_validation_use=True. This flag exists to make holdout access a deliberate, "
            "reviewable act, never an accidental default reached while iterating on a hypothesis. No "
            "OOS-validation operation exists yet in this codebase (out of this feature's scope) -- this "
            "function is the seam a future one will call through, with the flag set only from that "
            "explicitly designated code path."
        )
    return historical_bar_repository.get_bars_in_range(
        symbol=partition.symbol,
        timeframe=partition.timeframe,
        provider=partition.provider,
        start=partition.holdout_start,
        end=partition.holdout_end,
        db_path=db_path,
    )
