# Build Log

The chronological, detailed record of every change made to Pandey Quant
Lab — newest first. `README.md` describes **current** behavior only
(and stays intentionally short); this file is where the history and the
"why" behind each step lives.

**How to keep this updated:** every time a new version ships, prepend a
new entry at the top (right after this header) using the format below.
Pull the date and PR number from `git log --merges` / `gh pr view`
rather than guessing — this file is only useful if it stays accurate.

```
## vX.Y.Z — <short title> (YYYY-MM-DD, PR #N)

What shipped, in a paragraph or two: the problem it solved, the key
design decision, and what it explicitly did NOT do if that matters.

**Files:** the handful of files/directories that matter most.
**Tests:** what was added, and the full-suite pass count at merge.
```

---

## v0.1.21 — Feature Engine v1 (2026-08-17, PR #23)

A deterministic feature-computation layer on top of `historical_bars`:
PRICE (4 trailing returns), VOLUME (volume, time-of-day-aware relative
volume, volume acceleration), VOLATILITY (realized volatility, ATR,
volatility ratio/percentile), MARKET CONTEXT (SPY/QQQ returns +
relative strength, TSLA/NVDA by default), and PRICE POSITION
(VWAP/SMA20/SMA50 distance, intraday range position) — one
`FeatureRecord` per bar, persisted to a new `historical_features` table.
No session/VWAP/volatility concept existed anywhere in this codebase
before this; built from scratch using stdlib `zoneinfo` (NY-local
calendar-day sessions) rather than a new dependency. Every trailing
window verifies exact timestamp contiguity (not just array position) so
a missing bar yields `None`, never a value computed across a gap.
Market-context eligibility is an explicit caller-supplied set, not a
hardcoded rule in the pure engine — MCL gets nothing unless configured
in.

**Files:** `app/features/` (new package), `app/models/features.py`,
`app/storage/feature_repository.py` + `historical_features` table,
`app/api/features.py`.
**Tests:** 123 new tests (10 files) — every feature calculation,
insufficient-history/missing-bar/zero-denominator/timestamp-alignment/
no-look-ahead behavior, TSLA/NVDA context, MCL exclusion, persistence,
reproducibility. Full suite: 650 passed. Caught and fixed a real bug
(`session_lookback_start_index` returning the wrong window boundary)
before it reached the volatility tests.

## v0.1.20 — Research v1 (2026-08-17, PR #22)

A hypothesis-testing engine: define a `Condition` (`"{N}m_return"` +
operator + threshold) and an `Outcome` (`"forward_return"` + horizon +
operator + threshold) as an `Experiment`, run it against
`historical_bars`, and get back every qualifying `ExperimentEvent` plus
aggregate `ExperimentResults` (success rate, average/median/min/max/
std-dev — `None`, not `0.0`, when data is insufficient). The engine is
pure computation (no I/O); the condition at each bar only ever reads
bars up to and including that bar, which is the entire no-look-ahead
guarantee. Re-running an experiment deletes and re-inserts its events
rather than appending, so results stay reproducible. Explicitly not
backtesting (no simulated P&L/position/capital) and not ML.

**Files:** `app/research/` (new package), `app/models/research.py`,
`app/storage/research_repository.py` + `experiments`/`experiment_events`
tables, `app/api/research.py`.
**Tests:** 88 new tests (5 files) — condition/outcome math, signal/event
creation, success/failure classification, aggregate stats, no-look-ahead,
date-range/symbol filtering, persistence, reproducibility. Full suite:
527 passed.

## v0.1.19 — Raw-ingestion storage (2026-08-16, PR #21)

Closed a gap a verification pass found in the shipped v0.1.18 pipeline:
normalization happened *inside* ingestion, so the original provider
JSON/CSV text was parsed and discarded, never persisted. Added a new
`raw_ingestions` table (one row per fetch/upload *request*, not per bar
— preserves each source's own field names/vocabulary, not this app's
canonical schema) and a `persist_raw_ingestion_safely()` hook at the two
places the original payload actually exists, before anything parses it.
Never raises — a raw-storage failure can't turn an otherwise-successful
fetch into a failed one. Nothing in the existing validate/normalize/
store pipeline changed.

**Files:** `app/storage/raw_ingestion_repository.py`, `raw_ingestions`
table, small hooks in `alpaca_provider.py`/`massive_provider.py`/
`historical_comparison.py`.
**Tests:** raw-row-per-request, source-specific field preservation, raw
survives even when the bar it contains is later quarantined, raw
storage is deliberately not deduplicated (unlike `historical_bars`).

## v0.1.18 — Bar validation, quarantine, and auto-ingestion (2026-08-16, PR #20)

Added an explicit validation step between normalize and store: hard
rules (impossible OHLCV values, in-batch duplicates) reject a bar to a
new `quarantined_bars` audit table; soft rules (out-of-order arrival,
unusual gaps, extreme price moves) flag a bar but still store it. Also
added an opt-in (`AUTO_INGEST_ENABLED`) background loop that re-pulls a
configured symbol/timeframe list on an interval, reusing the exact same
fetch/validate/save path a manual "Save to Database" click already
used — off by default so a fresh checkout, test run, or CI never makes
an outbound provider call.

