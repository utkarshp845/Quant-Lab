"""Unconditional-baseline construction for Statistical Validation V1
(app/statistical_validation/).

Builds Population A -- "what does a forward return from ANY eligible
bar look like, ignoring the research condition entirely" -- by calling
app.backtesting.engine.run_backtest() (imported, NEVER modified) with a
trivial CONTROL_CONDITION that is true for every bar with a feature
record, instead of re-deriving forward-return/MFE/MAE math a second
time in a different module. `volume.volume` is a NUMERIC feature that
is NEVER None for a real bar (app/models/features.py::VolumeFeatures:
"the bar's own volume, always known -- it is not a derived
calculation") and is never negative, so `>= 0` matches every bar that
has a feature record at all -- exactly the same "eligible bar"
population run_backtest() already restricts a real experiment to via
its own unmodified logic (a feature-record match, a next-bar-open
entry actually being possible, at least one measurable window). This
is not a second implementation of "what bars are eligible" -- it is the
one, real implementation, run again with a condition that never filters
anything out.

Known, deliberate property (see app/statistical_validation/engine.py's
module docstring and the report's own Limitations section): this
baseline is NOT exclusive of the conditioned population -- every bar
that satisfies the real research condition also satisfies this control
condition, so the ~124 conditioned observations are themselves a subset
of the baseline. This makes any measured difference between the two
populations CONSERVATIVE (biased toward finding LESS of a difference
than a fully disjoint baseline would show), not a source of false
positives -- and it is the identical baseline definition Baseline
Analysis V1 used, kept unchanged here so results stay comparable across
both analyses.
"""

from app.backtesting.engine import run_backtest
from app.models.backtesting import BacktestSignal
from app.models.features import FeatureRecord
from app.models.market_data import HistoricalBar
from app.models.research import FeatureCondition, FeatureConditionOperator

CONTROL_CONDITION: list[FeatureCondition] = [
    FeatureCondition(feature_id="volume.volume", operator=FeatureConditionOperator.GTE, value=0)
]


def compute_unconditional_baseline(
    *,
    symbol: str,
    timeframe: str,
    windows: list[int],
    bars: list[HistoricalBar],
    feature_records: list[FeatureRecord],
    feature_contract_version: str,
) -> list[BacktestSignal]:
    """Runs the real, unmodified Backtesting v1 engine with
    CONTROL_CONDITION over the exact same `bars`/`feature_records` a
    real experiment's backtest already used, so the baseline reflects
    the identical entry rule, window definitions, and
    insufficient-future-data exclusions -- never a second, hand-rolled
    approximation of any of them."""
    signals, _ = run_backtest(
        backtest_id="baseline-control",
        experiment_id="baseline-control",
        symbol=symbol,
        timeframe=timeframe,
        conditions=CONTROL_CONDITION,
        windows=windows,
        bars=bars,
        feature_records=feature_records,
        feature_contract_version=feature_contract_version,
    )
    return signals
