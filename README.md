# Pandey Quant Lab

A transparent, educational quant toolkit: a **bear put spread calculator**
where every number traces back to a visible formula, plus a **market-data
pipeline** (live quotes, streaming, historical bars) and a **research
layer** (hypothesis testing + feature computation) built on top of it.

**This is not a trading bot.** No brokerage connection, no order
execution, no autonomous buy/sell recommendations, no user accounts. See
[10. What is intentionally NOT implemented](#10-what-is-intentionally-not-implemented-yet)
and [`BUILD_LOG.md`](BUILD_LOG.md) for the full change history.

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

Backend tests: `cd backend && ./venv/bin/pytest` — 813+ tests, all
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
computation exposed as a canonical vocabulary), and Backtesting v1
(event-based historical walk over an existing Research experiment:
next-bar-open entry, multi-horizon forward return/MFE/MAE, every signal
persisted and inspectable).

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
real trade or move real capital on your behalf, ever.

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

`cd backend && ./venv/bin/pytest` — 813+ deterministic tests, all against
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
    storage/                 SQLite schema + one repository per table family (bars, raw, research, features, backtests)
    streaming/                 Reconnecting WebSocket streams + the per-(provider,symbol) hub
    research/                   Research v1 -- pure condition/outcome engine (see section 15)
    features/                   Feature Engine v1 -- pure feature computation (see section 16)
    backtesting/                 Backtesting v1 -- pure chronological walk + aggregation (see section 17)
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

## Known limitations

- `npm audit` flags a moderate/high `esbuild` advisory (bundled by Vite
  5) affecting the **local dev server** only (GHSA-67mh-4wv8-2f99); not
  production builds. Fixing it needs a Vite 7/8 upgrade — out of scope
  for now.
- The frontend's `normalCdf` (rational approximation) and the backend's
  (`math.erf`, exact) can differ by ≤1.5×10⁻⁷ — invisible at the UI's
  displayed precision.