**Files:** `app/ingestion/bar_validation.py`, `quarantined_bars` table,
`app/ingestion/auto_ingest.py`, `app/config.py` `AUTO_INGEST_*` getters,
`app/main.py` lifespan hook.

## v0.1.17 — Persistent storage for historical market data (2026-08-16, PR #19)

A SQLite-backed storage layer (`backend/data/historical_bars.db`) for
fetched historical bars: `POST /market-data/history/save` (a deliberate
manual action on bars already fetched, never a second provider call) and
`GET /market-data/{symbol}/history/stored` (reads back with no provider
contacted at all). Keyed on `(provider, symbol, timeframe, timestamp)`
since different providers can genuinely disagree on OHLCV for "the same"
bar.

**Files:** `app/storage/db.py` (new), `app/storage/historical_bar_repository.py`,
`app/api/historical_storage.py`.

## v0.1.16 — Historical market data + CSV comparison (2026-08-16, PR #18)

First route to call `get_historical_data()` directly rather than as a
quote route's volume side-lookup: `GET /market-data/{symbol}/history`
(TSLA/NVDA, 1m–1d, translated per-provider). Added a CSV-vs-provider
comparison route that diffs an uploaded bar file against a live fetch,
reporting row-count/timestamp/OHLC/volume differences without
auto-correcting either side. Fixed a real bug along the way: Massive's
pagination silently dropped its query string when `httpx` was called
with `params={}` on a URL that already had one.

**Files:** `app/api/historical_data.py`, `app/api/historical_comparison.py`,
`app/ingestion/ohlcv_csv.py`.

## v0.1.15 — Massive free-tier polling fallback (2026-08-16, PR #17)

Massive accounts without WebSocket entitlement now fall back to polling
the free-tier minute-bar REST endpoint (30s interval) instead of failing
outright — derives a quote from the latest bar (no bid/ask, since a bar
has none). Tries the WebSocket first, falls back only on a fatal
WS error.

**Files:** `app/streaming/massive_stream.py` (`MassivePollingQuoteStream`,
`MassiveStream`).

## v0.1.14 — dev.sh fixes (2026-08-16, PR #16)

Found by a real session: `--reload`'s worker child wasn't reaped on
`stop` (leaving a zombie process holding the port), and `backend/.env`
wasn't being loaded automatically. Fixed both.

**Files:** `scripts/dev.sh`.

## v0.1.13 — Real-time TSLA streaming via Massive WebSocket (2026-08-15, PR #15)

