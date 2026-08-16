"""SchwabProvider -- live equity data (bars, latest quote).

Status: get_historical_data() and get_latest_quote() are real
integrations against Schwab's Trader API. get_chain() (options chain)
is still a placeholder, and stream_quotes() is unimplemented.

Schwab is a meaningfully different integration than alpaca_provider.py
/ massive_provider.py, for reasons worth reading before touching this
file:

1. AUTH IS OAUTH2, NOT A STATIC KEY. An access token lasts 30 minutes;
   a refresh token lasts 7 DAYS, after which it must be re-obtained via
   a full interactive browser login -- Schwab's login page, the
   account owner's credentials, MFA. This provider cannot do that step
   itself (it would mean handling brokerage login credentials, which
   this assistant will not do -- see scripts/schwab_oauth_bootstrap.py,
   which the account owner runs themselves). What THIS provider does
   automate is the ordinary token refresh (refresh_token -> new
   access_token), which needs no interactive login and can happen any
   time within that 7-day window.

2. RESPONSE FIELD NAMES ARE PARTIALLY UNCONFIRMED. Unlike Alpaca and
   Massive, Schwab's official API reference is gated behind an
   approved developer account -- it was not publicly reachable while
   writing this file (no Schwab developer app was available to test
   against). What's below is confirmed two different ways:
     - CONFIRMED via multiple independent, real client libraries'
       source code actually parsing live responses (not just docs
       prose): price-history candles are
       {"candles": [{"datetime": <ms>, "open":, "high":, "low":,
       "close":, "volume":}]}; quotes are keyed by symbol with a
       nested "quote" object: {SYMBOL: {"quote": {"bidPrice":,
       "askPrice":, "lastPrice":}}}.
     - NOT CONFIRMED: the quote timestamp field name. Schwab's Trader
       API is the successor to TD Ameritrade's (same company, by
       acquisition), and TDA's well-documented convention was
       "quoteTimeInLong" (epoch milliseconds) -- used below as a
       best-effort default, but this is the one field in this file
       that has NOT been verified against a real parsed response. If
       it's wrong, get_latest_quote() will raise a loud KeyError
       rather than silently return a wrong timestamp -- that's
       deliberate, not a gap to paper over.
   Whoever next has real Schwab API access should verify this against
   an actual response and update this docstring + remove this caveat.

3. Two more Schwab-specific auth quirks, confirmed: the OAuth
   authorization endpoint requires an HTTPS redirect URI even for a
   personal/local app (plain http://localhost is rejected) -- see the
   bootstrap script for how this is worked around without running a
   real HTTPS server. And unlike Alpaca/Massive's instant self-serve
   API keys, a Schwab developer app must be registered and approved
   before any of this works at all.

Endpoints (confirmed via multiple real client libraries' source, see
above):
    POST https://api.schwabapi.com/v1/oauth/token
    GET  https://api.schwabapi.com/marketdata/v1/pricehistory
    GET  https://api.schwabapi.com/marketdata/v1/quotes

Credentials: SCHWAB_CLIENT_ID / SCHWAB_CLIENT_SECRET / SCHWAB_REFRESH_TOKEN,
read via app.config -- the same generic get_provider_credential(provider,
credential) mechanism Alpaca/Massive use; no changes needed there.
SCHWAB_REFRESH_TOKEN has no self-serve equivalent to "generate an API
key" -- it comes from running scripts/schwab_oauth_bootstrap.py once
(and again every ~7 days when it expires), which is an interactive
step only the account owner can do.
"""

import base64
from datetime import date, datetime, timedelta, timezone

import httpx

from app import config
from app.models.market_data import MarketBar, MarketTimestamp, Quote
from app.providers.base import MarketDataProvider, NormalizedChainResult

DATA_BASE_URL = "https://api.schwabapi.com"
TOKEN_PATH = "/v1/oauth/token"
DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS = 1800  # 30 minutes, per Schwab's documented default
REFRESH_EARLY_SECONDS = 60  # refresh a bit before actual expiry, not exactly at it


def _to_epoch_ms(value: str | date | datetime) -> int:
    """Schwab's price-history start/end params are milliseconds since epoch."""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    else:
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _from_unix_ms(t_ms: int) -> datetime:
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc)


