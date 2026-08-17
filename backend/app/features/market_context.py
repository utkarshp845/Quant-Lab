"""MARKET CONTEXT features (app/features/): SPY/QQQ returns at the same
four horizons as PriceFeatures, and this symbol's relative strength
against each.

    relative_strength_spy_Xm = asset's own return_Xm - SPY's return_Xm
    relative_strength_qqq_Xm = asset's own return_Xm - QQQ's return_Xm

**Timestamp alignment** (this feature's rule 5): a SPY/QQQ bar is
matched to the underlying's bar by EXACT timestamp equality only -- no
nearest/fuzzy matching. Both series must already be the same
`timeframe`; matching by exact timestamp is what "the same point in
time" means for two series recorded at the same cadence. When no
SPY/QQQ bar exists at exactly the underlying bar's timestamp (a gap in
one series but not the other), every market-context feature for that
observation is None -- the SAME missing-bar rule (rule 4) applied
across symbols rather than within one.

**Eligibility is decided by the caller** (app/features/engine.py), not
here: this module only computes values given whatever SPY/QQQ bars it
is handed. Passing empty/no SPY or QQQ data yields an all-None
MarketContextFeatures (a symbol that IS configured for market context
but has no comparison data available yet -- rule 3), which is a
different, distinct outcome from FeatureRecord.market_context being
None entirely (a symbol that is NOT configured for market context at
all -- see engine.py and the "Do not apply SPY/QQQ context to MCL
unless explicitly configured" rule).
"""

from datetime import datetime

from app.models.features import MarketContextFeatures
from app.models.market_data import HistoricalBar

from app.features.price import RETURN_HORIZONS_MINUTES, trailing_return


def build_timestamp_index(bars: list[HistoricalBar]) -> dict[datetime, int]:
    """A timestamp -> position lookup for one bar series, built once
    per series (not once per underlying bar -- see engine.py, which
    builds this a single time for spy_bars/qqq_bars before looping over
    the underlying's own bars) so exact-timestamp alignment is an O(1)
    lookup instead of an O(n) scan repeated for every observation."""
    return {bar.timestamp: i for i, bar in enumerate(bars)}


def compute_market_context_features(
    bars: list[HistoricalBar],
    index: int,
    timeframe: str,
    *,
    spy_bars: list[HistoricalBar],
    spy_index_by_timestamp: dict[datetime, int],
    qqq_bars: list[HistoricalBar],
    qqq_index_by_timestamp: dict[datetime, int],
) -> MarketContextFeatures:
    signal_timestamp = bars[index].timestamp
    spy_index = spy_index_by_timestamp.get(signal_timestamp)
    qqq_index = qqq_index_by_timestamp.get(signal_timestamp)

    values: dict[str, float | None] = {}
    for horizon in RETURN_HORIZONS_MINUTES:
        asset_return = trailing_return(bars, index, horizon, timeframe)
        spy_return = trailing_return(spy_bars, spy_index, horizon, timeframe) if spy_index is not None else None
        qqq_return = trailing_return(qqq_bars, qqq_index, horizon, timeframe) if qqq_index is not None else None

        values[f"spy_return_{horizon}m"] = spy_return
        values[f"qqq_return_{horizon}m"] = qqq_return
        values[f"relative_strength_spy_{horizon}m"] = (
            asset_return - spy_return if asset_return is not None and spy_return is not None else None
        )
        values[f"relative_strength_qqq_{horizon}m"] = (
            asset_return - qqq_return if asset_return is not None and qqq_return is not None else None
        )

    return MarketContextFeatures(**values)
