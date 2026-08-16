"""API route for live equity quotes -- the first time provider data
(Alpaca/Massive/Schwab) reaches an HTTP endpoint at all.

This route does exactly one thing: ask a named provider for its
get_latest_quote() and return it. It does NOT touch the calculator, the
CSV pipeline, or NormalizedOption in any way -- the point of the
provider abstraction (see README section 13) is that this route can
exist without either of those knowing or caring. There is deliberately
no route for get_historical_data() yet; a quote is the smallest useful
slice, and the point right now is proving the wiring works end to end,
not building a full market-data API surface.

Exception mapping is deliberately specific rather than a blanket 500,
because each exception type means something different to a caller:
  - ValueError (registry.get_provider on an unknown name)  -> 404
  - NotImplementedError (provider doesn't support quotes,
    e.g. CSVProvider inherits base.py's default)            -> 501
  - RuntimeError (missing credentials, or Massive's own
    403-entitlement translation -- see massive_provider.py)  -> 503
  - httpx.HTTPStatusError (any other upstream API failure)   -> 502
No `except Exception` catch-all -- an error this route doesn't
recognize should surface as an unhandled 500 with a real traceback in
the logs, not be silently reclassified into one of the buckets above.
"""

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.models.market_data import Quote
from app.providers.registry import get_provider

router = APIRouter()


@router.get("/market-data/{symbol}/quote", response_model=Quote)
def get_latest_quote(
    symbol: str,
    provider: str = Query(..., description="Provider name, e.g. alpaca, massive, schwab"),
) -> Quote:
    try:
        provider_instance = get_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        return provider_instance.get_latest_quote(symbol=symbol.upper())
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"{provider} API request failed: {exc.response.status_code} {exc.response.reason_phrase}",
        ) from exc
