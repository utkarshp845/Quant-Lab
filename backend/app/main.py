"""Pandey Quant Lab -- FastAPI application entrypoint.

v0.1 scope: a transparent, manually-entered bear put spread
calculator, with an optional live equity quote lookup (v0.1.9), a
real-time streaming quote (v0.1.12, Alpaca only -- see
app/api/market_data_stream.py), (v0.1.17) persisted storage for
fetched historical bars (a local SQLite file, app/storage/), and
(v0.1.18) an unattended auto-ingestion loop that keeps that storage
growing without a human clicking "Save to Database" every time (see
app/ingestion/auto_ingest.py), and (Research v1, v0.1.20) a hypothesis-
testing engine that searches the normalized historical dataset for
every occurrence of a condition and measures its outcome (see
app/research/, app/api/research.py) -- layered on top -- no brokerage
connectivity, no trade execution, no auth. See the README for the full
list of assumptions and what is intentionally not implemented yet.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.api.bear_put_spread import router as bear_put_spread_router
from app.api.csv_import import router as csv_import_router
from app.api.historical_comparison import router as historical_comparison_router
from app.api.historical_data import router as historical_data_router
from app.api.historical_storage import router as historical_storage_router
from app.api.market_data import router as market_data_router
from app.api.market_data_stream import router as market_data_stream_router
from app.api.research import router as research_router
from app.ingestion.auto_ingest import run_ingestion_loop

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Starts the v0.1.18 auto-ingest background loop alongside the app
    -- and ONLY if AUTO_INGEST_ENABLED says to (see app/config.py's
    get_auto_ingest_enabled(): unset/false by default, so a fresh
    checkout, a test run, or CI never makes an outbound provider call
    just because the app process started). `stop_event` is how shutdown
    asks the loop to end its current sleep and exit cleanly instead of
    being killed mid-cycle -- see run_ingestion_loop()'s own docstring.
    """
    stop_event = asyncio.Event()
    task: asyncio.Task | None = None
    if config.get_auto_ingest_enabled():
        task = asyncio.create_task(run_ingestion_loop(stop_event=stop_event))
    else:
        logger.info(
            "auto-ingest disabled (set AUTO_INGEST_ENABLED=true to turn it on) -- "
            "historical bars are only saved via a manual Save to Database call."
        )

    yield

    if task is not None:
        stop_event.set()
        await task


app = FastAPI(
    title="Pandey Quant Lab API",
    description=(
        "Educational, transparent options calculator. "
        "Does not provide financial advice, execute trades, or connect to a brokerage."
    ),
    version="0.1.0",
    lifespan=lifespan,
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
app.include_router(historical_data_router, prefix="/api")
app.include_router(historical_comparison_router, prefix="/api")
app.include_router(historical_storage_router, prefix="/api")
app.include_router(research_router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "pandey-quant-lab", "version": "0.1.0"}