class SchwabProvider(MarketDataProvider):
    name = "schwab"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        client: httpx.Client | None = None,
        now: callable = None,
    ):
        self.client_id = client_id or config.get_provider_credential("schwab", "client_id")
        self.client_secret = client_secret or config.get_provider_credential("schwab", "client_secret")
        self.refresh_token = refresh_token or config.get_provider_credential("schwab", "refresh_token")
        # Injectable so tests never make a real network call -- see this
        # file's module docstring. Real usage leaves this as the default.
        self._client = client or httpx.Client(base_url=DATA_BASE_URL, timeout=10.0)
        # Injectable clock so tests can control token-expiry timing
        # deterministically instead of racing a real wall clock.
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._access_token: str | None = None
        self._access_token_expires_at: datetime | None = None

    def get_chain(self, **kwargs) -> NormalizedChainResult:
        """Options-chain data -- still a placeholder. This provider's
        first real integration covers equity bars/quotes only, matching
        Alpaca/Massive's scope."""
        self._require_credentials()
        raise NotImplementedError(
            "SchwabProvider does not implement options-chain data yet. "
            "get_historical_data() and get_latest_quote() (equity bars/quotes) "
            "are implemented; get_chain() is still a placeholder. "
            "See README's Phase 4 roadmap entry."
        )

    def get_historical_data(
        self,
        *,
        symbol: str,
        start: str | date | datetime,
        end: str | date | datetime,
        period_type: str = "day",
        frequency_type: str = "daily",
        frequency: int = 1,
    ) -> list[MarketBar]:
        data = self._get(
            "/marketdata/v1/pricehistory",
            {
                "symbol": symbol,
                "periodType": period_type,
                "frequencyType": frequency_type,
                "frequency": frequency,
                "startDate": _to_epoch_ms(start),
                "endDate": _to_epoch_ms(end),
            },
        )
        candles = data.get("candles") or []
        return [
            MarketBar(
                symbol=symbol,
                timestamp=MarketTimestamp(value=_from_unix_ms(candle["datetime"]), source=self.name),
                open=candle["open"],
                high=candle["high"],
                low=candle["low"],
                close=candle["close"],
                # round(), not int(): Massive's provider hit a real
                # ValidationError from a fractional volume value; this
                # provider hasn't been tested against real data yet, so
                # it defends the same way rather than assuming Schwab's
                # volume is always a clean integer.
                volume=round(candle["volume"]),
            )
            for candle in candles
        ]

    def get_latest_quote(self, *, symbol: str) -> Quote:
        data = self._get("/marketdata/v1/quotes", {"symbols": symbol})
        quote = data[symbol]["quote"]
        return Quote(
            symbol=symbol,
            bid=quote["bidPrice"],
            ask=quote["askPrice"],
            last=quote["lastPrice"],
            # quoteTimeInLong is NOT independently confirmed -- see
            # module docstring point 2. A KeyError here (rather than a
            # silently wrong timestamp) is the intended failure mode
            # until this is verified against a real account.
            timestamp=MarketTimestamp(value=_from_unix_ms(quote["quoteTimeInLong"]), source=self.name),
        )

    def _require_credentials(self) -> None:
        missing = [
            env_var
            for env_var, value in [
                ("SCHWAB_CLIENT_ID", self.client_id),
                ("SCHWAB_CLIENT_SECRET", self.client_secret),
                ("SCHWAB_REFRESH_TOKEN", self.refresh_token),
            ]
            if not value
        ]
        if missing:
            raise RuntimeError(
                "SchwabProvider requires " + ", ".join(missing) + " (env var(s) or explicit "
                "constructor arguments). SCHWAB_REFRESH_TOKEN has no self-serve equivalent -- "
                "it comes from a one-time interactive login: see scripts/schwab_oauth_bootstrap.py."
            )

    def _ensure_access_token(self) -> None:
        if (
            self._access_token is not None
            and self._access_token_expires_at is not None
            and self._now() < self._access_token_expires_at
        ):
            return
        self._refresh_access_token()

    def _refresh_access_token(self) -> None:
        self._require_credentials()
        basic_auth = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        response = self._client.post(
            TOKEN_PATH,
            data={"grant_type": "refresh_token", "refresh_token": self.refresh_token},
            headers={
                "Authorization": f"Basic {basic_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        response.raise_for_status()
        payload = response.json()
        self._access_token = payload["access_token"]
        expires_in = payload.get("expires_in", DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS)
        self._access_token_expires_at = self._now() + timedelta(seconds=expires_in - REFRESH_EARLY_SECONDS)

    def _get(self, path: str, params: dict) -> dict:
        self._ensure_access_token()
        response = self._client.get(
            path,
            params=params,
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        response.raise_for_status()
        return response.json()
