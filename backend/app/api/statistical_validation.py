"""API routes exposing Statistical Validation V1/V2 (app/
statistical_validation/, app/statistical_validation/v2/) -- the one
genuine "engine exists, no HTTP route exists" gap the redesign's audit
found: both engines have been fully built and tested since v0.1.32/
v0.1.34's own scripts/run_statistical_validation*.py, but neither was
ever reachable over HTTP; only a manual CLI script could run them.

    GET /backtests/{backtest_id}/statistical-validation      V1 report
    GET /backtests/{backtest_id}/statistical-validation-v2   V2 report (dependence-aware baseline)

Both routes call `build_statistical_validation_report()`/
`build_statistical_validation_report_v2()` UNMODIFIED -- this file adds
zero new statistics, exactly the same "expose backend functionality
through APIs" the architectural rules ask for, not a second
implementation. `experiment_id` is derived from the Backtest's own
`experiment_id` field rather than asked of the caller a second time
(the engine functions themselves still re-verify the backtest
references that exact experiment -- see their own ValueError checks),
matching every other route in this app that nests a sub-resource's id
under its already-known parent instead of re-asking for it.
"""

from fastapi import APIRouter, HTTPException

from app.models.statistical_validation import StatisticalValidationReport
from app.models.statistical_validation_v2 import StatisticalValidationReportV2
from app.statistical_validation.engine import (
    DEFAULT_CI_LEVEL,
    DEFAULT_N_BOOTSTRAP,
    DEFAULT_N_PERMUTATIONS,
    DEFAULT_SEED,
    build_statistical_validation_report,
)
from app.statistical_validation.v2.engine import (
    DEFAULT_BLOCK_LENGTH_MULTIPLIER,
    DEFAULT_CI_LEVEL as DEFAULT_CI_LEVEL_V2,
    DEFAULT_N_RESAMPLES,
    DEFAULT_POWER,
    DEFAULT_SEED as DEFAULT_SEED_V2,
    build_statistical_validation_report_v2,
)
from app.storage import backtest_repository

router = APIRouter()


def _get_backtest_or_404(backtest_id: str):
    backtest = backtest_repository.get_backtest(backtest_id)
    if backtest is None:
        raise HTTPException(status_code=404, detail=f"No backtest with id {backtest_id!r}")
    return backtest


@router.get("/backtests/{backtest_id}/statistical-validation", response_model=StatisticalValidationReport)
def get_statistical_validation(
    backtest_id: str,
    primary_window_bars: int = 5,
    seed: int = DEFAULT_SEED,
    n_bootstrap: int = DEFAULT_N_BOOTSTRAP,
    n_permutations: int = DEFAULT_N_PERMUTATIONS,
    ci_level: float = DEFAULT_CI_LEVEL,
) -> StatisticalValidationReport:
    """A derived, on-demand report -- never persisted (see
    StatisticalValidationReport's own docstring), so this always
    recomputes from the backtest's own already-persisted signals plus
    the underlying bars/features. A 400 (not 500) for anything
    build_statistical_validation_report() itself rejects (backtest
    doesn't reference the experiment, primary_window_bars isn't one of
    the backtest's configured windows, or the persisted signals fail
    the reproduction check) -- matching this app's "an already-scoped
    validation failure is a 4xx, not a crash" convention."""
    backtest = _get_backtest_or_404(backtest_id)
    try:
        return build_statistical_validation_report(
            experiment_id=backtest.experiment_id,
            backtest_id=backtest_id,
            primary_window_bars=primary_window_bars,
            seed=seed,
            n_bootstrap=n_bootstrap,
            n_permutations=n_permutations,
            ci_level=ci_level,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/backtests/{backtest_id}/statistical-validation-v2", response_model=StatisticalValidationReportV2)
def get_statistical_validation_v2(
    backtest_id: str,
    primary_window_bars: int = 5,
    seed: int = DEFAULT_SEED_V2,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    ci_level: float = DEFAULT_CI_LEVEL_V2,
    block_length_multiplier: int = DEFAULT_BLOCK_LENGTH_MULTIPLIER,
    power: float = DEFAULT_POWER,
) -> StatisticalValidationReportV2:
    """V2's dependence-aware successor -- see app/statistical_validation/
    v2/engine.py's own module docstring for exactly what it corrects
    (V1's baseline treated heavily-overlapping forward-return windows
    as independent observations; V2 doesn't). Same error-mapping
    convention as V1's route above."""
    backtest = _get_backtest_or_404(backtest_id)
    try:
        return build_statistical_validation_report_v2(
            experiment_id=backtest.experiment_id,
            backtest_id=backtest_id,
            primary_window_bars=primary_window_bars,
            seed=seed,
            n_resamples=n_resamples,
            ci_level=ci_level,
            block_length_multiplier=block_length_multiplier,
            power=power,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
