# User Guide

Task-oriented "how do I actually do X" instructions for Pandey Quant
Lab. This is not a replacement for [`README.md`](README.md) (what the
app is and how it's built) or [`BUILD_LOG.md`](BUILD_LOG.md) (the
chronological record of every change) — it's the third document: the
one you open when you want to *do something* and don't want to
reconstruct the request shape from Pydantic models.

Every worked example below uses **real output from a real run against
real Alpaca market data**, captured on 2026-08-18 in this repo, not
invented numbers — see [Worked example: TSLA selling continuation](#worked-example-tsla-selling-continuation)
for the full trail.

**Backtesting v1 has no frontend UI yet** (Research v1 does — see
[Creating and running a Research experiment](#4-creating-and-running-a-research-experiment) for the UI path). Every
Backtesting workflow below is API-only: use Swagger UI at
`http://localhost:8000/docs` for point-and-click exploration, or the
`curl` commands below for anything you want to script or repeat.

---

## Contents

1. [Starting the app](#1-starting-the-app)
2. [Getting historical data into the database](#2-getting-historical-data-into-the-database)
3. [Computing features for a symbol/date range](#3-computing-features-for-a-symboldate-range)
4. [Creating and running a Research experiment](#4-creating-and-running-a-research-experiment)
5. [Creating and running a Backtest](#5-creating-and-running-a-backtest)
6. [Reading backtest results correctly](#6-reading-backtest-results-correctly)
7. [Worked example: TSLA selling continuation](#worked-example-tsla-selling-continuation)
8. [Quick reference](#8-quick-reference)
9. [Resetting or inspecting the database directly](#9-resetting-or-inspecting-the-database-directly)

---

## 1. Starting the app

```bash
cd backend && ./venv/bin/uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm run dev
```

Or both together: `./scripts/dev.sh start` (see `./scripts/dev.sh
status|stop|restart`). Backend: `http://localhost:8000` — Swagger UI
at `http://localhost:8000/docs` is the fastest way to see every route,
its exact request/response shape, and fire one off by hand. Frontend:
`http://localhost:5173`.

Real provider calls (Alpaca/Massive) need real credentials in
`backend/.env` — `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY`  or
`MASSIVE_API_KEY`. `MARKET_DATA_PROVIDER` in `.env` picks the default
provider the calculator/live-quote UI uses, but every historical-data
and research/backtest route below takes `provider` explicitly per
request — it never depends on that default.

## 2. Getting historical data into the database

Two steps, always separate: **fetch** (ask a provider) and **save**
(write to SQLite). Fetching never auto-saves — that's deliberate, see
[README §9](README.md#9-historical-market-data-pipeline).

```bash
# 1. Fetch — TSLA/NVDA only, timeframe one of 1m/5m/15m/1h/1d
curl -s "http://localhost:8000/api/market-data/TSLA/history?start=2026-06-01&end=2026-08-15&timeframe=5m&provider=alpaca" \
  -o bars.json

# 2. Save — validates (impossible OHLCV / duplicates -> quarantined, soft anomalies -> flagged-but-kept)
python3 -c "import json; json.dump({'bars': json.load(open('bars.json'))['bars']}, open('save.json','w'))"
curl -s -X POST http://localhost:8000/api/market-data/history/save \
  -H "Content-Type: application/json" -d @save.json
```

The save response tells you exactly what happened —
`{"total": 4387, "inserted": 4387, "skipped_duplicates": 0, "flagged":
87, "rejected_invalid": 0, "rejected": []}` is real output: 87 bars
were flagged (an unusual gap or move — logged, still saved) and zero
were hard-rejected. Re-running the same fetch+save is always safe:
duplicates are silently skipped (`UNIQUE(provider, symbol, timeframe,
timestamp)` on the table), never double-inserted.

**Only TSLA and NVDA** are fetchable through this route today
(`ALLOWED_SYMBOLS` in `app/api/historical_data.py` — a deliberate v1
scope fence, not a provider limitation). That also means **SPY/QQQ
(market-context reference data) cannot be fetched this way** — see
[§6](#6-reading-backtest-results-correctly) for what that means for
your results. A CSV upload (any symbol) is the other way data gets in
— see the Calculator page's CSV import for that flow.

## 3. Computing features for a symbol/date range

```bash
curl -s -X POST http://localhost:8000/api/features/compute \
  -H "Content-Type: application/json" -d '{
    "symbol": "TSLA",
    "start_date": "2026-06-01",
    "end_date": "2026-08-15",
    "timeframe": "5m",
    "provider": "alpaca",
    "include_market_context": true
  }'
```

Computes the full 31-feature contract (PRICE/VOLUME/VOLATILITY/MARKET
CONTEXT/PRICE POSITION — see [README §16](README.md#16-feature-engine-v1))
for every bar you already saved in that range, and persists it —
`INSERT OR REPLACE`, so recomputing is always safe and just overwrites
stale derived data. `GET /api/features/vocabulary` lists every
`feature_id` you can reference in a condition, with its type and
supported operators — that's the one canonical list; nothing hardcodes
a second copy anywhere in this app.

You need **enough trailing history** before a feature stops being
`None` — e.g. `price.return_15m` needs a bar exactly 15 minutes
earlier to exist; `volatility_percentile` needs 252 sessions of prior
history. Compute over your full intended date range in one call rather
than day-by-day chunks, or early observations in each chunk will be
needlessly `None`.

## 4. Creating and running a Research experiment

This has a real frontend: the **Research** page (`frontend`, once
running) walks you through picking a symbol/date range, adding
`FeatureCondition`s (AND-combined — feature, operator, threshold, with
type-aware operators pulled live from `/api/features/vocabulary`), and
an `Outcome` (forward-return horizon + threshold), then Run and inspect
results/individual events. The API underneath, if you're scripting it:

```bash
curl -s -X POST http://localhost:8000/api/research/experiments \
  -H "Content-Type: application/json" -d '{
    "name": "...", "hypothesis": "...",
    "symbol": "TSLA", "start_date": "2026-06-01", "end_date": "2026-08-15",
    "timeframe": "5m", "provider": "alpaca",
    "conditions": [
      {"feature_id": "price.return_15m", "operator": "<=", "value": -0.005},
      {"feature_id": "volume.relative_volume", "operator": ">", "value": 1.5}
    ],
    "outcome": {"metric": "forward_return", "horizon_minutes": 30, "operator": "<=", "threshold": -0.003}
  }'
# -> {"id": "...", "status": "draft", ...}

curl -s -X POST http://localhost:8000/api/research/experiments/{id}/run
curl -s http://localhost:8000/api/research/experiments/{id}/events   # every individual signal
```

Research answers "did this pattern hold historically, on average" — one
number per horizon, entry priced at the **signal bar's own close**. If
you want next-bar-open entry, multiple horizons at once, and MFE/MAE,
that's what Backtesting is for — next section.

## 5. Creating and running a Backtest

A Backtest always references an **existing** Experiment by id — it
never redefines conditions of its own; it reuses the Experiment's
conditions and already-computed feature data exactly as they are. Do
step 4 first.

```bash
# 1. Create — windows default to [5, 15, 30, 60] bars if you omit them
curl -s -X POST http://localhost:8000/api/backtests \
  -H "Content-Type: application/json" \
  -d '{"experiment_id": "<experiment-id-from-step-4>"}'
# -> {"id": "...", "status": "draft", "windows": [5, 15, 30, 60], ...}

# 2. Run
curl -s -X POST http://localhost:8000/api/backtests/{id}/run
# -> "status": "completed", "results": {"windows": [ {per-window stats}, ... ]}

# 3. Inspect every individual signal (not just the aggregate)
curl -s http://localhost:8000/api/backtests/{id}/signals
```

To use non-default windows: `{"experiment_id": "...", "windows": [10,
20]}` — any distinct positive bar counts. Re-running (`POST
.../run`) is always safe: it deletes and replaces this backtest's
signals, so results are byte-identical across re-runs against
unchanged data, never doubled.

**What each signal record gives you** (`GET .../signals`): the
condition's own feature values at the moment it fired, the exact entry
timestamp/price (next bar's open — never the signal bar's own close,
see [§6](#6-reading-backtest-results-correctly)), and one outcome per
window that had enough forward data to measure: `forward_return`
(close-to-close from entry), `mfe` (best paper gain reached at any
point in the window), `mae` (worst paper drawdown reached at any point
in the window).

**What the aggregate gives you** (`results.windows`, one entry per
configured window): `signal_count`, `win_count`/`win_rate` (a win is
simply `forward_return > 0`), `mean_return`/`median_return`/
`std_dev_return`, `best_return`/`worst_return`, `mean_mfe`/`mean_mae`.
Any stat is `null` — never a fabricated `0.0` — when a window has too
few (or zero) measurable signals.

## 6. Reading backtest results correctly

Three things to know before you trust a number:

- **A window can have fewer signals than the total.** `signal_count`
  is reported per window because a signal near the end of your date
  range may not have enough forward bars for a 60-bar outcome even
  though its 5-bar outcome is fine. Don't assume every window covers
  the same signals — check `signal_count` per window, not just once.
- **Signals can cluster.** If a condition stays true for several
  consecutive bars (e.g. "price above its 20-bar average"), you get one
  signal **per bar** it's true, not one signal per time it *becomes*
  true — so a persistent condition produces a run of heavily-overlapping,
  non-independent signals. This is intentional (Backtesting reuses
  Research's own condition evaluation unchanged), but it means a large
  `signal_count` on a "sticky" condition isn't automatically strong
  statistical evidence — check whether your signals are actually
  spread out in time (`GET .../signals` and look at `signal_timestamp`
  gaps) before reading the aggregate at face value.
- **Windows are bar counts, not calendar time.** A "60-bar" window on
  5-minute bars can span past the trading day's close into the next
  session's bars — the window counts *available bars*, not elapsed
  minutes, so its real-world time span can vary. Check
  `outcome_timestamp` on individual signals if the exact time span
  matters to your read.

---

## Worked example: TSLA selling continuation

A real, end-to-end run performed against live Alpaca data, so every
number below is an actual result, not an illustration.

**1. Fetched 4,387 real 5-minute TSLA bars** from Alpaca, 2026-06-01
through 2026-08-14 — saved with 0 rejections, 87 flagged (soft
anomalies, e.g. gaps — kept, not discarded).

**2. Computed features** for that same range/timeframe (4,387 feature
records). SPY/QQQ weren't fetchable through the historical-data route
(TSLA/NVDA-only fence — [§2](#2-getting-historical-data-into-the-database)), so
`market_context.*` fields are `None` throughout this run — everything
else (price/volume/volatility/price-position) is real.

**3. Picked a grounded hypothesis**, not an arbitrary one — I queried
the actual persisted feature distribution first: `return_15m <=
-0.005` alone matched 353 of 4,037 observations (~8.7%); adding
`relative_volume > 1.5` narrowed it to 124 — a real, non-trivial
sample. Experiment: *"After TSLA drops at least 0.5% over a trailing
15 minutes on relative volume above 1.5x normal, price continues
declining over the next 30 minutes."*

**4. Ran it as a Research experiment first** — 124 events,
`success_rate: 0.484` against a `forward_return <= -0.3%` bar.

**5. Created a Backtest against that same experiment**, default
windows `[5, 15, 30, 60]`, and ran it. Real aggregate results:

| Window | Signals | Win rate | Mean return | Std dev | Best | Worst | Mean MFE | Mean MAE |
|---|---|---|---|---|---|---|---|---|
| 5 bars | 124 | 39.5% | −0.136% | 1.028% | +3.56% | −2.59% | +0.60% | −0.74% |
| 15 bars | 124 | 41.9% | −0.060% | 1.616% | +4.12% | −4.03% | +1.03% | −1.27% |
| 30 bars | 124 | 46.0% | −0.061% | 2.033% | +3.80% | −6.64% | +1.35% | −1.66% |
| 60 bars | **120** | 49.2% | +0.085% | 2.682% | +6.46% | −7.68% | +1.86% | −1.96% |

Note the 60-bar row: **120 signals, not 124** — 4 signals near the end
of the fetched range had no 60-bar-forward outcome available yet, so
they're correctly absent from that row's stats (never estimated).

**6. Inspected two individual signals** and saw both caveats from
[§6](#6-reading-backtest-results-correctly) directly, on real data:

- Signals at `2026-06-03T15:35:00Z` and `2026-06-03T15:40:00Z` are
  **five minutes apart** — the condition held true on two consecutive
  bars, so it fired twice, with nearly identical entries (`$426.10` vs
  `$425.95`) and heavily overlapping outcome windows. Exactly the
  clustering caveat, seen live.
- The first signal's 60-bar outcome timestamp is
  `2026-06-04T13:35:00Z` — **the next calendar day**. 60 bars forward
  from a 15:40 UTC entry ran past that session's close and picked up
  the next session's bars. Exactly the "bar count, not calendar time"
  caveat, seen live.

Read at face value, this run does **not** support the hypothesis at
the shorter horizons (win rate under 50%, negative mean return through
30 bars) — only the 60-bar window tips slightly positive, and every
window's standard deviation dwarfs its mean, meaning the spread of
outcomes is much wider than any average edge. That's a legitimate,
data-driven negative result — Backtesting v1's whole job is to let a
result like this stand on its own, not to keep searching until
something looks better.

---

## 8. Quick reference

| Action | Route |
|---|---|
| Fetch live historical bars | `GET /api/market-data/{symbol}/history?start=&end=&timeframe=&provider=` |
| Save fetched bars | `POST /api/market-data/history/save` |
| Read saved bars | `GET /api/market-data/{symbol}/history/stored?...` |
| Compute + persist features | `POST /api/features/compute` |
| Read saved features | `GET /api/features/{symbol}?...` |
| Feature vocabulary | `GET /api/features/vocabulary` |
| Create experiment | `POST /api/research/experiments` |
| Run experiment | `POST /api/research/experiments/{id}/run` |
| List / get experiment | `GET /api/research/experiments` / `GET /api/research/experiments/{id}` |
| Individual experiment events | `GET /api/research/experiments/{id}/events` |
| Create backtest | `POST /api/backtests` (`{"experiment_id": "...", "windows": [5,15,30,60]}`) |
| Run backtest | `POST /api/backtests/{id}/run` |
| List / get backtest | `GET /api/backtests` (`?experiment_id=`) / `GET /api/backtests/{id}` |
| Individual backtest signals | `GET /api/backtests/{id}/signals` |

## 9. Resetting or inspecting the database directly

Everything lives in one SQLite file, `backend/data/historical_bars.db`
(`DATABASE_PATH` in `.env` to point elsewhere). Delete it to start
completely clean — it's recreated with a fresh schema on the next
request. To look inside without going through the API:

```bash
sqlite3 backend/data/historical_bars.db \
  "SELECT symbol, timeframe, provider, COUNT(*) FROM historical_bars GROUP BY 1,2,3;"
```

Backend tests never touch this file — every test uses an isolated
`tmp_path` database (see `backend/tests/`), so running `pytest` is
always safe and never affects data you've fetched by hand.
