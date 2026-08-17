"""App-wide configuration, read from environment variables.

This is deliberately the ONLY module in the app that calls
os.environ directly for provider selection/credentials. Everything
else -- registry.py, individual providers -- takes configuration as
plain constructor/function arguments. That's not a style preference:
it means every provider's __init__ can be unit-tested by just passing
in a fake key, with no environment-variable juggling in the test, and
it means there's exactly one place to look if you're ever unsure where
a credential is read from.

No new dependency (python-dotenv, pydantic-settings, ...) was added
for this -- os.environ.get() with a default is enough for what this
app configures today. Reach for a real settings library if this file
grows past a handful of values.
"""

import os
from pathlib import Path

DEFAULT_MARKET_DATA_PROVIDER = "csv"

# backend/data/historical_bars.db -- a plain file next to the app, not
# backend/.env-adjacent or repo-root: computed from this file's own
# location so it resolves the same way regardless of the process's cwd
# (uvicorn run from backend/, pytest run from backend/, scripts/dev.sh
# run from the repo root -- all three happen in practice).
DEFAULT_DATABASE_PATH = str(Path(__file__).resolve().parent.parent / "data" / "historical_bars.db")


def get_configured_provider_name() -> str:
    """Which MarketDataProvider the app should use by default.

    Reads MARKET_DATA_PROVIDER (e.g. "csv", "alpaca", "massive",
    "schwab"), defaulting to "csv" if unset -- so a fresh checkout
    with no .env file still behaves exactly like it did before this
    config mechanism existed. Does not validate the name against the
    registry; that's registry.get_default_provider()'s job, so this
    function has no import-time dependency on providers/.
    """
    return os.environ.get("MARKET_DATA_PROVIDER", DEFAULT_MARKET_DATA_PROVIDER).strip().lower()


def get_provider_credential(provider_name: str, credential: str) -> str | None:
    """Read a provider credential, e.g. get_provider_credential("alpaca", "api_key_id").

    Env var naming convention: {PROVIDER}_{CREDENTIAL}, upper-cased --
    e.g. ALPACA_API_KEY_ID, ALPACA_SECRET_KEY, SCHWAB_CLIENT_ID.
    Returns None (never an empty string or a fabricated placeholder)
    when unset, so a provider can distinguish "not configured" from
    "configured as an empty string" and fail with a clear message
    instead of silently authenticating with nothing.
    """
    env_var = f"{provider_name.upper()}_{credential.upper()}"
    value = os.environ.get(env_var)
    return value if value else None


def get_database_path() -> str:
    """Where the SQLite database file for persisted historical bars lives
    (v0.1.17 -- see app/storage/db.py). Reads DATABASE_PATH (an absolute
    or relative file path), defaulting to DEFAULT_DATABASE_PATH above.
    Read fresh on every call, like every other function in this module --
    lets tests point at a throwaway path via monkeypatch.setenv without
    any caching to work around.
    """
    return os.environ.get("DATABASE_PATH", DEFAULT_DATABASE_PATH)


# --- Auto-ingestion (v0.1.18, app/ingestion/auto_ingest.py) ---
#
# Unattended, scheduled pulling of historical bars, as an alternative
# to a human clicking "Save to Database" after every manual fetch --
# see that module's docstring for the full design. OFF by default
# (get_auto_ingest_enabled() below): turning it on means the app
# process makes real, scheduled, credentialed calls to a real provider
# for as long as it's running, which is a meaningfully different thing
# from every other feature in this app (all of which are request-
# triggered) and should never happen just because the app started.

DEFAULT_AUTO_INGEST_SYMBOLS = "TSLA,NVDA"
DEFAULT_AUTO_INGEST_TIMEFRAMES = "1d"
DEFAULT_AUTO_INGEST_INTERVAL_SECONDS = 300
DEFAULT_AUTO_INGEST_LOOKBACK_DAYS = 5


def get_auto_ingest_enabled() -> bool:
    """Whether app/main.py should start the auto-ingest background loop
    at all. Reads AUTO_INGEST_ENABLED; unset or anything other than
    "true"/"1"/"yes"/"on" (case-insensitive) means disabled -- the safe
    default for a fresh checkout, a test run, or CI, none of which
    should make outbound network calls just because the app imported."""
    return os.environ.get("AUTO_INGEST_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def get_auto_ingest_symbols() -> list[str]:
    """Which symbols to poll. Reads AUTO_INGEST_SYMBOLS as a comma-
    separated list (e.g. "TSLA,NVDA"), defaulting to both symbols this
    app currently supports (see app/api/historical_data.py's
    ALLOWED_SYMBOLS) -- not validated against that set here, so a typo
    surfaces as a real per-pair error in the ingestion log
    (app/ingestion/auto_ingest.py) rather than a silent, separate check
    that could drift from ALLOWED_SYMBOLS over time.
    """
    raw = os.environ.get("AUTO_INGEST_SYMBOLS", DEFAULT_AUTO_INGEST_SYMBOLS)
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def get_auto_ingest_timeframes() -> list[str]:
    """Which timeframes to poll per symbol, e.g. "1d" or "5m,1h,1d".
    Reads AUTO_INGEST_TIMEFRAMES, defaulting to daily bars only."""
    raw = os.environ.get("AUTO_INGEST_TIMEFRAMES", DEFAULT_AUTO_INGEST_TIMEFRAMES)
    return [t.strip() for t in raw.split(",") if t.strip()]


def get_auto_ingest_provider() -> str:
    """Which MarketDataProvider the auto-ingest loop pulls from. Reads
    AUTO_INGEST_PROVIDER; defaults to get_configured_provider_name()
    (MARKET_DATA_PROVIDER) rather than a second, independent default --
    a deployment that already picked a provider for manual fetches
    almost always wants the unattended loop pulling from the same one,
    and this only needs setting separately when that's not true."""
    return os.environ.get("AUTO_INGEST_PROVIDER", get_configured_provider_name()).strip().lower()


def get_auto_ingest_interval_seconds() -> int:
    """How often (in seconds) the auto-ingest loop re-polls every
    configured symbol/timeframe pair. Reads AUTO_INGEST_INTERVAL_SECONDS,
    defaulting to 300 (5 minutes) -- frequent enough to catch a new
    daily bar the same day it closes without hammering a provider's
    rate limit every polling cycle."""
    return int(os.environ.get("AUTO_INGEST_INTERVAL_SECONDS", str(DEFAULT_AUTO_INGEST_INTERVAL_SECONDS)))


def get_auto_ingest_lookback_days() -> int:
    """How many trailing days each ingestion cycle re-fetches, not just
    "since last time" -- see app/ingestion/auto_ingest.py's
    run_ingestion_cycle() docstring for why re-fetching an overlapping
    window is deliberate rather than wasted work (the storage layer's
    UNIQUE-constraint dedup makes re-submitting an already-stored bar a
    no-op). Reads AUTO_INGEST_LOOKBACK_DAYS, defaulting to 5 -- enough
    to self-heal a gap left by a few missed cycles or a process that
    was down over a weekend, without asking for a provider's entire
    history every few minutes."""
    return int(os.environ.get("AUTO_INGEST_LOOKBACK_DAYS", str(DEFAULT_AUTO_INGEST_LOOKBACK_DAYS)))
