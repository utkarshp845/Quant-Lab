#!/usr/bin/env python3
"""One-time (or re-run-safe) historical backfill -- pulls a deep, wide
date range of bars for one or more symbol/timeframe pairs and saves
them through the exact same validate -> store pipeline every other
ingestion path in this app uses (app/ingestion/backfill.py).

NOT run by pytest -- like alpaca_manual_check.py / massive_manual_check.py
/ cross_validate_providers.py, this makes real network calls with real
credentials. app/ingestion/backfill.py's own orchestration logic (date
chunking, retry/backoff, per-chunk isolation) IS covered by
tests/test_backfill.py with a mocked provider; only this thin CLI
wrapper -- argument parsing and printing -- is untested, the same split
cross_validate_providers.py uses for its one pure function.

Usage:
    export ALPACA_API_KEY_ID=...
    export ALPACA_API_SECRET_KEY=...
    cd backend && ./venv/bin/python scripts/backfill_historical_data.py

    # Defaults: TSLA,NVDA, daily bars, last 2 years, MARKET_DATA_PROVIDER
    # (or "alpaca" if that's unset), one 365-day chunk per year of range.

    # Override any of it:
    ./venv/bin/python scripts/backfill_historical_data.py \\
        --symbols TSLA,NVDA --timeframes 1d,5m --years 2 --provider alpaca

    # Or an explicit date range instead of --years:
    ./venv/bin/python scripts/backfill_historical_data.py \\
        --start 2024-01-01 --end 2026-01-01

Safe to interrupt (Ctrl-C) and re-run: every bar is deduplicated by the
storage layer's UNIQUE(provider, symbol, timeframe, timestamp)
constraint, so a chunk that already landed is a fast, all-duplicates
no-op the second time -- there's no separate "resume point" to track.
Exits with a non-zero status if any chunk ultimately failed (after
retries), so a scripted/cron invocation can tell success from partial
failure; re-running is the intended way to pick up what failed.
"""

import argparse
import sys
from datetime import date, timedelta

sys.path.insert(0, __file__.rsplit("/backend/", 1)[0] + "/backend")

from app import config  # noqa: E402
from app.ingestion.backfill import DEFAULT_CHUNK_DAYS, DEFAULT_MAX_RETRIES, DEFAULT_SLEEP_SECONDS, run_backfill  # noqa: E402

DEFAULT_SYMBOLS = "TSLA,NVDA"
DEFAULT_TIMEFRAMES = "1d"
DEFAULT_YEARS = 2.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill historical OHLCV bars into backend/data/historical_bars.db.",
    )
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS, help=f"Comma-separated symbols (default: {DEFAULT_SYMBOLS})")
    parser.add_argument(
        "--timeframes",
        default=DEFAULT_TIMEFRAMES,
        help=f"Comma-separated timeframes, e.g. 1m,5m,15m,1h,1d (default: {DEFAULT_TIMEFRAMES})",
    )
    parser.add_argument("--start", type=date.fromisoformat, default=None, help="Inclusive start date, e.g. 2024-01-01 (overrides --years)")
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="Inclusive end date (default: today)")
    parser.add_argument(
        "--years",
        type=float,
        default=DEFAULT_YEARS,
        help=f"Used to compute --start when --start isn't given (default: {DEFAULT_YEARS})",
    )
    parser.add_argument("--provider", default=None, help="Defaults to MARKET_DATA_PROVIDER (e.g. alpaca, massive) -- 'csv' is rejected, see below")
    parser.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS, help=f"Days per fetch window (default: {DEFAULT_CHUNK_DAYS})")
    parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_SLEEP_SECONDS, help=f"Pause between chunks (default: {DEFAULT_SLEEP_SECONDS})")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help=f"Retries on a 429 before giving up on a chunk (default: {DEFAULT_MAX_RETRIES})")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    end = args.end or date.today()
    start = args.start or (end - timedelta(days=round(args.years * 365)))
    provider = (args.provider or config.get_configured_provider_name()).strip().lower()
    if provider == "csv":
        # csv is a real, valid MARKET_DATA_PROVIDER value (the default!)
        # for the manual-upload path, but has no get_historical_data() to
        # backfill from -- a clear error here beats a confusing
        # NotImplementedError bubbling up per-chunk, once per pair.
        print(
            "error: --provider resolved to 'csv' (MARKET_DATA_PROVIDER's default), which has no "
            "historical data to backfill. Pass --provider alpaca (or massive/schwab) explicitly.",
            file=sys.stderr,
        )
        sys.exit(2)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]

    print(f"Backfilling {symbols} x {timeframes}, {start}..{end}, via {provider!r} (chunk={args.chunk_days}d)")
    print(f"Database: {config.get_database_path()}\n")

    def _print_chunk(chunk) -> None:
        status = f"ERROR: {chunk.error}" if chunk.error else "ok"
        print(
            f"  {chunk.symbol:6s} {chunk.timeframe:4s} {chunk.start}..{chunk.end}  "
            f"fetched={chunk.fetched:<6d} inserted={chunk.inserted:<6d} "
            f"dupes={chunk.skipped_duplicates:<6d} flagged={chunk.flagged:<4d} rejected={chunk.rejected:<4d} [{status}]"
        )

    result = run_backfill(
        symbols=symbols,
        timeframes=timeframes,
        start=start,
        end=end,
        provider=provider,
        chunk_days=args.chunk_days,
        sleep_seconds=args.sleep_seconds,
        max_retries=args.max_retries,
        on_chunk_complete=_print_chunk,
    )

    print(
        f"\nTotals: fetched={result.total_fetched} inserted={result.total_inserted} "
        f"duplicates={result.total_skipped_duplicates} rejected={result.total_rejected}"
    )

    if result.failed_chunks:
        print(f"\n{len(result.failed_chunks)} chunk(s) failed:")
        for chunk in result.failed_chunks:
            print(f"  {chunk.symbol} {chunk.timeframe} {chunk.start}..{chunk.end}: {chunk.error}")
        print("\nRe-run this same command to retry -- already-saved bars are skipped as duplicates, safe to re-run.")
        sys.exit(1)


if __name__ == "__main__":
    main()
