"""Backend-owned real-time market-data streaming (v0.1.12).

Everything in this package is server-side only: it holds the upstream
connection to a broker's streaming API (Alpaca, for now -- see
alpaca_stream.py) and the credentials that connection needs
(app.config, same as every REST provider), and hands normalized
LiveQuote updates to app/api/market_data_stream.py, which relays them
to the frontend over its own WebSocket. No frontend code, and no
non-streaming route, imports anything from this package.

    alpaca_stream.py  -- one upstream connection to Alpaca's streaming
                          API for one symbol; owns auth, subscribe,
                          message normalization, and reconnect/backoff.
    hub.py            -- keeps exactly one upstream connection alive per
                          symbol no matter how many frontend clients are
                          watching (Alpaca allows only one connection
                          per API key), and fans out its updates.
"""
