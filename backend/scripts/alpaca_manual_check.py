#!/usr/bin/env python3
"""Manual, opt-in sanity check for AlpacaProvider against your real account.

NOT run by pytest / CI -- this makes real network calls using your real
credentials, which the automated test suite deliberately never does
(see tests/test_alpaca_provider.py, which mocks every request). Run
this by hand when you want to confirm your own ALPACA_API_KEY_ID /
ALPACA_API_SECRET_KEY actually work end-to-end.

Usage:
    export ALPACA_API_KEY_ID=...
    export ALPACA_API_SECRET_KEY=...
    cd backend && ./venv/bin/python scripts/alpaca_manual_check.py

Prints the last 5 daily bars and the latest quote for TSLA and NVDA on
the free "iex" feed. Exits non-zero with the underlying error if
credentials are missing or the API rejects the request (e.g. wrong
key, feed not entitled) -- this script does not hide failures.
"""

import sys
from datetime import date, timedelta

sys.path.insert(0, __file__.rsplit("/backend/", 1)[0] + "/backend")  # allow running from anywhere

from app.providers.alpaca_provider import AlpacaProvider  # noqa: E402

SYMBOLS = ["TSLA", "NVDA"]


def main() -> None:
    provider = AlpacaProvider()  # reads ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY from the environment

    today = date.today()
    start = today - timedelta(days=10)

    for symbol in SYMBOLS:
        print(f"\n=== {symbol} ===")

        bars = provider.get_historical_data(symbol=symbol, start=start, end=today)
        print(f"{len(bars)} daily bar(s), last 5:")
        for bar in bars[-5:]:
            print(
                f"  {bar.timestamp.value:%Y-%m-%d}  "
                f"O:{bar.open:.2f} H:{bar.high:.2f} L:{bar.low:.2f} C:{bar.close:.2f} V:{bar.volume:,}"
            )

        quote = provider.get_latest_quote(symbol=symbol)
        print(f"Latest quote: bid {quote.bid} / ask {quote.ask} / last {quote.last} at {quote.timestamp.value}")


if __name__ == "__main__":
    main()
