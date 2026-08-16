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
