"""The historical-features repository -- the ONLY module that writes
SQL for `historical_features` (app/storage/db.py). Same boundary rule
as app/storage/historical_bar_repository.py and app/storage/
research_repository.py: nothing outside this file (app/api/features.py,
the engine) touches sqlite3 or a SQL string for this table.

    app/features/engine.py     bars (+ spy/qqq) -> FeatureRecord[] (pure, no I/O)
    app/api/features.py        HTTP glue: calls this module + engine.py + historical_bar_repository
    Storage (THIS FILE)        FeatureRecord <-> database rows, and back

Every function here takes/returns FeatureRecord (app/models/features.py)
-- never a raw sqlite3.Row past this file's own boundary.
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.models.features import (
    FEATURE_CONTRACT_VERSION,
    FeatureRecord,
    MarketContextFeatures,
    PriceFeatures,
    PricePositionFeatures,
    VolatilityFeatures,
    VolumeFeatures,
)
from app.storage.db import get_connection

# One tuple per leaf feature column, in the exact order the schema
# declares them (app/storage/db.py) -- used to build both the INSERT
# column list and the row-unpacking logic below, so the two can never
# silently drift out of sync with each other (they stay in sync with
# the schema itself only by both being reviewed together, same as any
# other hand-maintained column list in this app).
_PRICE_COLUMNS = ("return_5m", "return_15m", "return_30m", "return_60m")
_VOLUME_COLUMNS = ("relative_volume", "volume_acceleration")  # `volume` itself is handled separately (NOT NULL, int)
_VOLATILITY_COLUMNS = ("realized_volatility", "atr", "volatility_ratio", "volatility_percentile")
_MARKET_CONTEXT_COLUMNS = (
    "spy_return_5m",
    "spy_return_15m",
    "spy_return_30m",
    "spy_return_60m",
    "qqq_return_5m",
    "qqq_return_15m",
    "qqq_return_30m",
    "qqq_return_60m",
    "relative_strength_spy_5m",
    "relative_strength_spy_15m",
    "relative_strength_spy_30m",
    "relative_strength_spy_60m",
    "relative_strength_qqq_5m",
    "relative_strength_qqq_15m",
    "relative_strength_qqq_30m",
    "relative_strength_qqq_60m",
)
_PRICE_POSITION_COLUMNS = ("vwap_distance", "ma20_distance", "ma50_distance", "intraday_range_position")


def save_features(records: list[FeatureRecord], *, db_path: str | Path | None = None) -> int:
    """Persists `records`, one row per record -- INSERT OR REPLACE
    keyed on the table's (symbol, timeframe, provider, timestamp)
    UNIQUE constraint, so recomputing features for a bar that already
    has a row overwrites it rather than erroring or silently no-oping
    (see the schema comment in app/storage/db.py for why: a feature
    row is derived data, not an original observation). Returns
    len(records) -- every record is either a fresh insert or a
    replace, both a genuine write, unlike save_bars()'s
    inserted/skipped_duplicates split (there is no "duplicate" concept
    here to report separately).
    """
    if not records:
        return 0

    conn = get_connection(db_path)
    try:
        with conn:
            for record in records:
                conn.execute(_UPSERT_SQL, _record_to_row(record))
    finally:
        conn.close()
    return len(records)


def get_features(
    *,
    symbol: str,
    timeframe: str,
    provider: str,
    start: date,
    end: date,
    db_path: str | Path | None = None,
) -> list[FeatureRecord]:
    """Previously-saved FeatureRecords for `symbol`/`timeframe`/
    `provider` whose timestamp falls within [start, end] (both
    inclusive dates -- identical semantics to
    historical_bar_repository.get_bars()), ordered oldest-first.
    """
    symbol = symbol.upper()
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt_exclusive = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT {_ALL_COLUMNS_SQL}
            FROM historical_features
            WHERE symbol = ? AND timeframe = ? AND provider = ?
              AND timestamp >= ? AND timestamp < ?
            ORDER BY timestamp ASC
            """,
            (symbol, timeframe, provider, start_dt.isoformat(), end_dt_exclusive.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    return [_row_to_record(row) for row in rows]


_ALL_COLUMNS = (
    ("symbol", "timeframe", "provider", "timestamp", "calculated_at", "feature_contract_version", "market_context_applicable")
    + _PRICE_COLUMNS
    + ("volume",)
    + _VOLUME_COLUMNS
    + _VOLATILITY_COLUMNS
    + _MARKET_CONTEXT_COLUMNS
    + _PRICE_POSITION_COLUMNS
)
_ALL_COLUMNS_SQL = ", ".join(_ALL_COLUMNS)
_UPSERT_SQL = (
    f"INSERT OR REPLACE INTO historical_features ({_ALL_COLUMNS_SQL}) "
    f"VALUES ({', '.join('?' for _ in _ALL_COLUMNS)})"
)


def _record_to_row(record: FeatureRecord) -> tuple:
    market_context = record.market_context
    market_context_values = (
        tuple(getattr(market_context, col) for col in _MARKET_CONTEXT_COLUMNS)
        if market_context is not None
        else tuple(None for _ in _MARKET_CONTEXT_COLUMNS)
    )
    return (
        record.symbol,
        record.timeframe,
        record.provider,
        _to_storage_timestamp(record.timestamp),
        _to_storage_timestamp(record.calculated_at),
        record.feature_contract_version,
        1 if market_context is not None else 0,
        *(getattr(record.price, col) for col in _PRICE_COLUMNS),
        record.volume.volume,
        *(getattr(record.volume, col) for col in _VOLUME_COLUMNS),
        *(getattr(record.volatility, col) for col in _VOLATILITY_COLUMNS),
        *market_context_values,
        *(getattr(record.price_position, col) for col in _PRICE_POSITION_COLUMNS),
    )


def _row_to_record(row) -> FeatureRecord:
    market_context = None
    if row["market_context_applicable"]:
        market_context = MarketContextFeatures(**{col: row[col] for col in _MARKET_CONTEXT_COLUMNS})

    return FeatureRecord(
        symbol=row["symbol"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        timeframe=row["timeframe"],
        provider=row["provider"],
        calculated_at=datetime.fromisoformat(row["calculated_at"]),
        feature_contract_version=row["feature_contract_version"] or FEATURE_CONTRACT_VERSION,
        price=PriceFeatures(**{col: row[col] for col in _PRICE_COLUMNS}),
        volume=VolumeFeatures(volume=row["volume"], **{col: row[col] for col in _VOLUME_COLUMNS}),
        volatility=VolatilityFeatures(**{col: row[col] for col in _VOLATILITY_COLUMNS}),
        market_context=market_context,
        price_position=PricePositionFeatures(**{col: row[col] for col in _PRICE_POSITION_COLUMNS}),
    )


def _to_storage_timestamp(value: datetime) -> str:
    """Same normalization as historical_bar_repository.py's own
    helper: a naive datetime is assumed UTC rather than rejected, then
    stored as a fixed-offset UTC ISO-8601 string."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
