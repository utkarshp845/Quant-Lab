"""WebSocket route for continuous live market-data updates (v0.1.12) --
the first server-push mechanism in this app; every other route so far
is plain request/response (see app/api/market_data.py's GET
.../quote, the manual one-shot lookup this complements, not replaces).

Scoped narrowly, matching this feature's roadmap step: Alpaca only, and
in practice only ever asked for TSLA today (the frontend hardcodes
symbol=TSLA -- see frontend/src/pages/CalculatorPage.tsx). The route
itself doesn't hardcode the symbol, since nothing about it is
Alpaca-symbol-specific, but `provider` is currently required to be
"alpaca" -- an unsupported value gets a clear error frame and the
socket closes, the same "fail loud, not quiet" convention the REST
quote route already follows for an unknown provider name.

Protocol: the client opens
    ws://localhost:8000/api/market-data/stream?symbol=TSLA&provider=alpaca
and receives a stream of JSON text frames, each one of:
    {"type": "status", "status": "connecting"|"connected"|"disconnected"|"error", "detail": string|null}
    {"type": "quote", "quote": {...same LiveQuote shape GET .../quote returns...}}
The client never sends anything after connecting -- this is server
push only, matching what the frontend actually needs (see
frontend/src/hooks/useAlpacaQuoteStream.ts). No Alpaca credentials are
ever part of any frame; they stay backend-side in app.config, read by
app.streaming.alpaca_stream -- see that module's docstring.

Fan-out and reconnection to Alpaca itself are the hub's job (see
app/streaming/hub.py); this route only does per-client plumbing: accept
the socket, subscribe to the hub, relay whatever comes off this
client's queue, and unsubscribe on disconnect (in a `finally`, so a
client that just closes its tab still cleans up its slot and lets the
hub tear down the upstream connection once nobody's left watching).
"""

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.streaming.hub import hub

router = APIRouter()

DEFAULT_SYMBOL = "TSLA"
SUPPORTED_STREAM_PROVIDERS = {"alpaca"}


@router.websocket("/market-data/stream")
async def stream_quotes(
    websocket: WebSocket,
    symbol: str = Query(DEFAULT_SYMBOL),
    provider: str = Query("alpaca"),
) -> None:
    await websocket.accept()

    if provider not in SUPPORTED_STREAM_PROVIDERS:
        await websocket.send_json(
            {
                "type": "status",
                "status": "error",
                "detail": f"Unsupported streaming provider: {provider!r}. Only 'alpaca' is supported so far.",
            }
        )
        await websocket.close(code=1008)  # policy violation -- not a retryable state
        return

    queue = await hub.subscribe(symbol)
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
        await hub.unsubscribe(symbol, queue)
