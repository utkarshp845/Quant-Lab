"""Small, single-purpose parsers for turning raw CSV cell text into
typed values. Each function returns None (never raises, never
fabricates a value) when the cell is empty or unparseable, so the
normalizer can decide how to react -- see csv_normalizer.py.
"""

import re
from datetime import date, datetime

_BLANK_TOKENS = {"", "n/a", "na", "--", "-", "null", "none"}

# ISO first, then common US and broker-export-ish formats. Tried in
# order; first one that parses wins.
_DATE_FORMATS = [
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%b %d %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %y",
    "%d %b %Y",
]

# Thinkorswim-style expirations sometimes look like "18 SEP 26 (35)",
# bundling the already-computed DTE in parentheses. Strip that suffix
# before trying the date formats above.
_PAREN_SUFFIX_RE = re.compile(r"\s*\(\s*-?\d+\s*\)\s*$")


def parse_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.strip().replace("$", "").replace(",", "").replace("%", "").strip()
    if cleaned.lower() in _BLANK_TOKENS:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_int(raw: str | None) -> int | None:
    value = parse_float(raw)
    if value is None:
        return None
    return int(value)


def parse_iv(raw: str | None) -> float | None:
    """Parses implied volatility into a decimal fraction (0.44 = 44%).

    Heuristic: a literal "%" in the source always means "divide by
    100". Without one, a value greater than 1.5 is assumed to already
    be on a 0-100-ish percentage scale (e.g. "44.00") and is divided
    by 100; a value at or below 1.5 is assumed to already be a decimal
    fraction (e.g. "0.44"). This threshold is a documented assumption,
    not a guarantee -- see the README/report for the caveat.
    """
    if raw is None:
        return None
    had_percent_sign = "%" in raw
    value = parse_float(raw)
    if value is None:
        return None
    if had_percent_sign or value > 1.5:
        return value / 100.0
    return value


def parse_option_type(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = raw.strip().lower()
    if cleaned in _BLANK_TOKENS:
        return None
    if cleaned in ("c", "call", "calls"):
        return "call"
    if cleaned in ("p", "put", "puts"):
        return "put"
    return None


def parse_expiration_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    cleaned = raw.strip()
    if cleaned.lower() in _BLANK_TOKENS:
        return None
    cleaned = _PAREN_SUFFIX_RE.sub("", cleaned).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None