Second streaming provider, same shape as Alpaca's (v0.1.12): one
upstream WebSocket connection per symbol, `"Q"`/`"T"` message
normalization, fatal-vs-retryable auth-failure classification (a real
account confirmed `"auth_failed"` means "plan doesn't include WebSocket
access"). `StreamHub` and the reconnect/backoff loop were generalized
(`ReconnectingQuoteStream`, `QuoteStream` protocol) to be provider-
agnostic rather than Alpaca-specific.

**Files:** `app/streaming/massive_stream.py`, `app/streaming/base.py`.

## v0.1.12 — Real-time TSLA streaming via Alpaca WebSocket (2026-08-15, PR #14)

This app's first server-push route: `GET /market-data/stream`
(WebSocket) relays one upstream Alpaca connection per symbol — shared
across every connected browser tab, since Alpaca allows only one live
connection per API key — with reconnect/backoff and state replay for a
late-joining client.

**Files:** `app/streaming/alpaca_stream.py`, `app/streaming/hub.py`,
`app/api/market_data_stream.py`, `frontend/src/hooks/useQuoteStream.ts`.

## v0.1.11 — Normalized LiveQuote + standalone TSLA panel (2026-08-15, PR #13)

Flattened `Quote` (the provider contract) into `LiveQuote`, the actual
HTTP response shape (`symbol/price/bid/ask/volume/timestamp/provider`),
assembled by the route rather than a provider. Added a best-effort
second `get_historical_data()` call for volume, since neither Alpaca's
nor Massive's quote endpoints return cumulative daily volume — a
failure there never fails the quote itself. Added a standalone TSLA
`LiveQuotePanel` instance on `CalculatorPage`, independent of any CSV
import, as a minimal always-visible proof the Provider → Backend →
Normalized Data → UI pipeline actually works.

**Files:** `app/models/market_data.py` (`LiveQuote`), `app/api/market_data.py`,
`frontend/src/components/LiveQuotePanel.tsx`.

## v0.1.10 — Cross-validate Alpaca vs. Massive (2026-08-15, PR #12)

An opt-in script (`scripts/cross_validate_providers.py`, not run by
pytest) that diffs real Alpaca vs. Massive bars/quotes for TSLA/NVDA
against real accounts, to surface where two "equivalent" data sources
genuinely disagree.

**Files:** `scripts/cross_validate_providers.py`.

## v0.1.9 — Live equity quote in the UI (2026-08-15, PR #12)

First frontend use of the provider layer for live (non-CSV) data:
`GET /market-data/{symbol}/quote`, rendered next to a CSV-imported
chain's underlying price as a staleness reference — never an input the
calculator applies automatically.

**Files:** `app/api/market_data.py`, `frontend/src/components/LiveQuotePanel.tsx`.

## v0.1.8 — SchwabProvider (2026-08-15, PR #11)

Real equity historical bars/quotes via Schwab's Trader API —
architecturally the odd one out among the three providers: OAuth2, not
a static key. A 30-minute access token refreshes itself transparently;
a 7-day refresh token needs an interactive login only a human can do
(`scripts/schwab_oauth_bootstrap.py` walks through it once; re-run
roughly weekly).

**Files:** `app/providers/schwab_provider.py`, `scripts/schwab_oauth_bootstrap.py`.

## v0.1.7 — MassiveProvider fixes from real-account testing (2026-08-15, PR #10)

Bugs a real-account test run surfaced and fixed: a millisecond-vs-
nanosecond timestamp mismatch, and case-sensitivity issues in bid/ask
field parsing.

**Files:** `app/providers/massive_provider.py`.

## v0.1.6 — MassiveProvider (2026-08-15, PR #9)

Real equity historical bars/quotes via Massive's (Polygon.io) REST API,
a single `MASSIVE_API_KEY` sent as a bearer token. `get_chain()`
(options) remains a placeholder — a separate, larger integration.

**Files:** `app/providers/massive_provider.py`.

## v0.1.5 — scripts/dev.sh (2026-08-15, PR #8)

One command to start/stop/restart/status both backend and frontend dev
servers together, PID-tracked, logs under gitignored `.dev/`. A dev
convenience wrapper around the same `uvicorn --reload`/`npm run dev`
commands, not a production deployment mechanism.

**Files:** `scripts/dev.sh`.

## v0.1.4 — AlpacaProvider (2026-08-15, PR #7)

Real equity historical bars/quotes via Alpaca's Market Data API v2
(default feed `iex`, the free tier). `get_chain()` (options) remains a
placeholder.

**Files:** `app/providers/alpaca_provider.py`.

## v0.1.3 — Config-driven provider selection (2026-08-15, PR #7)

`MARKET_DATA_PROVIDER` env var + `registry.py` maps a provider name to
its class, plus placeholder Alpaca/Massive/Schwab provider classes
(`get_chain`/`get_historical_data`/`get_latest_quote` all
`NotImplementedError` at this point) — the seam v0.1.4/6/8 later filled
in with real implementations.

**Files:** `app/config.py`, `app/providers/registry.py`.

## v0.1.2 — MarketDataProvider interface + CSVProvider (2026-08-15, PR #6)

The abstraction every future data source implements:
`MarketDataProvider` (an ABC with optional capability methods) and one
normalized result shape (`NormalizedChainResult`) regardless of how the
data arrived. `CSVProvider` — a thin wrapper around the existing v0.1.1
CSV pipeline — is the first, fully working implementation, proving the
interface before any live source existed.

**Files:** `app/providers/base.py`, `app/providers/csv_provider.py`.

## v0.1.1 — CSV market data import (2026-08-14, PR #2)

A second way to fill in the calculator's inputs besides typing them by
hand: upload a CSV export of an options chain, pick a long/short put
from a table, and get an instant client-side Spread Builder preview.
Includes a small in-file scanner (every valid long/short combination,
filterable by DTE/delta/max loss). No live data — nothing is fetched
automatically; the file never leaves your machine except to your own
local backend. No financial formula is duplicated: the CSV path
produces a normal `BearPutSpreadRequest` posted to the same endpoint
manual entry uses (proven identical by
`test_csv_import_api.py::test_csv_derived_request_matches_manual_entry`).

**Files:** `app/ingestion/` (new package), `app/models/option_chain.py`,
`app/api/csv_import.py`, `frontend/src/components/CsvImportWorkflow.tsx`.

## v0.1 — Initial release: transparent bear put spread calculator (2026-08-14, PR #1)

The foundation: manually-entered bear put spread analysis (debit — both
Mid and Conservative Entry conventions — max loss/profit, breakeven,
spread delta, average IV, expected move, breakeven probability),
Phase 2's closed-form probability engine (bucketed distribution +
Expected Value, probabilities proven to sum to exactly 1.0), and
Phase 3's Monte Carlo simulation (up to 100,000 simulated paths through
the identical payoff formulas, as a cross-check against Phase 2's exact
answer). Every formula is visible in the UI, not hidden behind a single
"Calculate" button — the principle every later version was built to
preserve.

**Files:** `app/calculations/`, `app/models/bear_put_spread.py`,
`app/models/response.py`, `frontend/src/calculations/`,
`frontend/src/components/` (calculator + probability/Monte Carlo
sections).
