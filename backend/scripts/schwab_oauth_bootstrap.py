#!/usr/bin/env python3
"""One-time (well, roughly weekly) interactive Schwab OAuth bootstrap.

WHY THIS SCRIPT EXISTS AND WHY IT'S INTERACTIVE: Schwab's Trader API
uses OAuth2. A refresh token lasts 7 days; getting the FIRST one (and
every one after the previous expires) requires an interactive login on
Schwab's own site -- your credentials, your MFA. Nothing in this
codebase automates that step, on purpose: this assistant does not log
into brokerage accounts on your behalf. What this script automates is
everything AROUND that manual step -- building the login URL, and
exchanging the authorization code you get back for a refresh token.

WHAT YOU NEED FIRST:
  - A Schwab developer app registered and approved at
    https://developer.schwab.com, with a callback/redirect URI
    configured. Schwab requires this URI to be HTTPS, even for a
    personal app -- a plain http://localhost redirect is rejected.
    You do NOT need a real server running at that URI; see step 3.
  - SCHWAB_CLIENT_ID, SCHWAB_CLIENT_SECRET, SCHWAB_REDIRECT_URI set
    (matching exactly what's registered in your Schwab app) --
    export them, or put them in backend/.env and `set -a && source
    .env && set +a` first.

USAGE:
    export SCHWAB_CLIENT_ID=...
    export SCHWAB_CLIENT_SECRET=...
    export SCHWAB_REDIRECT_URI=...   # must match your registered app exactly
    cd backend && ./venv/bin/python scripts/schwab_oauth_bootstrap.py

STEPS THIS SCRIPT WALKS YOU THROUGH:
    1. It prints an authorization URL.
    2. You open that URL in YOUR OWN browser and log into Schwab
       yourself -- this script never sees your Schwab credentials.
    3. Schwab redirects your browser to SCHWAB_REDIRECT_URI with a
       `?code=...` query parameter. Since nothing is actually running
       at that URI, your browser will likely show a "can't reach this
       page" / connection-refused error -- that's expected. What
       matters is the URL in your browser's address bar.
    4. Copy that FULL URL and paste it back into this script when
       prompted.
    5. The script exchanges the code for a refresh token and prints
       it, with instructions to put it in backend/.env.

The refresh token printed at the end is a secret, same as any other
credential in this app -- don't paste it anywhere but your own
backend/.env, and don't share your terminal output.
"""

import base64
import os
import sys
import urllib.parse

import httpx

TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"


def main() -> None:
    client_id = os.environ.get("SCHWAB_CLIENT_ID")
    client_secret = os.environ.get("SCHWAB_CLIENT_SECRET")
    redirect_uri = os.environ.get("SCHWAB_REDIRECT_URI")

    missing = [
        name
        for name, value in [
            ("SCHWAB_CLIENT_ID", client_id),
            ("SCHWAB_CLIENT_SECRET", client_secret),
            ("SCHWAB_REDIRECT_URI", redirect_uri),
        ]
        if not value
    ]
    if missing:
        print(f"Missing environment variable(s): {', '.join(missing)}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    auth_url = f"{AUTHORIZE_URL}?" + urllib.parse.urlencode({"client_id": client_id, "redirect_uri": redirect_uri})

    print("1. Open this URL in your own browser and log into Schwab yourself:\n")
    print(f"   {auth_url}\n")
    print("2. After you approve, your browser will be redirected to your")
    print("   SCHWAB_REDIRECT_URI with a ?code=... parameter. It's normal")
    print("   for that page to fail to load -- nothing is running there.")
    print("3. Copy the FULL resulting URL from your browser's address bar.\n")

    redirected_url = input("Paste the full redirected URL here: ").strip()

    parsed = urllib.parse.urlparse(redirected_url)
    query = urllib.parse.parse_qs(parsed.query)
    code = query.get("code", [None])[0]
    if not code:
        print("No `code` parameter found in that URL -- did you paste the whole thing?", file=sys.stderr)
        sys.exit(1)
    code = urllib.parse.unquote(code)  # Schwab's codes commonly arrive percent-encoded

    basic_auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    response = httpx.post(
        TOKEN_URL,
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        headers={
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    response.raise_for_status()
    payload = response.json()

    print("\nSuccess. Access token acquired (expires in "
          f"{payload.get('expires_in', '?')} seconds -- SchwabProvider refreshes this automatically).")
    print("\nAdd this to backend/.env (it's a secret -- treat it like a password):\n")
    print(f"SCHWAB_REFRESH_TOKEN={payload['refresh_token']}\n")
    print("This refresh token is valid for ~7 days. Re-run this script when it expires.")


if __name__ == "__main__":
    main()
