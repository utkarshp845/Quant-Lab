"""WebSocket route for continuous live market-data updates (v0.1.12) --
the first server-push mechanism in this app; every other route so far
is plain request/response (see app/api/market_data.py's GET
.../quote, the manual one-shot lookup this complements, not replaces).

Scoped to whichever providers actually implement real-time streaming
(Alpaca since v0.1.12, Massive since v0.1.13 -- see
app/streaming/hub.py's STREAM_FACTORIES; Schwab does not, yet), and in
practice only ever asked for TSLA today (the frontend hardcodes
symbol=TSLA -- see frontend/src/pages/CalculatorPage.tsx). The route
itself doesn't hardcode the symbol, since nothing about it is
symbol-specific.

Protocol: the client opens
    ws://localhost:8000/api/market-data/stream?symbol=TSLA&provider=alpaca
(or provider=massive) and receives a stream of JSON text frames, each
one of:
    {"type": "status", "status": "connecting"|"connected"|"disconnected"|"error", "detail": string|null}
    {"type": "quote", "quote": {...same LiveQuote shape GET .../quote returns...}}
The client never sends anything after connecting -- this is server
push only, matching what the frontend actually needs (see
frontend/src/hooks/useQuoteStream.ts). No provider credentials are
ever part of any frame; they stay backend-side in app.config, read by
each app/streaming/*_stream.py module.

An unsupported `provider` value gets a clear error frame and the
socket closes, the same "fail loud, not quiet" convention the REST
quote route already follows for an unknown provider name -- checked
against the hub's own STREAM_FACTORIES map (app/streaming/hub.py)
rather than a separately-maintained list, so this route and the hub
can never disagree about which providers stream.

Fan-out and reconnection to the upstream provider are the hub's job
(see app/streaming/hub.py); this route only does per-client plumbing:
accept the socket, subscribe to the hub, relay whatever comes off this
client's queue, and unsubscribe on disconnect (in a `finally`, so a
client that just closes its tab still cleans up its slot and lets the
hub tear down the upstream connection once nobody's left watching).
"""

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.streaming.hub import hub

router = APIRouter()

DEFAULT_SYMBOL = "TSLA"


@router.websocket("/market-data/stream")
async def stream_quotes(
    websocket: WebSocket,
    symbol: str = Query(DEFAULT_SYMBOL),
    provider: str = Query("alpaca"),
) -> None:
    await websocket.accept()

    if not hub.supports(provider):
        await websocket.send_json(
            {
                "type": "status",
                "status": "error",
                "detail": f"Unsupported streaming provider: {provider!r}.",
            }
        )
        await websocket.close(code=1008)  # policy violation -- not a retryable state
        return

    queue = await hub.subscribe(provider, symbol)
    try:
        while True:
            kind, a, b = await queue.get()
            if kind == "status":
                await websocket.send_json({"type": "status", "status": a, "detail": b})
            else:
                await websocket.send_json({"type": "quote", "quote": a.model_dump(mode="json")})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe(provider, symbol, queue)
