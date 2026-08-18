# Pandey Quant Lab

A transparent, educational quant toolkit: a **bear put spread calculator**
where every number traces back to a visible formula, plus a **market-data
pipeline** (live quotes, streaming, historical bars) and a **research
layer** (hypothesis testing + feature computation) built on top of it.

**This is not a trading bot.** No brokerage connection, no order
execution, no autonomous buy/sell recommendations, no user accounts. See
[10. What is intentionally NOT implemented](#10-what-is-intentionally-not-implemented-yet)
and [`BUILD_LOG.md`](BUILD_LOG.md) for the full change history. Trying
to *do* something (fetch data, create an experiment, run a backtest)?
See [`USER_GUIDE.md`](USER_GUIDE.md) instead — this file explains how
the app is built, that one walks you through using it.

---

## 1. What it does today

**Calculator** — type in an underlying and two put legs (or import a CSV
option chain and pick two rows) and get: debit (mid + a conservative
execution-check variant), max loss/profit, breakeven, spread delta,
average IV, expected move, a probability-of-below-breakeven estimate, a
full bucketed probability distribution with Expected Value, and a
100,000-path Monte Carlo simulation — every one with its formula shown,
not hidden behind a button. See [4. How the mathematics works](#4-how-the-mathematics-works).

**Market data** — a provider-agnostic layer (`MarketDataProvider`) with
real implementations for **Alpaca**, **Massive** (Polygon.io), **Schwab**
(OAuth2), and CSV import, each returning the same normalized shapes
regardless of source. Live quotes, WebSocket streaming (with a REST
polling fallback for accounts without streaming entitlement), and
historical OHLCV bars (TSLA/NVDA, 1m–1d) are all live in the UI as
side-by-side references — never inputs the calculator auto-applies. See
[8. Market data provider architecture](#8-market-data-provider-architecture).

**Historical data pipeline** — fetched bars are validated (impossible
OHLCV values and duplicates rejected to a quarantine table; soft
anomalies flagged but kept), persisted to SQLite, and the original
provider/CSV payload is preserved *before* any parsing touches it (a
separate raw-ingestion audit table). An opt-in background loop can keep
pulling bars on a schedule instead of a manual "Save to Database" click.
See [9](#9-historical-market-data-pipeline)/[12](#12-historical-bar-storage).

**Research v1** — define a falsifiable hypothesis as one or more
FeatureConditions (ANDed, each referencing a real Feature Engine value
— e.g. "VWAP distance > 0 AND volatility percentile between 0.5 and 0.7
AND relative volume > 1.5") and a forward-return outcome, run it
against already-computed feature data, and get back every individual
qualifying signal plus aggregate statistics. Deterministic, reproducible,
never modifies historical data. Not backtesting, not ML. See
[15. Research v1](#15-research-v1).

**Feature Engine v1** — transforms normalized bars into a fixed set of
timestamped PRICE/VOLUME/VOLATILITY/MARKET CONTEXT/PRICE POSITION values
(31 leaf features total), persisted for Research (or any future
consumer) to read rather than recompute, and exposed as a canonical
vocabulary Research's condition builder populates itself from. See
[16. Feature Engine v1](#16-feature-engine-v1).

**Backtesting v1** — select an existing Research experiment and walk its
already-persisted bars/features strictly chronologically: when the
experiment's conditions become true at bar `t`, enter at bar `t+1`'s
open (never bar `t`'s own close) and measure forward return/MFE/MAE at
several configurable bar-count horizons (5/15/30/60 by default). Every
individual signal is persisted, fully inspectable, alongside aggregate
statistics per horizon. Answers exactly one question — "when this
research condition occurred historically, what happened afterward?" —
still no simulated P&L, position sizing, or capital tracking. See
[17. Backtesting v1](#17-backtesting-v1).

## 2. Install and run

Backend (Python 3.11+; developed on 3.13):

```bash
cd backend
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/uvicorn app.main:app --reload --port 8000
```

Frontend (Node 18+):

```bash
cd frontend
npm install
npm run dev
```

Or both together: `./scripts/dev.sh start|status|stop|restart` (PIDs/logs
in the gitignored `.dev/`). Backend: `http://localhost:8000` (Swagger UI
at `/docs`). Frontend: `http://localhost:5173`, expects the backend at
`:8000` (`frontend/src/api/client.ts`).

Backend tests: `cd backend && ./venv/bin/pytest` — 902+ tests, all
against synthetic/mocked data, no live network calls or real credentials
required.

## 3. Example inputs and outputs

The calculator is pre-populated on load with this hypothetical setup:

| | Underlying | Long Put (BUY) | Short Put (SELL) |
|---|---|---|---|
| Price / Strike | $82.00 | 85 | 77 |
| Bid / Ask | — | $5.00 / $5.10 | $0.71 / $0.85 |
| Delta / IV | — | -0.58 / 44% | -0.29 / 42% |
| DTE | 30 | | |

Expected output (primary, Mid Debit — drives everything below):

| Metric | Value |
|---|---|
| Mid Debit | $4.27/share ($427/contract) |
| Max Loss / Max Profit | $427 / $373 |
| Breakeven | $80.73 |
| Spread Delta | -0.29 |
| Expected Move | ≈ ±$10.11 |
| Probability below breakeven | ≈ 45.0% |
| Expected Value (closed-form and Monte Carlo) | ≈ -$57.77/contract |

A second "Execution Reality Check" panel repeats these using a
**Conservative Entry Debit** (buy at ask, sell at bid — $4.39/share,
Max Loss $439, Max Profit $361, Breakeven $80.61, $12 slippage vs. mid).
These exact numbers are encoded as tests —
`backend/tests/test_calculations.py::TestGraduationExampleMidDebit` /
`TestGraduationExampleConservativeDebit`, plus the probability/Monte
Carlo/API test files.

## 4. How the mathematics works

A **bear put spread** buys a put at a higher strike and sells one at a
lower strike, same expiration; it profits if the underlying falls.

```
Mid Debit          = Long Put Mid − Short Put Mid        (PRIMARY -- drives everything below)
Conservative Debit = Long Put Ask − Short Put Bid         (execution-cost check only)

Max Loss    = Mid Debit × 100
Max Profit  = (Strike Width − Mid Debit) × 100
Breakeven   = Long Strike − Mid Debit
Spread Delta = Long Put Delta − Short Put Delta
Average IV   = (Long IV + Short IV) / 2
Expected Move = Underlying Price × Average IV × sqrt(DTE / 365)
z            = (Breakeven − Underlying Price) / Expected Move
P(below breakeven) = NormalCDF(z)                         -- math.erf, exact
```

Both debit conventions use the identical formula
(`backend/app/calculations/bear_put_spread.py::debit_per_share`) fed
different prices; only Mid Debit feeds Max Loss/Profit/Breakeven/the
probability engine/Monte Carlo. The frontend mirrors every formula in
TypeScript (`frontend/src/calculations/`), including a rational
`normalCdf` approximation (Abramowitz & Stegun, error ≤1.5×10⁻⁷) since
JS has no built-in `erf`.

**Probability engine** (`app/calculations/probability_distribution.py`):
splits the price axis into buckets (two open-ended tails so probabilities
sum to exactly 1.0), gives each an exact area-under-the-normal-curve
probability, attaches a P/L via the same payoff formula, and combines
into an Expected Value — **≈ -$57.77/contract** for the example above,
illustrating that a 45% win probability doesn't imply positive EV when
the payoff is asymmetric (capped +$373 vs. -$427).

**Monte Carlo** (`app/calculations/monte_carlo.py`): draws up to 100,000
random expiration prices from the identical model and runs each through
the same payoff formulas (numpy-vectorized) to report probability of
profit/max-loss/max-profit, Expected Value/Return, median, percentile
bands, and expected gain/loss. For a plain normal distribution this is
strictly noisier than the closed-form answer — its value is as a
cross-check (both should converge) and as the machinery a future
non-normal distribution would actually need. Every simulated number is
labeled *"...of N simulated outcomes..."*, never stated as a real-world
probability.

## 5. Financial assumptions and disclaimers

- Educational tool. **Not financial advice.**
- Ignores commissions/fees; assumes a simplified, symmetric, zero-drift
  normal price distribution; does not model volatility skew/smile, early
  exercise, or assignment.
- Every probability/EV number is a *model output* under stated
  assumptions, never presented as a real-world edge or forecast.
- No trade execution, no brokerage connection, no autonomous trading
  decisions, at any point in this project.

## 6. What's implemented vs. not

**Implemented:** the transparent calculator (debit/max-loss/profit/
breakeven/delta/IV/expected-move/probability), the closed-form
probability engine, Monte Carlo simulation, CSV option-chain import with
an in-file long/short scanner, a swappable live/historical market-data
provider layer (Alpaca/Massive/Schwab/CSV), real-time streaming quotes,
historical-bar fetch/validate/quarantine/store/raw-audit, an opt-in
unattended ingestion loop, a deep-range historical backfill script,
Research v1 (hypothesis testing over feature-based, AND-combined
conditions) integrated with Feature Engine v1 (deterministic feature
computation exposed as a canonical vocabulary), Backtesting v1
(event-based historical walk over an existing Research experiment:
next-bar-open entry, multi-horizon forward return/MFE/MAE, every signal
persisted and inspectable), Statistical Validation v1/v2 (dependence-
aware significance testing of a backtest's results against an
unconditional baseline), and the OOS / Holdout Partition Framework v1
(explicit development-vs-holdout dataset partitioning, with development
access unrestricted and holdout access gated behind an explicit
confirmation flag — see §20; the partitioning/provenance boundary
only, no OOS statistical test reads a holdout window through it yet),
Experiment Freeze & Provenance v1 (§21 — an explicit DRAFT → FROZEN →
OOS_EVALUATED → ARCHIVED lifecycle for a Research Experiment, a
deterministic `hypothesis_hash` and immutable snapshot captured at
freeze time, and validated linkage to an OOS partition), and OOS
Evaluation v1 (§22 — the actual OOS-evaluation operation: given a
FROZEN experiment linked to a partition, evaluates its frozen
hypothesis against ONLY the partition's holdout data via
`get_holdout_bars(..., confirm_oos_validation_use=True)`, orchestrating
the existing Feature Engine and Backtesting v1 engine unmodified, and
persists an immutable, append-only result).

**Not implemented, deliberately:** machine learning, authentication/user
accounts, portfolio management, historical **options** data (equity bars
only), a market-wide scanner across live symbols, a trade journal,
**simulated P&L/position sizing/capital tracking over time** (Backtesting
v1 measures forward return/MFE/MAE per signal — it does not size a
position, hold a book, or track capital across trades), model
calibration tracking, signal generation, paper trading, and live
execution. None of these is scheduled work — each is a real future
possibility gated behind the one before it (a trade journal needs
persistent storage of real outcomes before model calibration is even
askable; calibration needs to hold up before signal generation is
meaningful; signals need a paper-trading track record before live
execution is ever a live discussion) — but this repo does not commit to
a timeline for any of it. Automated buy/sell recommendations are
**permanently** out of scope at every one of those phases, including any
of them that does get built — the tool's job stays "here's what matches
your criteria," never "buy this." Live execution of a real trade with
real capital, if it is ever built, is never something this assistant
performs on your behalf — that action stays yours, deliberately, one
trade at a time.

## 7. Importing options data from CSV

Upload a CSV export of an option chain, pick a long/short put from a
table, and either get an instant client-side Spread Builder preview or
click "Analyze Spread" to populate the same manual-entry form the rest
of the app uses (byte-for-byte identical API response either way — see
`test_csv_import_api.py::test_csv_derived_request_matches_manual_entry`).
No live data, no network call beyond reading the file. Rows normalize
into one broker-agnostic shape (`app/models/option_chain.py`); required
columns (symbol/price/expiration/strike/type/bid/ask/delta/IV) are
matched case-insensitively via an alias list and the whole file is
rejected with a specific message if any are missing — never silently
defaulted. A small in-file scanner computes every valid long/short
combination and lets you filter by DTE/delta/max loss.

## 8. Market data provider architecture

```
                  +-- CSVProvider      (options chain, from an uploaded file)
MarketDataProvider+-- AlpacaProvider   (equity bars/quotes; options chain placeholder)
                  +-- MassiveProvider  (equity bars/quotes; options chain placeholder)
                  +-- SchwabProvider   (equity bars/quotes, OAuth2; options chain placeholder)
                          |
                          v
                 NormalizedChainResult / HistoricalBar / Quote
                          |
                          v
                    calculator, storage, research, features
```

Every provider implements the same interface
(`app/providers/base.py::MarketDataProvider`) and returns the same
normalized shapes — picking one is a config change
(`MARKET_DATA_PROVIDER` env var / `registry.py`), never a code change
downstream. Alpaca and Massive use a static API key; **Schwab uses
OAuth2** — a 30-minute access token it refreshes itself, and a 7-day
refresh token that requires an interactive login only you can do
(`scripts/schwab_oauth_bootstrap.py` walks through it; re-run weekly).
None of the three has real options-chain data yet (`get_chain()` is a
placeholder) — only equity bars/quotes are real. Live quotes
(`GET /market-data/{symbol}/quote`) and real-time WebSocket streaming
(`GET /market-data/stream`, Alpaca + Massive, with a REST-polling
fallback for Massive accounts not entitled to the WebSocket) render as
side-by-side reference panels — never an input the calculator applies
automatically.

## 9. Historical market data pipeline

```
Alpaca / Massive / CSV upload
        |
        v
persist_raw_ingestion_safely()   -- raw_ingestions table, BEFORE parsing (never raises)
        |
        v
   normalize -> HistoricalBar
        |
        v
   validate_bars()
   /              \
save_validated_bars()      save_rejected_bars()
-> historical_bars           -> quarantined_bars
```

**Fetch:** `GET /market-data/{symbol}/history` — TSLA/NVDA only, 1m
through 1d, translated per-provider (Alpaca `"5Min"`, Massive
`(5, "minute")`, ...). A CSV-vs-provider comparison route diffs an
uploaded bar file against a live fetch without auto-correcting either
side.

**Validate:** hard rules (impossible OHLCV, in-batch duplicates) reject a
bar to the append-only `quarantined_bars` audit table; soft rules
(out-of-order arrival, unusual gaps, extreme moves) flag but still store
the bar.

**Store:** `historical_bars`, keyed on `(provider, symbol, timeframe,
timestamp)` since different providers can genuinely disagree on OHLCV
for "the same" bar — `POST /market-data/history/save` /
`GET .../history/stored`, a deliberate manual action, never an automatic
side effect of fetching.

**Raw audit:** the *original* provider JSON / CSV text is persisted
(`raw_ingestions`, one row per fetch/upload request, not per bar) before
any parsing happens — answers "what did the source literally give us"
even for a record later quarantined.

**Auto-ingest:** an opt-in (`AUTO_INGEST_ENABLED=true`) background loop
re-pulls a configured symbol/timeframe list on an interval, reusing the
exact same fetch → validate → store path a manual click uses — off by
default so a fresh checkout, test run, or CI never makes an outbound
call. Built for *staying fresh* (a small trailing window, re-fetched
every few minutes), not for building up deep history from nothing.
Enabling it means the app process makes real, credentialed calls for as
long as it's running, including every `--reload` restart — enable it in
the `.env` a persistent server actually loads, not a throwaway
dev/testing checkout. A pair that fails several cycles in a row (not
just one transient blip) escalates from a routine WARNING to one ERROR
log line (v0.1.23, `AUTO_INGEST_FAILURE_ALERT_THRESHOLD`, default 3
consecutive cycles), and logs one INFO line the first cycle it recovers
— so a genuinely stale credential doesn't fail silently forever at the
same log level as a five-minute network blip.

**Backfill:** `scripts/backfill_historical_data.py` (v0.1.22,
`app/ingestion/backfill.py`) is the deep-history counterpart — a
one-time (or re-run-any-time) pull of a wide date range, split into
bounded chunks, through the identical fetch → validate → store
pipeline, with 429 retry/backoff and per-chunk failure isolation so
one bad chunk doesn't lose an otherwise-long run:

```bash
export ALPACA_API_KEY_ID=...
export ALPACA_API_SECRET_KEY=...
cd backend && ./venv/bin/python scripts/backfill_historical_data.py --provider alpaca
# defaults: TSLA,NVDA, daily bars, last 2 years -- see --help for every flag
```

Safe to interrupt and re-run: already-saved bars are deduplicated by
the same `UNIQUE(provider, symbol, timeframe, timestamp)` constraint
every other ingestion path relies on.

## 10. What is intentionally NOT implemented yet

See [6. What's implemented vs. not](#6-whats-implemented-vs-not) for the
current summary. In more detail: Research v1, Feature Engine v1, and
Backtesting v1 (sections 15–17) measure, compute, and walk forward over
*inputs* — Backtesting v1 answers "what happened after this condition
historically," not "what would my account balance be": simulated
position sizing, a held book, and capital tracking over time still do
not exist anywhere in this codebase. Historical **options** data, a live
market-wide scanner, and a trade journal are also not implemented.
Automated buy/sell recommendations are permanently out of scope, at
every phase, including any future paper-trading or signal-generation
work — the tool's job stays "these match your criteria," never "buy
this." Live execution of a real trade is not planned and, regardless of
what the codebase might someday support, this assistant will not place a
real trade or move real capital on your behalf, ever. OOS Evaluation v1
(section 22) runs exactly one deterministic backtest of an
already-frozen hypothesis against holdout data — it does not optimize
a threshold, search parameters, apply ML, construct a strategy, or
paper-trade; strategy optimization/parameter search/ML/strategy
construction/paper trading over holdout data remain explicitly out of
scope for every OOS-related feature in this codebase.

## 11. Real-time streaming

`GET /market-data/stream` (WebSocket) relays live quotes from one
upstream connection per `(provider, symbol)` pair — shared across every
connected browser tab, since each provider allows only one live
connection per API key — with automatic reconnect/backoff and state
replay for a client that joins mid-stream. Alpaca streams natively over
its own WebSocket; Massive falls back to REST polling (30s) for accounts
without WebSocket entitlement, since a polled bar has no bid/ask, only a
derived quote. `frontend/src/hooks/useQuoteStream.ts` owns the
browser↔backend socket independently of the backend's own reconnect
logic to the upstream provider.

## 12. Historical bar storage

SQLite (`backend/data/historical_bars.db`, `DATABASE_PATH` env var),
one connection opened and closed per call — a real relational database
with a real `UNIQUE(provider, symbol, timeframe, timestamp)` constraint,
not a settings-library-style abstraction, since this is a single-user,
no-auth local tool with no meaningful connection-pooling need. See
[9. Historical market data pipeline](#9-historical-market-data-pipeline)
above for the full fetch → validate → store → audit flow, and
[15](#15-research-v1)/[16](#16-feature-engine-v1) below for the two
consumers built on top of it.

## 13. Testing

`cd backend && ./venv/bin/pytest` — 1072+ deterministic tests, all against
synthetic fixtures or mocked HTTP, no live provider credentials or
network access required anywhere in the suite. Manual, opt-in scripts
that DO hit real provider APIs with your own credentials
(`scripts/alpaca_manual_check.py`, `massive_manual_check.py`,
`cross_validate_providers.py`) are never run by pytest.

## 14. Project structure

```
backend/
  app/
    main.py             FastAPI app, CORS, router mounts, auto-ingest lifespan
    config.py            The one os.environ touchpoint (provider creds, DB path, auto-ingest settings)
    models/               Pydantic request/response + domain shapes, one file per concern
    calculations/          Pure math: bear put spread, probability distribution, Monte Carlo, stats
    ingestion/              CSV parsing + normalization + bar validation
    providers/               MarketDataProvider + Alpaca/Massive/Schwab/CSV implementations
    storage/                 SQLite schema + one repository per table family (bars, raw, research, features, backtests, oos partitions)
    streaming/                 Reconnecting WebSocket streams + the per-(provider,symbol) hub
    research/                   Research v1 -- pure condition/outcome engine (see section 15)
    features/                   Feature Engine v1 -- pure feature computation (see section 16)
    backtesting/                 Backtesting v1 -- pure chronological walk + aggregation (see section 17)
    statistical_validation/      Statistical Validation v1/v2 -- dependence-aware significance testing (see section 18/19)
    oos/                          OOS / Holdout Partition Framework v1 -- classification + access boundary (see section 20)
    research/lifecycle.py          Experiment Freeze & Provenance v1 -- transitions, hash, partition linkage (see section 21)
    oos_evaluation/                OOS Evaluation v1 -- warm-up + orchestration of the frozen hypothesis vs. holdout data (see section 22)
    api/                         One router per route group, mounted in main.py
  scripts/                Manual/opt-in real-API checks, Schwab OAuth bootstrap, dev.sh
  tests/                  One test file per module above, all deterministic/offline
frontend/
  src/
    types/                TS mirrors of backend schemas
    calculations/          TS mirror of the backend formulas (bearPutSpread.ts, incl. normalCdf)
    utils/                 CSV-selection -> form state, the in-file scanner
    api/client.ts          Fetch wrapper; derives ws:// URL from the same origin as http(s)
    hooks/useQuoteStream.ts  Owns the browser<->backend WebSocket + its own reconnect/backoff
    components/             One component per UI section (calculator, CSV import, live quote/stream, historical data)
    pages/CalculatorPage.tsx  Composes everything, owns form state
scripts/dev.sh          start/stop/restart/status for backend + frontend together
```

For exactly which file changed in which version and why, see
[`BUILD_LOG.md`](BUILD_LOG.md) — that file is the detailed,
chronological record; this README describes current behavior only.

## 15. Research v1

A hypothesis-testing engine on top of already-computed Feature Engine
data: define one or more `FeatureCondition`s (`feature_id`/`operator`/
`value(/value_max)`, AND-combined — `feature_id` references an entry in
[16](#16-feature-engine-v1)'s vocabulary; operators are `< <= = >= >
between` for a numeric feature, `=` only for a boolean one) and an
`Outcome` (`"forward_return"`, `horizon_minutes`, `operator`,
`threshold` — unchanged, still evaluated against raw bars, since
measuring "what happened after the signal" was never a Feature Engine
concern) as an `Experiment`, run it, and get back every individual
qualifying `ExperimentEvent` (with every fired condition's own observed
value, `condition_values`) plus aggregate `ExperimentResults` (success
rate, average/median/min/max/std-dev of the outcome — `None`, never a
fabricated `0.0`, when there isn't enough data).

```
historical_features (already computed, [16]) -> app/research/engine.py::run_experiment() -> ExperimentEvent[] + ExperimentResults
                                                          ^
                                            historical_bars (signal price + forward_return only)
```

The engine (`app/research/`) is pure computation, no I/O, and never
calls `app/features/engine.py` — "do not recalculate features inside
Research" is a hard boundary: it reads only already-persisted
`FeatureRecord`s (matched to each bar by exact timestamp), never
recomputes one. `Experiment.feature_contract_version` (captured once at
creation) is the reproducibility guarantee: a run only evaluates
against FeatureRecords whose own contract version matches, so a future
feature-formula change can never silently alter what an already-created
experiment measures — bumping the Feature Engine's contract just makes
old experiments find no data until duplicated against the new one.
Re-running an experiment (`POST /research/experiments/{id}/run`) deletes
and re-inserts its events rather than appending, so results stay
reproducible against an unchanged dataset. Routes:
`POST /research/experiments` (create), `GET /research/experiments`
(list), `GET .../{id}` (retrieve), `GET .../{id}/events` (every
individual signal, not just the aggregate), `POST .../{id}/run`.

**Not implemented, on purpose:** OR/nested condition groups (AND only),
simulated P&L/position/capital (backtesting), ML/parameter optimization,
paper trading, multi-symbol experiments.

**Tests:** `tests/test_research_*.py` + `tests/test_feature_vocabulary.py`
(150+ tests) — vocabulary loading/lookup, condition validation
(between-shape, boolean-vs-numeric operators), multi-condition AND
evaluation, feature-contract-version reproducibility, event creation,
success/failure classification, aggregate stats (incl. explicit
zero/one-observation handling), no-look-ahead, date-range/symbol
filtering, persistence, and reproducibility.

## 16. Feature Engine v1

A deterministic feature-computation layer on top of the same
`historical_bars` dataset — transforms each bar into a fixed
`historical_features` record for Research (or any future consumer) to
read rather than recompute:

- **PRICE:** `return_5m/15m/30m/60m`
- **VOLUME:** `volume`, `relative_volume` (time-of-day-aware historical
  baseline), `volume_acceleration`
- **VOLATILITY:** `realized_volatility` (20-bar rolling log-return
  stdev, annualized), `atr` (14-bar), `volatility_ratio`,
  `volatility_percentile` (252-trading-session rolling history)
- **MARKET CONTEXT:** SPY/QQQ returns + relative strength at the same
  four horizons — TSLA/NVDA by default; a symbol outside that set (e.g.
  MCL) gets none unless explicitly configured in
- **PRICE POSITION:** `vwap_distance`, `ma20_distance`, `ma50_distance`,
  `intraday_range_position`

Every leaf value is `float | None` — `None` for insufficient history, a
missing bar inside a required window (verified by exact timestamp
contiguity, not just array position), or a zero denominator, never a
fabricated `0.0`. "Session" (for VWAP/session-range/time-of-day
matching) is the NY-local calendar date of a bar's timestamp
(`app/features/session.py`, stdlib `zoneinfo`, no new dependency) — a
documented simplification, not strict 9:30–16:00 ET exchange hours,
since the data carries no extended-hours flag.

```
normalized historical_bars (+ SPY/QQQ, for eligible symbols)
        -> app/features/engine.py::compute_features()
        -> FeatureRecord[] (one per bar) -> historical_features table (INSERT OR REPLACE on recompute)
```

Routes: `POST /features/compute` (fetch, compute, persist),
`GET /features/{symbol}` (read back), `GET /features/vocabulary`
(v0.1.24 — every leaf feature above as a `FeatureDefinition`: stable
`feature_id` in `{category}.{field}` form, name, type, description,
supported operators, contract version — the canonical list
[15](#15-research-v1)'s condition builder populates itself from,
`app/features/vocabulary.py`).

**Not implemented, on purpose:** any feature beyond the fixed list
above, per-symbol trading calendars (annualization is applied uniformly
using the standard 252-day/390-minute convention).

**Tests:** `tests/test_feature_*.py` (123 tests) — every feature
calculation, insufficient-history/missing-bar/zero-denominator behavior,
timestamp alignment, no-look-ahead, TSLA/NVDA market context, MCL
exclusion unless configured, persistence/retrieval, and deterministic
recomputation.

## 17. Backtesting v1

An event-based historical backtester that answers exactly one question:
**"when this research condition occurred historically, what happened
afterward?"** Built on top of Research v1 and Feature Engine v1 without
duplicating either — a `Backtest` references an existing `Experiment`
by id (`POST /backtests {"experiment_id": ...}`); its conditions and
already-computed `FeatureRecord`s are read, never redefined or
recomputed (`app/backtesting/engine.py` reuses
`app/research/conditions.py::evaluate_feature_conditions()` verbatim).

```
experiments (existing, [15])            historical_bars + historical_features (existing, [16])
        |                                          |
        +---------------- app/backtesting/engine.py::run_backtest() ----------------+
                                          |
                          BacktestSignal[] (persisted, one per signal)
                                          |
                              BacktestResults (one per configured window)
```

**Chronological walk, no look-ahead:** bars are walked oldest-first.
When conditions evaluate true at bar `t` (using bar `t`'s own,
already-computed feature values only), a signal is generated but never
acted on at bar `t`'s own close — entry happens at bar `t+1`'s **open**,
the first price genuinely available once the condition is fully known.
A condition true on the dataset's last bar produces no signal at all
(there is no next bar to enter at). Forward-window outcomes are computed
only from bars at or after the entry bar.

**Multiple forward horizons per signal:** windows are configurable BAR
counts (not minutes — a Backtest already runs against a fixed
timeframe, so no unit conversion is needed), defaulting to **5, 15, 30,
and 60 bars**. For each window that has enough forward bars remaining
in the dataset, the engine computes:

- **Forward return:** `(outcome_bar.close - entry_price) / entry_price`
- **MFE** (Maximum Favorable Excursion): the best paper gain reached at
  any point in the window, from every bar's `high`
- **MAE** (Maximum Adverse Excursion): the worst paper drawdown reached
  at any point in the window, from every bar's `low`

A window whose outcome bar falls outside the queried dataset is simply
absent from that signal's outcomes — never estimated or fabricated. A
signal with zero measurable windows is not persisted at all.

**Every individual signal is persisted** (`backtest_signals` — signal
timestamp, entry timestamp, entry price, the observed feature values
that fired, and one outcome per measurable window), not just the
aggregate — `GET /backtests/{id}/signals` returns them all, so results
stay fully inspectable. `POST /backtests/{id}/run` deletes and
re-inserts events on every run (same reproducibility convention as
Research v1) — re-running against an unchanged dataset always produces
identical results. `feature_contract_version` is captured from the
referenced Experiment at Backtest-creation time — the identical
reproducibility guarantee Experiment itself already makes.

**Aggregate results per window** (`BacktestWindowResults`): signal
count, win count/rate (a win is `forward_return > 0` — Backtesting v1
has no separate success threshold of its own), mean/median/std-dev
return, best/worst return, and mean MFE/mean MAE — `None`, never a
fabricated `0.0`, when a window has too few (or zero) measurable
signals.

Routes: `POST /backtests` (create, referencing an existing experiment),
`GET /backtests` (list, optionally `?experiment_id=`), `GET .../{id}`
(retrieve), `GET .../{id}/signals` (every individual signal),
`POST .../{id}/run`.

**Not implemented, on purpose (Backtesting v1's own scope, same
discipline as Research v1):** position sizing, a held book, or capital
tracking over time; portfolio construction; parameter optimization or
ML; live/paper trading; advanced execution simulation (slippage,
partial fills, spread cost). Backtesting v1 measures what happened
after a signal — it does not simulate an account.

**Tests:** `tests/test_backtest_*.py` (77 tests) — hand-verified
forward-return/MFE/MAE arithmetic, next-bar-open entry (never the
signal bar's own close), chronological execution, explicit no-look-ahead
proofs (perturbing/truncating future bars never changes an earlier
signal's computed fields; a condition true on the dataset's last bar
produces no signal), per-window aggregation (including zero/one-signal
None handling), feature-contract-version reproducibility, persistence
round-trips, and the full create → run → inspect HTTP flow.

## 18. Statistical Validation v1

Answers one question about an already-run Backtest: does the
conditioned population look different from TSLA's own unconditional
behavior by more than random variation would explain? Consumes
Backtesting v1's already-persisted output — `app/statistical_validation/`
never modifies the Feature Engine, Research, or Backtesting layers, and
adds no database table of its own; every report is recomputed on
demand from the same bars/features/signals a real backtest already
used (`scripts/run_statistical_validation.py --experiment-id ...
--backtest-id ...`).

**Episode-level inference, not raw signals:** a research condition
that stays true for several consecutive bars produces one signal per
bar, not one per onset (see [§17](#17-backtesting-v1) and
`app/statistical_validation/episodes.py`) — those signals are
correlated, not independent. Every confidence interval, p-value, and
effect size here uses the non-overlapping EPISODE-level sample (one
observation per contiguous run of signals, its first bar), never the
raw, clustered signal count — while still reporting both counts
side by side so neither is silently hidden.

**Unconditional baseline**, built without reimplementing any
forward-return math: `app/statistical_validation/baseline.py` calls
the real, unmodified `run_backtest()` with a trivial always-true
control condition (`volume.volume >= 0`), so the baseline gets the
identical next-bar-open entry rule, window definitions, and
insufficient-future-data exclusions a real experiment's backtest
already used.

**Inference, per horizon** (`app/statistical_validation/resampling.py`,
`numpy`-vectorized, always seeded — same seed and data reproduce an
identical report, never a different answer per run): a percentile
bootstrap 95% CI for the conditioned-vs-baseline mean-return
difference and for the win-rate difference. At the one designated
PRIMARY horizon (5 bars by default — configurable, but treated as the
single pre-specified hypothesis, never picked after seeing results): a
two-sided permutation test (H0: the condition carries no information
beyond baseline) and Cohen's d, computed on the raw signal population
too, side by side, so a reader can see exactly how much the inference
changes once clustering is corrected for — the episode-level result is
always the authoritative one.

**Not implemented, on purpose:** multiple-comparison correction across
horizons (only one horizon is ever treated as confirmatory), any claim
of causality, parameter/threshold optimization, and any persistence of
a report — a report is a derived read, always recomputed, never a
row that could silently go stale next to the backtest it describes.

**Tests:** `tests/test_statistical_validation_*.py` (43 tests) —
the episode-grouping rule in isolation, bootstrap/permutation
determinism and structural correctness (CI ordering, p-value bounds,
a hand-verified Cohen's d), and a full synthetic pipeline (real
Feature Engine → real Research → real Backtesting v1 → Statistical
Validation) exercising sample-size reconciliation, exactly-one-primary-
horizon, and error handling (a stale/tampered persisted backtest is
detected and rejected, never silently trusted).

## 19. Statistical Validation v2

Corrects V1's one flagged weakness (see §18's own Limitations): V1's
baseline treated every eligible bar as an independent observation
despite adjacent bars' forward-return windows overlapping heavily —
V1's own report on the real TSLA experiment flagged this explicitly
rather than silently trusting the number. `app/statistical_validation/v2/`
is additive only — it does not modify V1
(`app/statistical_validation/{episodes,baseline,resampling,engine}.py`),
the Feature Engine, Research, or Backtesting, and reuses V1's episode
definition and unconditional-baseline construction unchanged.

**Two independent, clearly-labeled dependence corrections for the
baseline side** (the conditioned side keeps V1's episode-level
treatment exactly):

- **Method A — non-overlapping windows** (`app/statistical_validation/v2/baseline.py`):
  subsamples V1's own baseline down to entries whose forward-return
  windows never overlap. Once non-overlapping, V1's own, unmodified
  bootstrap/permutation functions apply directly — no new statistical
  machinery needed.
- **Method B — moving block bootstrap** (`app/statistical_validation/v2/resampling.py`):
  resamples the full, chronologically-ordered, overlapping baseline
  series in contiguous blocks (length = 4× the horizon, a fixed,
  documented rule) rather than individual points — the standard
  technique (Künsch 1989; Politis & Romano) for a valid bootstrap
  distribution from serially dependent data. Its hypothesis test uses
  the standard "H0-centered bootstrap test" construction (Hall &
  Wilson 1991): both samples are shifted to a common mean before
  resampling, since naively permuting individual points in an
  autocorrelated series would produce an invalidly narrow (falsely
  confident) null distribution.

Run for real against the same TSLA experiment (5-bar horizon, 65
episodes): Method A gives p=0.2526, Method B gives p=0.4140 — both
methods agree the 5-bar effect does not clearly stand out from
baseline once dependence is handled correctly on both sides, and the
two methods' conclusions agree (`conclusion_changes_materially: False`).

**Post-hoc power analysis** (`app/statistical_validation/v2/power.py`):
a standard closed-form minimum-detectable-effect-size calculation (no
scipy dependency — two fixed, tabulated z-quantiles) — with 65
episodes and 946 effective baseline observations, this study could
reliably detect a Cohen's d of ≈0.36 at 80% power; the observed d
(≈0.15) is below that threshold, meaning a null result here is
unsurprising on power grounds alone, never interpreted as evidence
that no effect exists.

15/30/60-bar horizons remain descriptive only — no CI, no p-value, no
effect size at any horizon but the primary one, per this feature's own
scope.

**Tests:** `tests/test_statistical_validation_v2_*.py` (46 tests) —
non-overlapping selection (dense spacing, real gaps, out-of-order
input), moving-block-bootstrap determinism and a hand-verified
periodic-series exact-mean case, the H0-shift p-value's invariance to
a common additive shift, the power formula against a hand-computed
value, and a full synthetic pipeline exercising population-count
reconciliation, both methods' CI/p-value validity, and the same
error-handling guarantees as V1.

## 20. OOS / Holdout Partition Framework v1

The first step toward reproducible out-of-sample validation: a way to
explicitly split an existing symbol/timeframe/provider's already-stored
bars (`historical_bars` — never duplicated) into a **development**
window and a later, non-overlapping **holdout** window, and a real
technical boundary against *accidentally* reading the holdout side
while iterating on a hypothesis. It does **not** implement OOS
statistical testing, strategy optimization, parameter searches, ML,
strategy construction, or paper trading — this is the partitioning and
provenance foundation those would be built on, not one of them.

**Partition model** (`app/models/oos_partition.py`): an `OOSPartition`
holds `symbol`/`timeframe`/`provider`, four boundary timestamps
(`development_start`/`development_end`/`holdout_start`/`holdout_end`),
`created_at`, and a deterministic `id`. `development_end` must be
strictly before `holdout_start` — a touching or overlapping boundary is
rejected by a pydantic validator before a partition can even be
constructed, both at the API layer (`OOSPartitionCreateRequest`) and on
`OOSPartition` itself (defense in depth for a row rebuilt from the
database).

**Determinism** (requirement 5): `id` is a SHA-256 hash of
provider/symbol/timeframe/the four boundary timestamps
(`compute_partition_id()`) — never a random id. Creating the identical
partition twice is idempotent: `oos_partition_repository.save_partition()`
INSERT-OR-IGNOREs on that id, so the second call is a no-op that leaves
the original record (including its original `created_at`) untouched.

**Explicit development/holdout semantics** (`app/oos/access.py`):
`get_development_bars()` reads the development window, unrestricted.
`get_holdout_bars()` reads the holdout window but requires
`confirm_oos_validation_use=True` to be passed explicitly — the default
(`False`, and the corresponding `GET .../holdout/bars` HTTP route's
default) raises `HoldoutAccessError` (403 over HTTP). No OOS-validation
operation exists yet to set that flag from — it is the seam a future one
will call through.

**API:**

```
POST /oos/partitions                          create (idempotently) a partition
GET  /oos/partitions                           list, optionally filtered by symbol/timeframe/provider
GET  /oos/partitions/{id}                      one partition
GET  /oos/partitions/{id}/development/bars     development-window bars (unrestricted)
GET  /oos/partitions/{id}/holdout/bars          holdout-window bars (needs ?confirm_oos_validation_use=true)
```

**What is actually prevented vs. merely documented:**

- Overlapping/touching ranges, invalid ordering (inverted development or
  holdout window, development after holdout), and missing metadata (a
  blank symbol/provider/timeframe or an absent field) — **technically
  prevented**: a partition simply cannot be constructed in that shape,
  by pydantic validators app/models/oos_partition.py runs on every
  construction path.
- A single call mixing development and holdout bars — **technically
  prevented** for any caller going through `app/oos/access.py`:
  `get_development_bars()`/`get_holdout_bars()` each query only their
  own window.
- Accidental holdout access (the default, argument-free call) —
  **technically prevented**: `get_holdout_bars()` without
  `confirm_oos_validation_use=True` raises immediately, matched by the
  HTTP route's 403. This is a deliberateness guard, not access control —
  any caller that deliberately passes `confirm_oos_validation_use=True`
  outside a real future OOS-validation operation is not stopped by
  anything in this codebase; Python has no mechanism that could enforce
  who is allowed to set a boolean.
- A holdout window that includes development bars — **structurally
  impossible by construction**: since `holdout_start` must be strictly
  after `development_end`, no timestamp inside `[development_start,
  development_end]` can ever also fall inside `[holdout_start,
  holdout_end]`.
- A Research experiment, a Feature Engine computation, a Backtest, or a
  Statistical Validation run being created against a range that
  overlaps or falls entirely inside a holdout window — **NOT
  technically prevented today**. `app/oos/partition.py::
  require_development_range()` exists as the ready-to-wire guard for
  this, but nothing in `app/api/research.py`, `app/api/features.py`,
  `app/api/backtesting.py`, or `app/statistical_validation/` calls it
  yet — those engines have no notion of an `OOSPartition` at all, by
  this feature's own explicit scope (it must not modify their
  behavior). A developer can still create a Research experiment whose
  date range lands inside a holdout window; nothing in the codebase
  stops that yet.
- A caller bypassing `app/oos/access.py` entirely and calling
  `app/storage/historical_bar_repository.py` directly with a range that
  spans both windows — **NOT technically prevented**, since the
  repository itself has no notion of partitions.

**Tests:** `tests/test_oos_partition_model.py`,
`tests/test_oos_partition_logic.py`,
`tests/test_oos_partition_repository.py`,
`tests/test_oos_partitions_api.py` (41 tests) — valid partition
construction, overlapping/touching/inverted ranges rejected, boundary
timestamps (exact-touch rejected, one-microsecond-clear accepted),
deterministic id (same inputs → same id, case/offset-insensitive,
different inputs → different id), metadata persistence/round-trip,
idempotent re-save, range classification and the development-only
guard, and both segment-bar-access functions (including the holdout
confirmation gate, at both the function and HTTP-route level).

## 21. Experiment Freeze & Provenance v1

A Research Experiment (§15) gains a SECOND, independent lifecycle axis
on top of its existing run status (draft/running/completed/failed,
unchanged): `lifecycle_state` — **DRAFT → FROZEN → OOS_EVALUATED →
ARCHIVED** (`FROZEN → ARCHIVED` also allowed directly). Freezing commits
an experiment's hypothesis definition — after that point, its research
meaning can never change. Deliberately does **not** implement OOS
statistical testing or strategy optimization — this is the lifecycle
and provenance foundation those would be built on.

**Lifecycle** (`app/research/lifecycle.py::validate_transition()`, the
one place the state table is enforced): `DRAFT→FROZEN`,
`FROZEN→OOS_EVALUATED`, `FROZEN→ARCHIVED`, `OOS_EVALUATED→ARCHIVED`.
Every other transition (including anything out of `ARCHIVED`, or
`DRAFT` to anything but `FROZEN`) raises
`InvalidLifecycleTransitionError` (409 over HTTP).
`FROZEN→OOS_EVALUATED` is infrastructure only —
`research_repository.mark_oos_evaluated()` exists and is tested
directly, but no HTTP route calls it, since the actual OOS-evaluation
operation is a future feature.

**Freeze semantics:** freezing (`POST /research/experiments/{id}/freeze`)
computes a deterministic `hypothesis_hash` and persists an immutable
`ExperimentFreezeSnapshot` — a value copy of the experiment's
research-defining fields at that instant (symbol/timeframe/provider/
start_date/end_date/feature_contract_version/conditions/outcome), not
a live reference — in the SAME operation as the lifecycle-state write.
Nothing added by this feature can mutate a research-defining field
after freezing: there has never been a generic "edit experiment"
endpoint in this codebase, and the one new pre-freeze-mutable field
this feature adds (`oos_partition_id`, see below) is rejected with a
409 the instant `lifecycle_state` leaves `DRAFT` — enforced twice
(the API route, and independently in `research_repository.set_oos_partition()`'s
own `WHERE lifecycle_state = 'draft'`).

**Provenance hash** (`app/research/lifecycle.py::compute_hypothesis_hash()`):
SHA-256 of a canonical JSON encoding of exactly the fields that define
research meaning — symbol, timeframe, provider, development date
range, feature contract version, conditions, and outcome. Deterministic
and ordering-independent (`conditions` is sorted before hashing, so an
AND-combined set hashes identically regardless of the order it was
written in) and stable across equivalent representations
(`json.dumps(sort_keys=True)`). Excludes `id`/`created_at`
(database-generated/timestamps), `name`/`hypothesis` (free-text
metadata), and `oos_partition_id` (which partition is *reserved* for
evaluation is not part of what the hypothesis *is*).

**OOS partition linkage** (`app/oos/` itself is unmodified — this
reuses `app.oos.partition.classify_range()` as-is): a DRAFT experiment
may associate an OOS partition
(`POST /research/experiments/{id}/oos-partition`); both that route and
freezing itself validate symbol/timeframe/provider compatibility and
that the experiment's ENTIRE date range falls inside the partition's
development window (which, by `classify_range()`'s own construction,
also rejects any range touching the holdout window at all) — a
mismatch on either check is a 400, a partition that doesn't exist is a
404.

**API:**

```
POST /research/experiments/{id}/oos-partition   associate a DRAFT experiment with a partition
POST /research/experiments/{id}/freeze           DRAFT -> FROZEN
GET  /research/experiments/{id}/frozen           the immutable freeze snapshot
GET  /research/experiments/{id}/provenance       symbol/timeframe/provider/dates/conditions/outcome/
                                                  feature contract/reserved partition, in one response
POST /research/experiments/{id}/archive          FROZEN|OOS_EVALUATED -> ARCHIVED
```

**Gotcha:** `Experiment.start_date`/`end_date` are calendar dates;
`OOSPartition` boundaries are full timestamps. A same-day experiment
end_date is treated as spanning the WHOLE day (`23:59:59.999999` UTC)
for containment purposes — a partition's `development_end` set to
literal midnight of that day (`T00:00:00Z`) will reject it as
"touching the boundary"; set it to end-of-day instead.

**Tests:** `tests/test_experiment_lifecycle.py`,
`tests/test_experiment_freeze_repository.py`,
`tests/test_experiment_freeze_api.py` (72 tests) — every valid/invalid
transition, hash determinism/ordering-independence/exclusions and that
it changes with every research-defining field, partition-linkage
compatibility and containment/holdout-leakage rejection, snapshot
persistence surviving the live row moving on to ARCHIVED, provenance
retrieval, and the full HTTP flow including 409s for invalid
transitions and post-freeze mutation attempts.

## 22. OOS Evaluation v1

The actual OOS-evaluation operation §20/§21 were both built toward:
given a FROZEN (or already OOS_EVALUATED, for a re-run) experiment
linked to an OOS partition, runs its frozen hypothesis against the
partition's **holdout** data and persists an immutable, append-only
result. Orchestrates the existing Feature Engine, Research condition
evaluation, and Backtesting v1 engine — reuses all three UNMODIFIED,
never a second implementation of any of them.

**Pipeline** (`app/oos_evaluation/engine.py::evaluate_oos()`): load the
live Experiment (lifecycle_state only) + the immutable
`ExperimentFreezeSnapshot` (everything research-defining comes from
here, never the mutable row) → load the linked `OOSPartition` and
re-validate symbol/timeframe/provider/containment against the
snapshot's own fields → read a bounded DEVELOPMENT warm-up range →
read holdout bars via `app.oos.access.get_holdout_bars(...,
confirm_oos_validation_use=True)` — **the sole holdout access path** →
compute features (`app.features.engine.compute_features()`,
unmodified) over warm-up + holdout bars together, then discard every
record computed at a warm-up bar → run
`app.backtesting.engine.run_backtest()` (unmodified) with `bars` =
holdout bars ONLY and a single window converted from the frozen
Outcome's own `horizon_minutes` → wrap the result into an
`OOSEvaluationResult` + `OOSSignal` rows.

**Warm-up handling:** the Feature Engine emits one FeatureRecord per
bar it's given, walking backward through its own fixed per-feature
window (ATR 14+1, realized volatility 20+1, SMA50 50, the largest
return horizon converted to bars — `app/oos_evaluation/warmup.py`,
reusing these as PUBLISHED constants from the Feature Engine itself,
never re-derived). Warm-up bars are read from a bounded, calendar-time-
buffered range strictly BEFORE `holdout_start` **and clamped to never
exceed the partition's own `development_end`** (audit finding,
2026-08-18 — see below), floored at `development_start`, and
concatenated ahead of the holdout bars ONLY for this feature-
computation call — the resulting records at warm-up-bar timestamps are
then thrown away before condition evaluation or backtesting ever runs,
so a development bar can structurally never become a signal/entry/
outcome observation. `volatility_ratio`/`volatility_percentile` need up
to 252 TRADING SESSIONS of history — deliberately NOT warmed up (that
would violate "minimum required context" far more than it would help);
those two features may legitimately read `None` for a while into the
holdout period, the Feature Engine's own existing, unmodified behavior.

**OOS Evaluation V1 Audit (2026-08-18):** an adversarial audit found
one genuine (non-holdout-leaking) defect — a partition's own validator
only requires `development_end < holdout_start`, not adjacency, so a
partition MAY declare a gap between the two (e.g. to wall off a known
data-quality window). Before the fix, warm-up's read range was bounded
only by `holdout_start`, so it could read bars from that undeclared gap
instead of the partition's own declared development window — confirmed
by construction (a partition with a 22-day gap, real bars seeded only
in the gap, still fully warmed up a 50-bar SMA50 feature at the first
holdout bar). Not a holdout leak (gap bars are still strictly
pre-holdout, so no future information was ever used), but a violation
of "use the minimum required DEVELOPMENT context". Fixed by also
clamping the warm-up range's end to `development_end`
(`app/oos_evaluation/warmup.py::warmup_range()`) — a no-op for every
adjacent partition (the common case, including every example partition
elsewhere in this codebase). Every other audited property — sole
holdout access path, no fetch-then-slice, no writes anywhere in the
pipeline, boundary conditions (final-holdout-bar signals, insufficient
future bars, outcomes exactly at/never beyond `holdout_end`), immunity
to a tampered live Experiment row (conditions/symbol/timeframe/feature
contract/outcome threshold/horizon), provenance independence,
deterministic re-runs, and failure safety (a pipeline failure never
reaches `OOS_EVALUATED`, never persists partial signals, and a
subsequent success still works) — held up under adversarial testing
with no further defects found. See `tests/test_oos_evaluation_audit.py`.

**Anti-leakage:** `get_holdout_bars(..., confirm_oos_validation_use=True)`
is the only function anywhere in this pipeline that reads the
experiment's own symbol's bars at or after `holdout_start` — no
"fetch everything, slice later" step exists. `run_backtest()`'s `bars`
argument is always holdout bars alone, so a signal, an entry, or an
outcome window can never reference a development-side bar.

**API** (request body is completely ignored — every research-defining
fact comes from the frozen snapshot and linked partition, never the
caller):

```
POST /research/experiments/{id}/oos-evaluate            run (or re-run) the evaluation
GET  /research/experiments/{id}/oos-evaluations          every evaluation ever run for this experiment
GET  /research/oos-evaluations/{evaluation_id}            one evaluation
GET  /research/oos-evaluations/{evaluation_id}/signals    its individual OOS signals
```

**Persistence:** append-only `oos_evaluations`/`oos_evaluation_signals`
tables — re-running the SAME frozen experiment creates a brand-new
evaluation row (random id, like `experiments.id`, not a deterministic
hash like `oos_partitions.id`) with identical analytical results,
never replacing a prior evaluation. A pipeline-stage failure (as
opposed to a precondition rejection) is itself persisted as a `FAILED`
result with `error_message` set, and does **not** advance the
lifecycle — `FROZEN → OOS_EVALUATED` only happens on a `COMPLETED`
result, and only when the experiment was still exactly `FROZEN` going
into the call (a re-run of an already-`OOS_EVALUATED` experiment
leaves the lifecycle untouched).

**Real-data validation:** `scripts/run_oos_evaluation.py --demo` seeds
a deterministic, seeded random-walk synthetic dataset (no live
provider access in this environment), freezes a single, un-optimized
condition (`price.return_15m <= -0.5%` → 30-minute forward return), and
runs the full pipeline against it — a genuine, non-cherry-picked system
validation run, not a strategy search.

**Tests:** `tests/test_oos_evaluation_warmup.py`,
`tests/test_oos_evaluation_engine.py`,
`tests/test_oos_evaluation_repository.py`,
`tests/test_oos_evaluation_api.py`,
`tests/test_oos_evaluation_audit.py` (57 tests) — DRAFT/ARCHIVED
rejection, missing/incomplete/incompatible partition linkage rejection,
the holdout-authorization gate, a development-only condition never
producing a signal even though the Feature Engine's own warm-up
computation genuinely satisfies it there, every signal/entry/outcome
falling strictly within the holdout window, an early signal's values
provably unaffected by a change far later in holdout, deterministic
re-runs, append-only persistence, and the full HTTP flow including the
adversarial-request-body-is-ignored proof.

## Known limitations

- `npm audit` flags a moderate/high `esbuild` advisory (bundled by Vite
  5) affecting the **local dev server** only (GHSA-67mh-4wv8-2f99); not
  production builds. Fixing it needs a Vite 7/8 upgrade — out of scope
  for now.
- The frontend's `normalCdf` (rational approximation) and the backend's
  (`math.erf`, exact) can differ by ≤1.5×10⁻⁷ — invisible at the UI's
  displayed precision.
- OOS Evaluation v1's feature warm-up (§22) deliberately does NOT
  extend far enough to guarantee `volatility_ratio`/
  `volatility_percentile` are non-`None` at the start of a holdout
  period (they need up to 252 trading sessions of history — warming
  that much up for every evaluation would violate "minimum required
  context" far more than it would help). A condition referencing
  either feature may simply find fewer eligible signals near the start
  of holdout as a result — the Feature Engine's own existing
  "insufficient history" behavior, not a bug specific to OOS
  Evaluation.
