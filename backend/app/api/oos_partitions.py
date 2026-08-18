"""API routes for the OOS / Holdout Partition Framework v1:

    POST /oos/partitions                              create (idempotently) a partition
    GET  /oos/partitions                               every saved partition, newest first
                                                        (optionally filtered by symbol/timeframe/provider)
    GET  /oos/partitions/{id}                          one partition
    GET  /oos/partitions/{id}/development/bars         bars in the development window (unrestricted)
    GET  /oos/partitions/{id}/holdout/bars              bars in the holdout window (requires
                                                        ?confirm_oos_validation_use=true)

Reads ONLY app.storage.historical_bar_repository, via app.oos.access --
never duplicates a bar, never writes to `historical_bars` (matching
app/api/research.py's own "Research must never modify historical data"
boundary, applied here to partitions too). Symbol/timeframe are
validated against the SAME ALLOWED_SYMBOLS/ALLOWED_TIMEFRAMES sets
app/api/research.py already uses, imported from
app/api/historical_data.py -- one scope fence, not a second, parallel
one.

The holdout route's 403 is this task's actual, load-bearing boundary:
GET .../holdout/bars with no query param (or `confirm_oos_validation_use=false`,
the default) is refused outright, matching app/oos/access.py's own
HoldoutAccessError -- see that module's docstring for exactly what this
does and does not guard against.
"""

from fastapi import APIRouter, HTTPException, Query

from app.api.historical_data import ALLOWED_SYMBOLS, ALLOWED_TIMEFRAMES
from app.models.market_data import HistoricalBar
from app.models.oos_partition import OOSPartition, OOSPartitionCreateRequest
from app.oos.access import HoldoutAccessError, get_development_bars, get_holdout_bars
from app.storage import oos_partition_repository

router = APIRouter()


def _validate_request(request: OOSPartitionCreateRequest) -> None:
    symbol = request.symbol.upper()
    if symbol not in ALLOWED_SYMBOLS:
        raise HTTPException(
            status_code=400, detail=f"Symbol {symbol!r} is not supported yet. Allowed: {sorted(ALLOWED_SYMBOLS)}"
        )
    if request.timeframe not in ALLOWED_TIMEFRAMES:
        raise HTTPException(
            status_code=400, detail=f"Unsupported timeframe {request.timeframe!r}. Allowed: {sorted(ALLOWED_TIMEFRAMES)}"
        )


def _get_or_404(partition_id: str) -> OOSPartition:
    partition = oos_partition_repository.get_partition(partition_id)
    if partition is None:
        raise HTTPException(status_code=404, detail=f"No OOS partition with id {partition_id!r}")
    return partition


@router.post("/oos/partitions", response_model=OOSPartition)
def create_partition(request: OOSPartitionCreateRequest) -> OOSPartition:
    """Range ordering (development_end < holdout_start, both windows
    start < end) is already enforced by OOSPartitionCreateRequest's own
    pydantic validators -- a malformed request never reaches this
    handler at all (FastAPI turns that into a 422 automatically).
    Creating the identical partition twice is a no-op that returns the
    original record (see oos_partition_repository.save_partition()'s
    idempotency, requirement 5)."""
    _validate_request(request)
    partition = OOSPartition.new(request)
    oos_partition_repository.save_partition(partition)
    return oos_partition_repository.get_partition(partition.id)


@router.get("/oos/partitions", response_model=list[OOSPartition])
def list_partitions(
    symbol: str | None = Query(None),
    timeframe: str | None = Query(None),
    provider: str | None = Query(None),
) -> list[OOSPartition]:
    return oos_partition_repository.list_partitions(symbol=symbol, timeframe=timeframe, provider=provider)


@router.get("/oos/partitions/{partition_id}", response_model=OOSPartition)
def get_partition(partition_id: str) -> OOSPartition:
    return _get_or_404(partition_id)


@router.get("/oos/partitions/{partition_id}/development/bars", response_model=list[HistoricalBar])
def get_development_segment_bars(partition_id: str) -> list[HistoricalBar]:
    return get_development_bars(_get_or_404(partition_id))


@router.get("/oos/partitions/{partition_id}/holdout/bars", response_model=list[HistoricalBar])
def get_holdout_segment_bars(
    partition_id: str,
    confirm_oos_validation_use: bool = Query(
        False,
        description=(
            "Must be explicitly true. Holdout data is reserved for a future, explicitly designated "
            "OOS-validation operation -- this flag exists so accessing it can never happen by accident "
            "while iterating on a hypothesis."
        ),
    ),
) -> list[HistoricalBar]:
    partition = _get_or_404(partition_id)
    try:
        return get_holdout_bars(partition, confirm_oos_validation_use=confirm_oos_validation_use)
    except HoldoutAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
