"""Feature Engine v1's orchestrator (app/features/) -- turns a bar
series (plus, for market-context-eligible symbols, SPY/QQQ's own bar
series) into one FeatureRecord per bar.

Deliberately pure, mirroring app/research/engine.py::run_experiment():
bars in, FeatureRecord[] out -- no I/O, no repository calls, no HTTP
concerns. app/api/features.py is the only caller that fetches bars (via
historical_bar_repository) and persists what this returns (via
app/storage/feature_repository.py).

**No look-ahead, by construction**: every per-category compute_*()
function this loop calls (app/features/price.py, volume.py,
volatility.py, market_context.py, price_position.py) takes the full
`bars` list and an `index`, and is individually documented never to
read past `index` -- and, for market context, never a SPY/QQQ bar at a
LATER timestamp than bars[index]'s own (exact-timestamp matching only,
see market_context.py). This loop itself does not slice or filter
`bars` per iteration; it simply advances `index` forward once per bar,
so nothing here could introduce look-ahead even if a category module's
own internal guarantee were somehow bypassed.

**Market-context eligibility** is passed in explicitly as
`market_context_symbols` -- this module has NO default allowlist and
does not import anything from app.api, the same "pure computation,
scope decisions live at the API route" boundary app/models/research.py
documents for Research v1. An empty/omitted `market_context_symbols`
means no symbol gets market-context features -- see app/api/features.py
for where TSLA/NVDA (ALLOWED_SYMBOLS) becomes the actual default, and
"Do not apply SPY/QQQ context to MCL unless explicitly configured" is
satisfied by MCL simply not being in whatever set the caller passes.
"""

from datetime import datetime

from app.models.features import FeatureRecord
from app.models.market_data import HistoricalBar

from app.features.market_context import build_timestamp_index, compute_market_context_features
from app.features.price import compute_price_features
from app.features.price_position import compute_price_position_features
from app.features.volatility import compute_volatility_features
from app.features.volume import compute_volume_features


def compute_features(
    *,
    symbol: str,
    timeframe: str,
    provider: str,
    bars: list[HistoricalBar],
    calculated_at: datetime,
    spy_bars: list[HistoricalBar] | None = None,
    qqq_bars: list[HistoricalBar] | None = None,
    market_context_symbols: frozenset[str] | set[str] = frozenset(),
) -> list[FeatureRecord]:
    """One FeatureRecord per bar in `bars` (same order, 1:1 -- unlike
    Research v1's engine, which only emits an event when a condition
    fires, this emits a record for every observation, since a feature
    transform has no "condition" of its own).

    `bars` must already be the caller's fully-filtered, normalized
    dataset for `symbol`/`timeframe`/`provider` (see
    historical_bar_repository.get_bars()) -- this function does not
    re-filter by symbol or date itself, and never writes to
    `historical_bars`; it only reads what it is given.

    `spy_bars`/`qqq_bars` (optional) must be normalized bars of the
    SAME `timeframe` -- see app/features/market_context.py for the
    exact-timestamp alignment rule applied per observation. Omitting
    either (or both) is not an error: a market-context-eligible symbol
    with no SPY/QQQ data supplied simply gets an all-None
    MarketContextFeatures for every bar, not a crash.
    """
    symbol_upper = symbol.upper()
    eligible_symbols = {s.upper() for s in market_context_symbols}
    is_market_context_eligible = symbol_upper in eligible_symbols

    spy_bars = spy_bars or []
    qqq_bars = qqq_bars or []
    spy_index_by_timestamp = build_timestamp_index(spy_bars)
    qqq_index_by_timestamp = build_timestamp_index(qqq_bars)

    records: list[FeatureRecord] = []
    for index in range(len(bars)):
        market_context = None
        if is_market_context_eligible:
            market_context = compute_market_context_features(
                bars,
                index,
                timeframe,
                spy_bars=spy_bars,
                spy_index_by_timestamp=spy_index_by_timestamp,
                qqq_bars=qqq_bars,
                qqq_index_by_timestamp=qqq_index_by_timestamp,
            )

        records.append(
            FeatureRecord(
                symbol=symbol_upper,
                timestamp=bars[index].timestamp,
                timeframe=timeframe,
                provider=provider,
                calculated_at=calculated_at,
                price=compute_price_features(bars, index, timeframe),
                volume=compute_volume_features(bars, index, timeframe),
                volatility=compute_volatility_features(bars, index, timeframe),
                market_context=market_context,
                price_position=compute_price_position_features(bars, index, timeframe),
            )
        )

    return records
