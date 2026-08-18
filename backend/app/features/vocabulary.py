"""Feature Engine v1's canonical vocabulary (v0.1.24) -- the single
source of truth Research's condition builder (GET /features/vocabulary,
app/api/features.py) reads to know which features exist, what each one
means, what type it is, and which comparison operators make sense
against it. This is what closes the "hardcoded 25 features inside
Research" gap: nothing in app/research/ or app/models/research.py
names a feature directly -- every Condition (app/models/research.py::
FeatureCondition) references a `feature_id` from THIS list, checked
against it at the API route (app/api/research.py), the same layer that
already owns symbol/timeframe scope checks.

One FeatureDefinition per LEAF field app/models/features.py's
FeatureRecord contract already defines -- not a second,
independently-maintained feature list that could silently drift from
what the engine actually computes. get_feature_value() below reads the
EXACT same sub-model/attribute pair FeatureRecord itself uses (see
_CATEGORY_ATTR), so a field renamed in app/models/features.py without a
matching update here fails loudly (an AttributeError surfacing through
a test), never a silent None.

Every feature this engine can compute today is NUMERIC (see
FeatureValueType) -- BOOLEAN exists in this vocabulary's type system
because the condition UI must be type-aware for one ("Boolean ->
true/false", per this integration's spec) the moment one ever exists,
not because one has been added yet. A future boolean feature (e.g.
"is_regular_session") slots in by adding one FeatureDefinition entry
here with value_type=BOOLEAN -- nothing about Research's condition
builder, storage, or evaluation changes.

Note on count: this vocabulary has 31 entries (4 PRICE + 3 VOLUME + 4
VOLATILITY + 16 MARKET CONTEXT + 4 PRICE POSITION), not 25 -- that is
every leaf FeatureRecord field that genuinely exists in this codebase
today, counted exactly. This module exposes the real contract rather
than an arbitrary subset chosen to hit a round number.
"""

from enum import Enum

from pydantic import BaseModel

from app.models.features import FEATURE_CONTRACT_VERSION, FeatureRecord


class FeatureValueType(str, Enum):
    NUMERIC = "numeric"
    BOOLEAN = "boolean"


class FeatureCategory(str, Enum):
    PRICE = "price"
    VOLUME = "volume"
    VOLATILITY = "volatility"
    MARKET_CONTEXT = "market_context"
    PRICE_POSITION = "price_position"


# Requirement 4's type-aware operator sets, defined once here so both
# the API (vocabulary response) and any future validator read the same
# list rather than two hand-copied ones. "=" (single equals), not "=="
# -- see app/models/research.py::FeatureConditionOperator's own
# docstring for why this is a deliberately different vocabulary from
# the pre-existing ConditionOperator Outcome still uses.
NUMERIC_OPERATORS: tuple[str, ...] = ("<", "<=", "=", ">=", ">", "between")
BOOLEAN_OPERATORS: tuple[str, ...] = ("=",)

_OPERATORS_BY_TYPE: dict[FeatureValueType, tuple[str, ...]] = {
    FeatureValueType.NUMERIC: NUMERIC_OPERATORS,
    FeatureValueType.BOOLEAN: BOOLEAN_OPERATORS,
}


class FeatureDefinition(BaseModel):
    """One vocabulary entry -- requirement 1's five fields, plus
    `category` (which FeatureRecord sub-model this lives on -- what
    get_feature_value() below needs to actually read it) and
    `market_context_only` (True for the 16 SPY/QQQ-derived fields,
    which are None for any symbol not configured for market context at
    all -- a structural absence, not "insufficient history"; see
    FeatureRecord.market_context's own docstring)."""

    feature_id: str
    name: str
    category: FeatureCategory
    value_type: FeatureValueType
    description: str
    supported_operators: tuple[str, ...]
    version: str = FEATURE_CONTRACT_VERSION
    market_context_only: bool = False


