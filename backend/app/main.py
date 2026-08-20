"""Pandey Quant Lab -- FastAPI application entrypoint.

v0.1 scope: a transparent, manually-entered bear put spread
calculator, with an optional live equity quote lookup (v0.1.9), a
real-time streaming quote (v0.1.12, Alpaca only -- see
app/api/market_data_stream.py), (v0.1.17) persisted storage for
fetched historical bars (a local SQLite file, app/storage/), and
(v0.1.18) an unattended auto-ingestion loop that keeps that storage
growing without a human clicking "Save to Database" every time (see
app/ingestion/auto_ingest.py), (Research v1, v0.1.20) a hypothesis-
testing engine that searches the normalized historical dataset for
every occurrence of a condition and measures its outcome (see
app/research/, app/api/research.py), (Feature Engine v1, v0.1.21) a
deterministic feature-computation layer that transforms normalized
bars into timestamped feature records for research (see app/features/,
app/api/features.py), and (Backtesting v1, v0.1.25) an event-based
historical backtester that walks an existing Research experiment's
bars chronologically, enters at the next bar's open, and measures
forward return/MFE/MAE at several configurable bar-count horizons (see
app/backtesting/, app/api/backtesting.py), and (OOS / Holdout Partition
Framework v1, v0.1.29) a way to explicitly split an existing symbol/
timeframe/provider's stored bars into a development window and a
later, non-overlapping holdout window, with development access
unrestricted and holdout access gated behind an explicit confirmation
flag (see app/oos/, app/api/oos_partitions.py -- statistical testing
against the holdout side is intentionally NOT implemented yet; this is
the partitioning/provenance boundary only), and (Experiment Freeze &
Provenance v1, v0.1.30) a way to freeze a Research Experiment's
hypothesis definition -- DRAFT -> FROZEN -> OOS_EVALUATED/ARCHIVED,
with a deterministic hypothesis_hash, an immutable point-in-time
snapshot, and an optional, validated link to an OOS partition (see
app/research/lifecycle.py, app/api/experiment_freeze.py -- the actual
OOS-evaluation operation is intentionally NOT implemented yet; this is
the lifecycle/provenance boundary only), and (OOS Evaluation v1,
v0.1.31) the actual OOS-evaluation operation itself -- given a FROZEN
experiment linked to an OOS partition, reads ONLY the partition's
holdout data (via app/oos/access.py::get_holdout_bars(...,
confirm_oos_validation_use=True), the sole holdout access path),
computes features under the frozen contract with bounded development
warm-up strictly for trailing-window context, evaluates the frozen
condition, and runs Backtesting v1's own unmodified engine against it,
persisting an append-only OOSEvaluationResult and advancing
FROZEN -> OOS_EVALUATED on success (see app/oos_evaluation/,
app/api/oos_evaluation.py), and (OOS Evidence Accumulation V1,
v0.1.33) lets an already-frozen experiment accumulate MORE THAN ONE
independent OOS evaluation period over time -- registering additional,
already-created OOS partitions as evaluation periods, running the SAME
OOS Evaluation v1 pipeline against each one (reused unmodified) against
the SAME immutable frozen snapshot, and a read-only aggregation across
every completed period that keeps raw (possibly correlated) signal
counts and truly-independent episode counts explicitly separate,
computes no statistical significance claim of any kind, and never
mutates the hypothesis, a prior evaluation, or development data (see
app/oos_evidence/, app/api/oos_evidence.py), and (OOS Statistical
Review V1, v0.1.34) a formal, READ-ONLY statistical review of that
accumulated evidence -- episode-level (never raw-signal) conditioned
observations, an OOS-scoped unconditional baseline built from the SAME
holdout time ranges (never development data), both of Statistical
Validation V2's dependence-aware baseline methods (reused unmodified),
a pre-specified primary horizon (the frozen hypothesis' own, never
horizon-searched), power/minimum-detectable-effect reporting, and a
deterministic SUPPORTED/NOT_SUPPORTED/INCONCLUSIVE/INSUFFICIENT_DATA
verdict that never equates a non-significant result with "false" or a
significant one with "profitable" -- persisting only new, immutable
review rows, never touching the hypothesis, a prior OOS evaluation, or
any other evidence (see app/oos_statistical_review/, app/api/
oos_statistical_review.py) -- layered on top -- no brokerage
connectivity, no trade execution, no auth. See the README for the full
list of assumptions and what is intentionally not implemented yet.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.api.backtesting import router as backtesting_router
from app.api.bear_put_spread import router as bear_put_spread_router
from app.api.csv_import import router as csv_import_router
from app.api.historical_comparison import router as historical_comparison_router
from app.api.historical_data import router as historical_data_router
from app.api.historical_storage import router as historical_storage_router
from app.api.features import router as features_router
from app.api.experiment_freeze import router as experiment_freeze_router
from app.api.market_data import router as market_data_router
from app.api.market_data_stream import router as market_data_stream_router
from app.api.oos_evaluation import router as oos_evaluation_router
from app.api.oos_evidence import router as oos_evidence_router
from app.api.oos_partitions import router as oos_partitions_router
from app.api.oos_statistical_review import router as oos_statistical_review_router
from app.api.research import router as research_router
from app.api.research_lineage import router as research_lineage_router
from app.api.research_notebook import router as research_notebook_router
from app.api.research_pipeline import router as research_pipeline_router
from app.api.statistical_validation import router as statistical_validation_router
from app.ingestion.auto_ingest import run_ingestion_loop

# v0.1.23: without this, every `app.*` logger.info()/.warning() call in
# this codebase (auto-ingest's cycle-by-cycle results, its "disabled at
# startup" notice, PairFailureTracker's escalation/recovery lines, ...)
# was silently invisible -- Python's root logger defaults to WARNING
# with no handler attached, so INFO records never even reach output,
# and WARNING/ERROR records fall back to logging.lastResort's bare,
# unformatted stderr line. Confirmed by a real run: the auto-ingest
# "disabled" notice never appeared in server logs even with
# AUTO_INGEST_ENABLED unset, before this fix. A no-op if something else
# already configured the root logger (basicConfig() itself no-ops
# without force=True when root already has a handler -- e.g. under
# pytest, where the logging plugin installs its own capture handler
# before this module is ever imported) -- this only takes effect for a
# real `uvicorn app.main:app` process that hasn't configured logging
# itself.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

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
app.include_router(features_router, prefix="/api")
app.include_router(backtesting_router, prefix="/api")
app.include_router(oos_partitions_router, prefix="/api")
app.include_router(experiment_freeze_router, prefix="/api")
app.include_router(oos_evaluation_router, prefix="/api")
app.include_router(oos_evidence_router, prefix="/api")
app.include_router(oos_statistical_review_router, prefix="/api")
app.include_router(research_notebook_router, prefix="/api")
app.include_router(research_pipeline_router, prefix="/api")
app.include_router(research_lineage_router, prefix="/api")
app.include_router(statistical_validation_router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "pandey-quant-lab", "version": "0.1.0"}
