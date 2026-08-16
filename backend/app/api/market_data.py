"""API route for live equity quotes -- the first time provider data
(Alpaca/Massive/Schwab) reaches an HTTP endpoint at all.

This route asks a named provider for its get_latest_quote(), then makes
a second, best-effort call to get_historical_data() to fill in the most
recent session's volume (neither Alpaca's nor Massive's quote/trade
endpoints return cumulative daily volume -- see LiveQuote's docstring),
and returns the combined, flat LiveQuote shape the frontend consumes.
It does NOT touch
the calculator, the CSV pipeline, or NormalizedOption in any way -- the
point of the provider abstraction (see README section 13) is that this
route can exist without either of those knowing or caring. There is
deliberately no *route* for get_historical_data() on its own yet; this
endpoint's use of it is purely internal, to enrich a quote.

Exception mapping for the primary get_latest_quote() call is
deliberately specific rather than a blanket 500, because each exception
type means something different to a caller:
  - ValueError (registry.get_provider on an unknown name)  -> 404
  - NotImplementedError (provider doesn't support quotes,
    e.g. CSVProvider inherits base.py's default)            -> 501
  - RuntimeError (missing credentials, or Massive's own
    403-entitlement translation -- see massive_provider.py)  -> 503
  - httpx.HTTPStatusError (any other upstream API failure)   -> 502
No `except Exception` catch-all there -- an error this route doesn't
recognize should surface as an unhandled 500 with a real traceback in
the logs, not be silently reclassified into one of the buckets above.

The volume-enrichment call is the one deliberate exception to that
rule: it wraps ANY exception, broadly, on purpose. Volume was always
"if available" in this endpoint's contract (see LiveQuote), so a
provider that doesn't implement get_historical_data (a NotImplementedError),
one that's missing separate credentials for it, or a transient upstream
failure must never turn a successful quote into a failed request --
volume just comes back None, honestly, rather than the whole response
being lost over an optional field.
"""

from datetime import date, timedelta

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.models.market_data import LiveQuote
from app.providers.registry import get_provider

router = APIRouter()

# How far back to look for the most recent completed session's bar.
# Confirmed empirically, not assumed: querying strictly start=end=today
# returns an empty list on a weekend (no session has happened yet
# today), which would make volume look "unavailable" every Saturday/
# Sunday even though a live quote itself still works fine. A trailing
# window and taking the LAST bar returned gives the most recent
# completed session's volume instead -- still honest (MarketBar's own
# timestamp says exactly which day it's from), just not artificially
# blank on non-trading days. 5 days comfortably covers any weekend or
# single-day holiday.
_VOLUME_LOOKBACK_DAYS = 5


def _best_effort_latest_volume(provider_instance, symbol: str) -> int | None:
    """Never raises. Returns None if no recent volume can be determined
    for any reason -- see this module's docstring for why that's the
    deliberate, sole exception to "don't catch bare Exception" here."""
    try:
        end = date.today()
        start = end - timedelta(days=_VOLUME_LOOKBACK_DAYS)
        bars = provider_instance.get_historical_data(symbol=symbol, start=start, end=end)
        return bars[-1].volume if bars else None
    except Exception:
        return None


@router.get("/market-data/{symbol}/quote", response_model=LiveQuote)
def get_latest_quote(
    symbol: str,
    provider: str = Query(..., description="Provider name, e.g. alpaca, massive, schwab"),
) -> LiveQuote:
    try:
        provider_instance = get_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    symbol = symbol.upper()
    try:
        quote = provider_instance.get_latest_quote(symbol=symbol)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"{provider} API request failed: {exc.response.status_code} {exc.response.reason_phrase}",
        ) from exc

    return LiveQuote(
        symbol=quote.symbol,
        price=quote.last,
        bid=quote.bid,
        ask=quote.ask,
        volume=_best_effort_latest_volume(provider_instance, symbol),
        timestamp=quote.timestamp.value,
        provider=quote.timestamp.source,
    )