def _feature(
    feature_id: str,
    name: str,
    category: FeatureCategory,
    description: str,
    *,
    value_type: FeatureValueType = FeatureValueType.NUMERIC,
    market_context_only: bool = False,
) -> FeatureDefinition:
    return FeatureDefinition(
        feature_id=feature_id,
        name=name,
        category=category,
        value_type=value_type,
        description=description,
        supported_operators=_OPERATORS_BY_TYPE[value_type],
        market_context_only=market_context_only,
    )


# feature_id = "{category}.{field}" -- matches the dotted convention
# frontend/src/types/features.ts::FEATURE_METRIC_LABELS already uses
# for this exact purpose (labeling a feature field), rather than
# inventing a second, competing naming scheme. Descriptions are drawn
# directly from each computation's own docstring (app/features/price.py,
# volume.py, volatility.py, market_context.py, price_position.py) --
# never re-derived or guessed.
FEATURE_VOCABULARY: list[FeatureDefinition] = [
    # ---- PRICE (app/features/price.py) ----
    _feature(
        "price.return_5m", "Return (5m)", FeatureCategory.PRICE,
        "Trailing 5-minute return ending at this bar's close: close[t] / close[t-5m] - 1. "
        "None if the 5-minutes-earlier bar is missing or not enough history exists yet.",
    ),
    _feature(
        "price.return_15m", "Return (15m)", FeatureCategory.PRICE,
        "Trailing 15-minute return ending at this bar's close. Same rule as Return (5m), 15-minute window.",
    ),
    _feature(
        "price.return_30m", "Return (30m)", FeatureCategory.PRICE,
        "Trailing 30-minute return ending at this bar's close. Same rule as Return (5m), 30-minute window.",
    ),
    _feature(
        "price.return_60m", "Return (60m)", FeatureCategory.PRICE,
        "Trailing 60-minute return ending at this bar's close. Same rule as Return (5m), 60-minute window.",
    ),
    # ---- VOLUME (app/features/volume.py) ----
    _feature(
        "volume.volume", "Volume", FeatureCategory.VOLUME,
        "This bar's own traded volume, carried straight through -- never None.",
    ),
    _feature(
        "volume.relative_volume", "Relative Volume (RVOL)", FeatureCategory.VOLUME,
        "This bar's volume divided by the historical average volume of bars at the same NY-local time-of-day, "
        "over up to 20 prior sessions. None if no historical match exists yet.",
    ),
    _feature(
        "volume.volume_acceleration", "Volume Acceleration", FeatureCategory.VOLUME,
        "This bar's volume divided by the immediately preceding bar's volume. None if the previous bar is "
        "missing or had zero volume.",
    ),
    # ---- VOLATILITY (app/features/volatility.py) ----
    _feature(
        "volatility.realized_volatility", "Realized Volatility (annualized)", FeatureCategory.VOLATILITY,
        "Trailing 20-bar sample standard deviation of log returns, annualized. None if the 20-bar window isn't "
        "fully available and contiguous.",
    ),
    _feature(
        "volatility.atr", "ATR (14-bar)", FeatureCategory.VOLATILITY,
        "Classic 14-bar Average True Range (simple mean of True Range, not Wilder-smoothed). None if the "
        "14-bar window isn't fully available and contiguous.",
    ),
    _feature(
        "volatility.volatility_ratio", "Volatility Ratio", FeatureCategory.VOLATILITY,
        "Current realized volatility divided by its own mean over the trailing 252-session history. None if "
        "either side is undefined.",
    ),
    _feature(
        "volatility.volatility_percentile", "Volatility Percentile (252-session)", FeatureCategory.VOLATILITY,
        "Percentile rank (0-1) of current realized volatility against its trailing 252-session history. None "
        "under the same conditions as Volatility Ratio.",
    ),
    # ---- MARKET CONTEXT (app/features/market_context.py) -- SPY/QQQ-eligible symbols only ----
    _feature(
        "market_context.spy_return_5m", "SPY Return (5m)", FeatureCategory.MARKET_CONTEXT,
        "SPY's own trailing 5-minute return, aligned to this bar's exact timestamp. None if no SPY bar exists "
        "at that exact timestamp.", market_context_only=True,
    ),
    _feature(
        "market_context.spy_return_15m", "SPY Return (15m)", FeatureCategory.MARKET_CONTEXT,
        "SPY's own trailing 15-minute return, aligned to this bar's exact timestamp.", market_context_only=True,
    ),
    _feature(
        "market_context.spy_return_30m", "SPY Return (30m)", FeatureCategory.MARKET_CONTEXT,
        "SPY's own trailing 30-minute return, aligned to this bar's exact timestamp.", market_context_only=True,
    ),
    _feature(
        "market_context.spy_return_60m", "SPY Return (60m)", FeatureCategory.MARKET_CONTEXT,
        "SPY's own trailing 60-minute return, aligned to this bar's exact timestamp.", market_context_only=True,
    ),
    _feature(
        "market_context.qqq_return_5m", "QQQ Return (5m)", FeatureCategory.MARKET_CONTEXT,
        "QQQ's own trailing 5-minute return, aligned to this bar's exact timestamp.", market_context_only=True,
    ),
    _feature(
        "market_context.qqq_return_15m", "QQQ Return (15m)", FeatureCategory.MARKET_CONTEXT,
        "QQQ's own trailing 15-minute return, aligned to this bar's exact timestamp.", market_context_only=True,
    ),
    _feature(
        "market_context.qqq_return_30m", "QQQ Return (30m)", FeatureCategory.MARKET_CONTEXT,
        "QQQ's own trailing 30-minute return, aligned to this bar's exact timestamp.", market_context_only=True,
    ),
    _feature(
        "market_context.qqq_return_60m", "QQQ Return (60m)", FeatureCategory.MARKET_CONTEXT,
        "QQQ's own trailing 60-minute return, aligned to this bar's exact timestamp.", market_context_only=True,
    ),
    _feature(
        "market_context.relative_strength_spy_5m", "Relative Strength vs SPY (5m)", FeatureCategory.MARKET_CONTEXT,
        "This symbol's 5m return minus SPY's 5m return at the same timestamp. None if either return is None.",
        market_context_only=True,
    ),
    _feature(
        "market_context.relative_strength_spy_15m", "Relative Strength vs SPY (15m)", FeatureCategory.MARKET_CONTEXT,
        "This symbol's 15m return minus SPY's 15m return at the same timestamp.", market_context_only=True,
    ),
    _feature(
        "market_context.relative_strength_spy_30m", "Relative Strength vs SPY (30m)", FeatureCategory.MARKET_CONTEXT,
        "This symbol's 30m return minus SPY's 30m return at the same timestamp.", market_context_only=True,
    ),
    _feature(
        "market_context.relative_strength_spy_60m", "Relative Strength vs SPY (60m)", FeatureCategory.MARKET_CONTEXT,
        "This symbol's 60m return minus SPY's 60m return at the same timestamp.", market_context_only=True,
    ),
    _feature(
        "market_context.relative_strength_qqq_5m", "Relative Strength vs QQQ (5m)", FeatureCategory.MARKET_CONTEXT,
        "This symbol's 5m return minus QQQ's 5m return at the same timestamp.", market_context_only=True,
    ),
    _feature(
        "market_context.relative_strength_qqq_15m", "Relative Strength vs QQQ (15m)", FeatureCategory.MARKET_CONTEXT,
        "This symbol's 15m return minus QQQ's 15m return at the same timestamp.", market_context_only=True,
    ),
    _feature(
        "market_context.relative_strength_qqq_30m", "Relative Strength vs QQQ (30m)", FeatureCategory.MARKET_CONTEXT,
        "This symbol's 30m return minus QQQ's 30m return at the same timestamp.", market_context_only=True,
    ),
    _feature(
        "market_context.relative_strength_qqq_60m", "Relative Strength vs QQQ (60m)", FeatureCategory.MARKET_CONTEXT,
        "This symbol's 60m return minus QQQ's 60m return at the same timestamp.", market_context_only=True,
    ),
    # ---- PRICE POSITION (app/features/price_position.py) ----
    _feature(
        "price_position.vwap_distance", "Distance from VWAP", FeatureCategory.PRICE_POSITION,
        "(close - session VWAP) / session VWAP, where VWAP is cumulative from the start of the current "
        "NY-local session through this bar. None if session volume so far is 0.",
    ),
    _feature(
        "price_position.ma20_distance", "Distance from 20-bar MA", FeatureCategory.PRICE_POSITION,
        "(close - SMA20) / SMA20. None if the trailing 20-bar window isn't fully available and contiguous.",
    ),
    _feature(
        "price_position.ma50_distance", "Distance from 50-bar MA", FeatureCategory.PRICE_POSITION,
        "(close - SMA50) / SMA50. None if the trailing 50-bar window isn't fully available and contiguous.",
    ),
    _feature(
        "price_position.intraday_range_position", "Intraday Range Position", FeatureCategory.PRICE_POSITION,
        "(close - session_low) / (session_high - session_low), over the current NY-local session's bars so "
        "far. None if the session's high equals its low (zero range).",
    ),
]

