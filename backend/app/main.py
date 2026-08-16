"""Pandey Quant Lab -- FastAPI application entrypoint.

v0.1 scope: a transparent, manually-entered bear put spread
calculator, with an optional live equity quote lookup (v0.1.9) and a
real-time streaming quote (v0.1.12, Alpaca only -- see
app/api/market_data_stream.py) layered on top -- no brokerage
connectivity, no trade execution, no database, no auth. See the README
for the full list of assumptions and what is intentionally not
implemented yet.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.bear_put_spread import router as bear_put_spread_router
from app.api.csv_import import router as csv_import_router
from app.api.market_data import router as market_data_router
from app.api.market_data_stream import router as market_data_stream_router

app = FastAPI(
    title="Pandey Quant Lab API",
    description=(
        "Educational, transparent options calculator. "
        "Does not provide financial advice, execute trades, or connect to a brokerage."
    ),
    version="0.1.0",
)

# The frontend runs on Vite's default dev server port. This is a local,
# no-auth, single-user tool, so a permissive CORS setup for local dev
# origins is sufficient -- there is nothing further to secure yet.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(bear_put_spread_router, prefix="/api")
app.include_router(csv_import_router, prefix="/api")
app.include_router(market_data_router, prefix="/api")
app.include_router(market_data_stream_router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "pandey-quant-lab", "version": "0.1.0"}
