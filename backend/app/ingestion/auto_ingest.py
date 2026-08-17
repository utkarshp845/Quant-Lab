"""Unattended, scheduled bar ingestion (v0.1.18) -- the auto-pull
alternative to a human clicking "Save to Database"
(app/api/historical_storage.py) after every manual fetch:

    EXTERNAL WORLD (a provider's REST API -- Alpaca, Massive, ...)
          |
          v
    fetch_normalized_bars()   app/api/historical_data.py -- the SAME
          |                    fetch+normalize path GET .../history uses,
          |                    reused here rather than a second,
          |                    could-drift way to ask a provider for bars
          v
    validate_bars()            app/ingestion/bar_validation.py
          |
          v
    save_validated_bars()      app/storage/historical_bar_repository.py
    save_rejected_bars()        (accepted bars / rejected-and-quarantined,
                                  same split POST .../history/save uses)

This module has exactly two public pieces: run_ingestion_cycle() (one
synchronous pass over every configured symbol/timeframe pair) and
run_ingestion_loop() (the async scheduler that calls it on a timer).
app/main.py's lifespan wires the loop to the app process's own
start/stop -- see that file.

OFF by default. See app/config.py's get_auto_ingest_enabled(): running
this means the app process makes real, scheduled, credentialed calls
to a real provider for as long as it's up, a meaningfully different
thing from every other feature here (all request-triggered) and never
something that should start just because `uvicorn` did.

One pair's failure (a rate limit, a network blip, a provider that's
briefly down, a bad symbol) is caught and logged without aborting the
rest of the cycle or killing the loop -- see run_ingestion_cycle()'s
per-pair try/except. There is no retry-with-backoff here: the next
scheduled cycle IS this module's retry, which is enough for a periodic
poller and simpler than a second, parallel retry mechanism.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from app import config
from app.api.historical_data import fetch_normalized_bars
from app.ingestion.bar_validation import validate_bars
from app.models.validation import ValidationStatus
from app.storage import historical_bar_repository

logger = logging.getLogger("app.ingestion.auto_ingest")


@dataclass
class PairResult:
    """What one cycle did for one symbol/timeframe pair -- mirrors
    SaveBarsResponse's accounting (app/models/historical_storage.py)
    plus `error`, since a pair here can fail entirely (a provider call
    raising) in a way a manual save's payload -- already-fetched bars
    in hand -- never can."""

    symbol: str
    timeframe: str
    fetched: int = 0
    inserted: int = 0
    skipped_duplicates: int = 0
    flagged: int = 0
    rejected: int = 0
    error: str | None = None


@dataclass
class IngestionCycleResult:
    provider: str
    results: list[PairResult] = field(default_factory=list)


def run_ingestion_cycle() -> IngestionCycleResult:
    """One pass over every configured symbol/timeframe pair (see
    app.config's get_auto_ingest_symbols()/get_auto_ingest_timeframes()/
    get_auto_ingest_provider()): fetch a trailing
    get_auto_ingest_lookback_days()-day window, validate, save.

    Re-fetching a window that overlaps what's already stored is
    deliberate, not wasted work: save_validated_bars()'s INSERT OR
    IGNORE (the same UNIQUE-constraint dedup a manual save already
    relies on) makes re-submitting an already-stored bar a no-op. That
    is what makes "just refetch the last few days every cycle" a safe,
    simple way to both catch each newly-closed bar and self-heal a gap
    left by a cycle that failed or a process that was down -- with no
    separate "where did I leave off" bookkeeping needed anywhere.

    Synchronous, on purpose: every provider call under
    fetch_normalized_bars() is synchronous (httpx.Client, not an async
    client -- see app/providers/*.py), so there is nothing to `await`
    in here. run_ingestion_loop() below is what makes calling this
    non-blocking for the rest of the app.
    """
    provider = config.get_auto_ingest_provider()
    symbols = config.get_auto_ingest_symbols()
    timeframes = config.get_auto_ingest_timeframes()
    lookback_days = config.get_auto_ingest_lookback_days()

    end = date.today()
    start = end - timedelta(days=lookback_days)

    cycle = IngestionCycleResult(provider=provider)
    for symbol in symbols:
        for timeframe in timeframes:
            result = PairResult(symbol=symbol, timeframe=timeframe)
            try:
                bars = fetch_normalized_bars(
                    symbol=symbol, start=start, end=end, timeframe=timeframe, provider=provider
                )
                result.fetched = len(bars)

                validation = validate_bars(bars)
                save_result = historical_bar_repository.save_validated_bars(validation.accepted)
                if validation.rejected:
                    historical_bar_repository.save_rejected_bars(validation.rejected)

                result.inserted = save_result.inserted
                result.skipped_duplicates = save_result.skipped_duplicates
                result.flagged = sum(1 for v in validation.accepted if v.status == ValidationStatus.FLAGGED)
                result.rejected = len(validation.rejected)

                logger.info(
                    "auto-ingest %s %s/%s: fetched=%d inserted=%d duplicates=%d flagged=%d rejected=%d",
                    provider,
                    symbol,
                    timeframe,
                    result.fetched,
                    result.inserted,
                    result.skipped_duplicates,
                    result.flagged,
                    result.rejected,
                )
            except Exception as exc:  # noqa: BLE001 -- deliberate: one pair's failure must never abort the cycle or crash the loop; see module docstring.
                result.error = str(exc)
                logger.warning("auto-ingest %s %s/%s failed: %s", provider, symbol, timeframe, exc)

            cycle.results.append(result)

    return cycle


async def run_ingestion_loop(*, stop_event: asyncio.Event) -> None:
    """The scheduler: runs one cycle immediately (so data starts
    flowing as soon as the app starts, not after the first full
    interval elapses), then repeats every
    config.get_auto_ingest_interval_seconds() until `stop_event` is set
    (app/main.py's lifespan sets it on shutdown, so this loop stops
    cleanly instead of being killed mid-cycle).

    run_ingestion_cycle() is synchronous, so each call runs via
    asyncio.to_thread() -- keeping the event loop free for the rest of
    the app (in particular the streaming routes in app/streaming/)
    while a provider call is in flight, the same reason
    app/storage/db.py opens a short-lived connection per call rather
    than holding one open.
    """
    interval = config.get_auto_ingest_interval_seconds()
    logger.info(
        "auto-ingest loop starting: provider=%s symbols=%s timeframes=%s interval=%ds lookback=%dd",
        config.get_auto_ingest_provider(),
        config.get_auto_ingest_symbols(),
        config.get_auto_ingest_timeframes(),
        interval,
        config.get_auto_ingest_lookback_days(),
    )
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(run_ingestion_cycle)
        except Exception:  # noqa: BLE001 -- run_ingestion_cycle() already isolates every per-pair error; this is a last-resort net so the LOOP itself survives something it didn't anticipate.
            logger.exception("auto-ingest cycle raised unexpectedly")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass  # normal case: the interval elapsed with no stop requested -- loop again.

    logger.info("auto-ingest loop stopped")
