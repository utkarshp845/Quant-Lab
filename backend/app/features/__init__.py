"""Feature Engine v1 -- the deterministic feature-computation layer
(app/features/).

    normalized historical_bars -> feature engine (THIS PACKAGE) -> historical_features

Transforms normalized HistoricalBar series (app/models/market_data.py,
app/storage/historical_bar_repository.py -- never raw/quarantined
data) into FeatureRecord rows (app/models/features.py): one record per
bar, covering PRICE/VOLUME/VOLATILITY/MARKET CONTEXT/PRICE POSITION
features, per this feature's fixed v1 contract.

Deliberately pure, like app/research/: every module here takes bars
(plain Python lists of HistoricalBar) in and returns feature values or
FeatureRecord objects out -- no I/O, no repository calls, no HTTP
concerns. That is what makes every calculation unit-testable against
synthetic, in-memory bars with no database and no network call
anywhere in the loop (see tests/test_feature_*.py). app/api/features.py
is the only caller that fetches bars (via historical_bar_repository)
and persists what this package returns (via
app/storage/feature_repository.py).

Modules, one per contract section (so each is independently testable
-- see each module's own docstring for its exact formulas and the
missing-bar/zero-denominator/insufficient-history rules it applies):

    timeframes.py       timeframe <-> minutes <-> bar-count conversion,
                         and the timestamp-contiguity check every
                         trailing-window feature relies on for its
                         missing-bar handling
    session.py          NY-local session-day grouping, annualization
                         constants, and the "N distinct prior sessions"
                         lookback walk volatility_percentile/ratio and
                         relative_volume both need
    price.py            return_5m/15m/30m/60m
    volume.py            volume, relative_volume, volume_acceleration
    volatility.py        realized_volatility, atr, volatility_ratio,
                         volatility_percentile
    market_context.py    SPY/QQQ returns and relative strength
    price_position.py    vwap_distance, ma20_distance, ma50_distance,
                         intraday_range_position
    engine.py             compute_features(): the orchestrator that
                         calls all of the above once per bar

No look-ahead, by construction: every function here that computes a
value "as of" bars[index] only ever reads bars at index or earlier
(and, for market context, the SPY/QQQ bar with the identical
timestamp -- never a later one). See engine.py's own docstring for how
that guarantee is preserved across the whole per-bar loop.
"""
