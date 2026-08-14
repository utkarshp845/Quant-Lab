"""Maps broker-specific CSV column names onto the normalized option model.

This is deliberately its own small module: swapping in a different
export format later (a different broker, a different vendor) means
adding a new alias list here, not touching the parser or the
normalized model.

Column-name matching is case-insensitive and ignores surrounding
whitespace, so "Impl Vol", "impl vol", and " IMPL VOL " all match the
same alias.
"""

# Each normalized field name maps to a list of column-header aliases we
# recognize for it. These are educated guesses at common option-chain
# export naming (Thinkorswim-style and similar), not a verified spec --
# see the README / final report for that caveat.
COLUMN_ALIASES: dict[str, list[str]] = {
    "symbol": ["symbol", "root symbol", "underlying", "underlying symbol", "ticker"],
    "underlying_price": [
        "underlying price",
        "underlying last",
        "stock price",
        "underlying last price",
        "spot",
        "spot price",
    ],
    "expiration": ["exp date", "expiration", "exp", "expiry", "expiration date"],
    "strike": ["strike", "strike price"],
    "option_type": ["type", "put/call", "call/put", "p/c", "option type", "putcall"],
    "bid": ["bid"],
    "ask": ["ask"],
    "last": ["last", "last price", "last x"],
    "volume": ["volume", "vol"],
    "open_interest": ["open int", "open interest", "open.int", "oi"],
    "implied_volatility": ["impl vol", "implied vol", "iv", "implied volatility"],
    "delta": ["delta"],
    "gamma": ["gamma"],
    "theta": ["theta"],
    "vega": ["vega"],
}

# Fields without which we cannot build a NormalizedOption / cannot run
# a bear put spread analysis at all. Missing any of these at the
# COLUMN level (i.e. no matching header found anywhere in the file)
# is a whole-file error. "symbol" is included even though it isn't a
# math input, because without it we cannot show "available underlying
# symbols" as required, and defaulting it would violate the
# never-silently-substitute rule just as much as defaulting a price.
REQUIRED_FIELDS: list[str] = [
    "symbol",
    "underlying_price",
    "expiration",
    "strike",
    "option_type",
    "bid",
    "ask",
    "delta",
    "implied_volatility",
]

OPTIONAL_FIELDS: list[str] = ["last", "volume", "open_interest", "gamma", "theta", "vega"]


def _normalize_header(header: str) -> str:
    return " ".join(header.strip().lower().replace("%", "").split())


def detect_column_mapping(headers: list[str]) -> dict[str, str]:
    """Given a CSV's header row, return {normalized_field: actual_header}.

    Only fields with a recognized alias present in `headers` are
    included -- callers check REQUIRED_FIELDS against the result to
    decide whether the file can be imported at all.
    """
    normalized_to_actual = {_normalize_header(h): h for h in headers}

    mapping: dict[str, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized_to_actual:
                mapping[field] = normalized_to_actual[alias]
                break
    return mapping


def missing_required_columns(mapping: dict[str, str]) -> list[str]:
    return [field for field in REQUIRED_FIELDS if field not in mapping]
