"""Historical backfill (v0.1.22) -- pulls a deep, wide date range of bars
in one deliberate run, as opposed to app/ingestion/auto_ingest.py's job
of staying fresh with a small trailing window on a timer.

    EXTERNAL WORLD (a provider's REST API -- Alpaca, Massive, ...)
          |
          v
    fetch_normalized_bars()   app/api/historical_data.py -- the SAME
          |                    fetch+normalize path GET .../history and
          |                    auto_ingest.py both use, reused here
          |                    rather than a third, could-drift way to
          |                    ask a provider for bars
          v
    validate_bars()            app/ingestion/bar_validation.py
          |
          v
    save_validated_bars()      app/storage/historical_bar_repository.py
    save_rejected_bars()        (accepted bars / rejected-and-quarantined,
                                  same split every other ingestion path uses)

Why this is a separate module from auto_ingest.py rather than one more
knob on it: auto_ingest's whole design (re-fetch a small trailing window
every few minutes forever) is wrong for "pull two years of daily bars
once." A wide date range fetched in a single request either hits a
provider's own per-request page cap (see AlpacaProvider's `_MAX_PAGES`)
or -- for a fine-grained intraday timeframe over a long range -- would
need to. Backfilling in bounded date CHUNKS (see `_date_chunks` below)
sidesteps that regardless of provider/timeframe, gives real progress
output for a run that can take a while, and means a single failed chunk
(a rate limit, a network blip) doesn't waste every chunk fetched before
it.

Safe to interrupt and re-run from scratch: every chunk goes through the
same INSERT OR IGNORE dedup (keyed on provider+symbol+timeframe+
timestamp) every other ingestion path relies on, so a chunk that already
landed is just a fast, all-duplicates no-op the second time -- no
separate "where did I leave off" bookkeeping needed, the same reasoning
auto_ingest.py's own docstring gives for re-fetching overlapping windows.

Rate-limit handling: a 429 from the provider retries the SAME chunk with
exponential backoff (`_sleep_seconds * 2**attempt`), up to `max_retries`
times, before giving up on that chunk and moving on -- unlike
auto_ingest.py, where "the next scheduled cycle IS the retry" isn't
available here (a backfill runs once). A short pause between successful
chunks (`sleep_seconds`) is deliberate courtesy to a real account's
per-minute rate limit, not a correctness requirement.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx

from app.api.historical_data import fetch_normalized_bars
from app.ingestion.bar_validation import validate_bars
from app.models.validation import ValidationStatus
from app.storage import historical_bar_repository

logger = logging.getLogger("app.ingestion.backfill")

DEFAULT_CHUNK_DAYS = 365
DEFAULT_SLEEP_SECONDS = 0.3
DEFAULT_MAX_RETRIES = 3


@dataclass
class ChunkResult:
    """What one (symbol, timeframe, date-chunk) fetch did -- mirrors
    auto_ingest.PairResult's accounting, plus the chunk's own start/end
    since a backfill run has many chunks per symbol/timeframe where
    auto_ingest only ever has one (its fixed trailing lookback window)."""

    symbol: str
    timeframe: str
    start: date
    end: date
    fetched: int = 0
    inserted: int = 0
    skipped_duplicates: int = 0
    flagged: int = 0
    rejected: int = 0
    error: str | None = None


@dataclass
class BackfillResult:
    provider: str
    results: list[ChunkResult] = field(default_factory=list)

    @property
    def total_fetched(self) -> int:
        return sum(r.fetched for r in self.results)

    @property
    def total_inserted(self) -> int:
        return sum(r.inserted for r in self.results)

    @property
    def total_skipped_duplicates(self) -> int:
        return sum(r.skipped_duplicates for r in self.results)

    @property
    def total_rejected(self) -> int:
        return sum(r.rejected for r in self.results)

    @property
    def failed_chunks(self) -> list[ChunkResult]:
        return [r for r in self.results if r.error is not None]


def _date_chunks(start: date, end: date, chunk_days: int):
    """Splits [start, end] (both inclusive) into consecutive, non-
    overlapping windows of at most `chunk_days` each, oldest first.
    `chunk_days=365` over a 2-year range yields exactly two chunks; a
    range shorter than `chunk_days` yields exactly one. Raises on a
    non-positive `chunk_days` or start > end -- both mean the caller
    passed something that can't be chunked, not a range this function
    should silently turn into zero chunks."""
    if chunk_days <= 0:
        raise ValueError("chunk_days must be positive")
    if start > end:
        raise ValueError(f"start ({start}) must not be after end ({end})")

    current = start
    step = timedelta(days=chunk_days - 1)
    while current <= end:
        chunk_end = min(current + step, end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def run_backfill(
    *,
    symbols: list[str],
    timeframes: list[str],
    start: date,
    end: date,
    provider: str,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    on_chunk_complete=None,
    _sleep=time.sleep,
) -> BackfillResult:
    """Fetches, validates, and saves every (symbol, timeframe) pair in
    `symbols` x `timeframes` over [start, end], one bounded date chunk
    at a time. One chunk's failure (after retries) is caught and
    recorded on its own ChunkResult, never raised -- so a rate limit or
    a bad symbol partway through a multi-year, multi-symbol run doesn't
    lose progress already saved, or abort chunks still to come. Rerun
    the same call afterward to pick up only what failed or never ran;
    everything already saved is a no-op the second time (see module
    docstring).

    `on_chunk_complete`, if given, is called with each ChunkResult as
    soon as it's known -- the CLI script uses this for live progress
    output on a run that can take minutes; tests can use it to assert
    on ordering without waiting for the whole result.

    `_sleep` is injectable so tests exercise the retry/backoff path
    without a real backend/scripts run's wall-clock delay.
    """
    result = BackfillResult(provider=provider)
    for symbol in symbols:
        for timeframe in timeframes:
            for chunk_start, chunk_end in _date_chunks(start, end, chunk_days):
                chunk = _fetch_validate_save_chunk(
                    symbol=symbol,
                    timeframe=timeframe,
                    start=chunk_start,
                    end=chunk_end,
                    provider=provider,
                    max_retries=max_retries,
                    sleep_seconds=sleep_seconds,
                    _sleep=_sleep,
                )
                result.results.append(chunk)
                if on_chunk_complete is not None:
                    on_chunk_complete(chunk)
                if sleep_seconds:
                    _sleep(sleep_seconds)
    return result


def _fetch_validate_save_chunk(
    *,
    symbol: str,
    timeframe: str,
    start: date,
    end: date,
    provider: str,
    max_retries: int,
    sleep_seconds: float,
    _sleep,
) -> ChunkResult:
    chunk = ChunkResult(symbol=symbol, timeframe=timeframe, start=start, end=end)
    attempt = 0
    while True:
        try:
            bars = fetch_normalized_bars(symbol=symbol, start=start, end=end, timeframe=timeframe, provider=provider)
            chunk.fetched = len(bars)

            validation = validate_bars(bars)
            save_result = historical_bar_repository.save_validated_bars(validation.accepted)
            if validation.rejected:
                historical_bar_repository.save_rejected_bars(validation.rejected)

            chunk.inserted = save_result.inserted
            chunk.skipped_duplicates = save_result.skipped_duplicates
            chunk.flagged = sum(1 for v in validation.accepted if v.status == ValidationStatus.FLAGGED)
            chunk.rejected = len(validation.rejected)

            logger.info(
                "backfill %s %s/%s %s..%s: fetched=%d inserted=%d duplicates=%d flagged=%d rejected=%d",
                provider,
                symbol,
                timeframe,
                start,
                end,
                chunk.fetched,
                chunk.inserted,
                chunk.skipped_duplicates,
                chunk.flagged,
                chunk.rejected,
            )
            return chunk
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and attempt < max_retries:
                attempt += 1
                backoff = sleep_seconds * (2**attempt)
                logger.warning(
                    "backfill %s %s/%s %s..%s: rate limited, retry %d/%d in %.1fs",
                    provider,
                    symbol,
                    timeframe,
                    start,
                    end,
                    attempt,
                    max_retries,
                    backoff,
                )
                _sleep(backoff)
                continue
            chunk.error = str(exc)
            logger.warning("backfill %s %s/%s %s..%s failed: %s", provider, symbol, timeframe, start, end, exc)
            return chunk
        except Exception as exc:  # noqa: BLE001 -- deliberate: one chunk's failure must never abort the rest of the run, same as auto_ingest.py's per-pair isolation.
            chunk.error = str(exc)
            logger.warning("backfill %s %s/%s %s..%s failed: %s", provider, symbol, timeframe, start, end, exc)
            return chunk
