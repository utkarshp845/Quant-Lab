#!/usr/bin/env python3
"""Cross-validates AlpacaProvider vs. MassiveProvider against real accounts.

NOT run by pytest -- like alpaca_manual_check.py / massive_manual_check.py,
this makes real network calls with real credentials. Run it by hand for a
correctness signal beyond either provider's own mocked tests: two
independently-implemented providers agreeing on the same real data is a
much stronger signal than either one's tests passing in isolation -- the
same "two independent implementations checking each other" logic this app
already uses for Phase 2 (closed-form) vs. Phase 3 (Monte Carlo).

Usage:
    export ALPACA_API_KEY_ID=...
    export ALPACA_API_SECRET_KEY=...
    export MASSIVE_API_KEY=...
    cd backend && ./venv/bin/python scripts/cross_validate_providers.py

Compares daily bars for TSLA/NVDA over the last LOOKBACK_DAYS days, and
attempts a latest-quote comparison (skipped gracefully, not treated as a
failure, if a provider isn't entitled to it on your current plan -- see
massive_provider.py's docstring on the free-plan 403).

ONE EXPECTED, NOT-A-BUG DIVERGENCE: Alpaca's free tier uses the IEX-only
feed (a few percent of total US equity volume); Massive's aggregates
endpoint returns consolidated (all-exchange) volume. O/H/L/C should be
close for a liquid symbol's daily bar; VOLUME will NOT match -- that's a
feed-coverage difference, not a disagreement, and this script labels it
as such rather than flagging it.
"""

import sys
from datetime import date, timedelta

sys.path.insert(0, __file__.rsplit("/backend/", 1)[0] + "/backend")

from app.providers.alpaca_provider import AlpacaProvider  # noqa: E402
from app.providers.massive_provider import MassiveProvider  # noqa: E402

SYMBOLS = ["TSLA", "NVDA"]
LOOKBACK_DAYS = 10
PRICE_TOLERANCE_PCT = 1.0  # flag O/H/L/C differences beyond this -- volume is never flagged, see above


def pct_diff(a: float, b: float) -> float:
    if a == 0:
        return float("inf") if b != 0 else 0.0
    return abs(a - b) / abs(a) * 100


def compare_bars(symbol: str, alpaca: AlpacaProvider, massive: MassiveProvider) -> None:
    print(f"\n=== {symbol}: daily bars, last {LOOKBACK_DAYS} days ===")
    end = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)

    try:
        alpaca_bars = {
            b.timestamp.value.date(): b for b in alpaca.get_historical_data(symbol=symbol, start=start, end=end)
        }
    except Exception as exc:
        print(f"  Alpaca bars failed: {exc}")
        return
    try:
        massive_bars = {
            b.timestamp.value.date(): b for b in massive.get_historical_data(symbol=symbol, start=start, end=end)
        }
    except Exception as exc:
        print(f"  Massive bars failed: {exc}")
        return

    shared_dates = sorted(set(alpaca_bars) & set(massive_bars))
    if not shared_dates:
        print("  No overlapping trading days returned by both providers.")
        return

    any_flagged = False
    for d in shared_dates:
        a, m = alpaca_bars[d], massive_bars[d]
        o_diff, h_diff, l_diff, c_diff = (
            pct_diff(a.open, m.open),
            pct_diff(a.high, m.high),
            pct_diff(a.low, m.low),
            pct_diff(a.close, m.close),
        )
        flagged = max(o_diff, h_diff, l_diff, c_diff) > PRICE_TOLERANCE_PCT
        any_flagged = any_flagged or flagged
        marker = "⚠" if flagged else "✓"
        print(
            f"  {marker} {d}  "
            f"O: {a.open:.2f}/{m.open:.2f} ({o_diff:.2f}%)  "
            f"H: {a.high:.2f}/{m.high:.2f} ({h_diff:.2f}%)  "
            f"L: {a.low:.2f}/{m.low:.2f} ({l_diff:.2f}%)  "
            f"C: {a.close:.2f}/{m.close:.2f} ({c_diff:.2f}%)  "
            f"V: {a.volume:,} (Alpaca/IEX) vs {m.volume:,} (Massive/consolidated) -- expected to differ"
        )

    if any_flagged:
        print(f"  ⚠ One or more days exceeded the {PRICE_TOLERANCE_PCT}% O/H/L/C tolerance -- see above.")
    else:
        print(f"  ✓ All O/H/L/C values agree within {PRICE_TOLERANCE_PCT}% across {len(shared_dates)} shared day(s).")


def compare_latest_quote(symbol: str, alpaca: AlpacaProvider, massive: MassiveProvider) -> None:
    print(f"\n=== {symbol}: latest quote ===")

    a_quote = None
    try:
        a_quote = alpaca.get_latest_quote(symbol=symbol)
        print(f"  Alpaca:  bid {a_quote.bid} / ask {a_quote.ask} / last {a_quote.last} at {a_quote.timestamp.value}")
    except Exception as exc:
        print(f"  Alpaca quote failed: {exc}")

    m_quote = None
    try:
        m_quote = massive.get_latest_quote(symbol=symbol)
        print(f"  Massive: bid {m_quote.bid} / ask {m_quote.ask} / last {m_quote.last} at {m_quote.timestamp.value}")
    except RuntimeError as exc:
        # Confirmed real behavior, not a bug: Massive's free plan
        # doesn't include real-time quotes -- see massive_provider.py.
        print(f"  Massive quote skipped (not entitled on your plan): {exc}")
    except Exception as exc:
        print(f"  Massive quote failed: {exc}")

    if a_quote and m_quote and a_quote.last is not None and m_quote.last is not None:
        diff = pct_diff(a_quote.last, m_quote.last)
        marker = "⚠" if diff > PRICE_TOLERANCE_PCT else "✓"
        print(f"  {marker} Last-price diff: {diff:.2f}%")


def main() -> None:
    alpaca = AlpacaProvider()
    massive = MassiveProvider()
    for symbol in SYMBOLS:
        compare_bars(symbol, alpaca, massive)
        compare_latest_quote(symbol, alpaca, massive)


if __name__ == "__main__":
    main()
