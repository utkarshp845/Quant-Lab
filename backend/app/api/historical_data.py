"""API route for historical OHLCV bars -- GET /market-data/{symbol}/history
(v0.1.16), the first route to expose MarketDataProvider.get_historical_data()
directly rather than as a quote route's best-effort volume side-lookup
(see app/api/market_data.py). This does NOT touch the calculator, the
CSV-import pipeline, the scanner, or any quantitative calculation --
it's a new, independent read path through the same provider abstraction
those already use (see README section 13).

Narrow v1 scope, deliberately: equities only, OHLCV bars only, TSLA/NVDA
only, and only the Alpaca/Massive providers (both of which already have
real get_historical_data() integrations -- see alpaca_provider.py /
massive_provider.py). See ALLOWED_SYMBOLS below for why the symbol
restriction is enforced here rather than left to the provider or the
frontend alone. No options data, no database, no backtesting -- this is
purely "ask a provider for bars, normalize them, return them."

Exception mapping mirrors app/api/market_data.py's GET .../quote route
exactly, for the same reasons (each exception type means something
different to a caller):
  - ValueError (registry.get_provider on an unknown name)     -> 404
  - NotImplementedError (provider doesn't support historical
    data, e.g. a provider added later without it)             -> 501
  - RuntimeError (missing credentials, or a provider's own
    entitlement/rate-limit translation)                       -> 503
  - httpx.HTTPStatusError, status 429 specifically             -> 429
  - httpx.HTTPStatusError, any other status                    -> 502
No `except Exception` catch-all -- an error this route doesn't recognize
should surface as an unhandled 500 with a real traceback in the logs,
not be silently reclassified into one of the buckets above.
"""

from datetime import date

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.models.market_data import HistoricalBar, HistoricalBarsResponse
from app.providers.registry import get_provider

router = APIRouter()

# Deliberate v1 scope fence, not a technical limitation of the provider
# abstraction -- MarketDataProvider.get_historical_data() takes any
# symbol a real account is entitled to. TSLA/NVDA are the two symbols
# named for this first implementation (see the spec this route was
# built against); loosen this by editing the set, not by bypassing the
# check that reads it.
ALLOWED_SYMBOLS = {"TSLA", "NVDA"}

# The normalized timeframe vocabulary this route accepts from callers,
# translated below into each provider's own vocabulary
# (alpaca_provider.py takes Alpaca's "1Min"/"1Hour"/"1Day" strings;
# massive_provider.py takes Polygon-style (multiplier, timespan) pairs).
# Neither provider file changed to gain a shared vocabulary -- see both
# files' get_historical_data() docstrings -- the translation lives here,
# at the one caller that actually needs symbols to mean the same thing
# across providers.
ALLOWED_TIMEFRAMES = {"1m", "5m", "15m", "1h", "1d"}

_ALPACA_TIMEFRAME = {
    "1m": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "1h": "1Hour",
    "1d": "1Day",
}

_MASSIVE_TIMEFRAME = {  # (multiplier, timespan)
    "1m": (1, "minute"),
    "5m": (5, "minute"),
    "15m": (15, "minute"),
    "1h": (1, "hour"),
    "1d": (1, "day"),
}


def _provider_kwargs(provider: str, timeframe: str) -> dict:
    """Translates this route's normalized `timeframe` into whichever
    kwargs the named provider's get_historical_data() actually expects.
    A provider with no translation entry here (anything but alpaca/
    massive) gets no timeframe-related kwarg at all -- it falls back to
    that provider's own get_historical_data() default (e.g. Schwab's
    daily bars), which is honest: this route has no normalized-timeframe
    mapping for it yet, not a silent guess."""
    if provider == "alpaca":
        return {"timeframe": _ALPACA_TIMEFRAME[timeframe]}
    if provider == "massive":
        multiplier, timespan = _MASSIVE_TIMEFRAME[timeframe]
        return {"multiplier": multiplier, "timespan": timespan}
    return {}


def fetch_normalized_bars(
    *, symbol: str, start: date, end: date, timeframe: str, provider: str
) -> list[HistoricalBar]:
    """The actual fetch-and-normalize step, factored out of the route
    function so the CSV-comparison route (app/api/historical_comparison.py)
    can reuse the exact same validated path to the exact same provider
    call -- rather than a second, parallel way to ask a provider for
    bars that could quietly drift from this one. Raises the same
    exception types the route below maps to HTTP status codes; the
    caller decides how to present them."""
    symbol = symbol.upper()
    if symbol not in ALLOWED_SYMBOLS:
        raise ValueError(f"Symbol {symbol!r} is not supported yet. Allowed: {sorted(ALLOWED_SYMBOLS)}")
    if timeframe not in ALLOWED_TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe {timeframe!r}. Allowed: {sorted(ALLOWED_TIMEFRAMES)}")
    if end < start:
        raise ValueError("end date must not be before start date")

    provider_instance = get_provider(provider)  # raises ValueError for an unknown provider name
    kwargs = _provider_kwargs(provider, timeframe)
    bars = provider_instance.get_historical_data(symbol=symbol, start=start, end=end, **kwargs)
    return [HistoricalBar.from_market_bar(b, timeframe=timeframe) for b in bars]


@router.get("/market-data/{symbol}/history", response_model=HistoricalBarsResponse)
def get_historical_bars(
    symbol: str,
    start: date = Query(..., description="Inclusive start date, e.g. 2026-08-01"),
    end: date = Query(..., description="Inclusive end date, e.g. 2026-08-15"),
    timeframe: str = Query("1d", description="One of: " + ", ".join(sorted(ALLOWED_TIMEFRAMES))),
    provider: str = Query(..., description="Provider name, e.g. alpaca, massive"),
) -> HistoricalBarsResponse:
    try:
        bars = fetch_normalized_bars(symbol=symbol, start=start, end=end, timeframe=timeframe, provider=provider)
    except ValueError as exc:
        # Covers two different things that both mean "the request itself
        # is invalid": this route's own validation (bad symbol/timeframe/
        # date range) and registry.get_provider's unknown-provider-name
        # error. Both are 4xx-the-caller's-fault, but they're not the
        # same status -- an unsupported symbol/timeframe/date range is
        # 400 (bad request), an unknown provider name is 404 (not
        # found), matching GET .../quote's existing convention.
        message = str(exc)
        status_code = 404 if message.startswith("Unknown market data provider") else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail=f"{provider} rate limit exceeded: {exc.response.text or exc.response.reason_phrase}",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail=f"{provider} API request failed: {exc.response.status_code} {exc.response.reason_phrase}",
        ) from exc

    return HistoricalBarsResponse(
        symbol=symbol.upper(),
        provider=provider,
        timeframe=timeframe,
        start=start,
        end=end,
        bar_count=len(bars),
        bars=bars,
    )