_BY_ID: dict[str, FeatureDefinition] = {f.feature_id: f for f in FEATURE_VOCABULARY}

_CATEGORY_ATTR: dict[FeatureCategory, str] = {
    FeatureCategory.PRICE: "price",
    FeatureCategory.VOLUME: "volume",
    FeatureCategory.VOLATILITY: "volatility",
    FeatureCategory.MARKET_CONTEXT: "market_context",
    FeatureCategory.PRICE_POSITION: "price_position",
}


def get_feature_vocabulary() -> list[FeatureDefinition]:
    """Every feature Research's condition builder (or any future
    consumer) can reference -- a defensive copy, so a caller mutating
    the returned list can never corrupt the module-level vocabulary."""
    return list(FEATURE_VOCABULARY)


def get_feature_definition(feature_id: str) -> FeatureDefinition:
    """Raises ValueError (not KeyError) for an unknown feature_id --
    the same "clear message, caller decides the HTTP status" convention
    every other lookup in this app follows (e.g.
    app/providers/registry.py::get_provider)."""
    try:
        return _BY_ID[feature_id]
    except KeyError:
        raise ValueError(
            f"Unknown feature_id {feature_id!r}. See GET /features/vocabulary for the supported list."
        ) from None


def get_feature_value(record: FeatureRecord, feature_id: str) -> float | int | bool | None:
    """Reads `feature_id`'s value off an already-computed FeatureRecord
    -- the ONE place Research's condition evaluator (app/research/
    conditions.py) asks "what is this feature's value here", so it
    never needs its own copy of which sub-model/attribute a feature_id
    maps to. Raises ValueError via get_feature_definition() for an
    unknown feature_id (should never happen for a feature_id that
    already passed API-route validation -- see app/api/research.py --
    but this function makes no assumption about that).

    Returns None both when the value itself is None (insufficient
    history, a missing bar, a zero denominator -- see each feature's
    own docstring above) and when `record.market_context` is None
    entirely (this symbol isn't configured for market context at all)
    -- both are "no honest value available here", which is exactly what
    None already means everywhere else in this app's condition
    evaluation (see app/research/conditions.py::evaluate_feature_conditions).
    """
    definition = get_feature_definition(feature_id)
    sub_model = getattr(record, _CATEGORY_ATTR[definition.category])
    if sub_model is None:
        return None
    return getattr(sub_model, definition.feature_id.split(".", 1)[1])
