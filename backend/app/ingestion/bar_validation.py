"""The explicit Validate step in this app's bar pipeline (v0.1.18):

    Receive (a provider's raw response, app/providers/*.py)
      -> Parse   (that same file, raw JSON -> MarketBar)
      -> Normalize (HistoricalBar.from_market_bar / the route/service
                     calling it -- one provider-independent shape)
      -> VALIDATE (THIS FILE)
      -> Store   (app/storage/, only for what THIS FILE accepted)

Every caller that can put a bar in front of the storage layer --
POST /market-data/history/save (a human's "Save to Database" click)
and app/ingestion/auto_ingest.py (the unattended polling loop, v0.1.18)
alike -- runs bars through validate_bars() first. Neither of them talks
to sqlite directly, and this module never imports app.storage: keeping
the dependency one-directional (ingestion -> nothing storage-shaped)
means this file has no way to know or care how a bar ends up persisted,
only whether it deserves to be.

By the time a HistoricalBar object exists at all, Pydantic has already
done checks #1-3 of the design this module implements -- required
fields present, correct types, a real parseable timestamp -- rejecting
anything short of that with a 422 before it ever reaches here (see
app.models.market_data.HistoricalBar's field list: nothing is
Optional). What Pydantic *can't* check is whether the values make
sense together. That's this module's whole job, split into two
severities:

  HARD (-> RejectedBar, never stored, but never silently dropped
  either -- see app/storage/db.py's `quarantined_bars` table):
    #5  positive prices, non-negative volume
    #6  OHLC relationships (low <= open/close <= high, high >= low)
    #7  duplicate detection (same provider+symbol+timeframe+timestamp
        appearing twice in the same batch -- the natural identity a
        second, already-stored copy would collide on anyway, made
        explicit and reported here instead of silently absorbed by the
        storage layer's UNIQUE constraint)
    also: a timestamp more than a few minutes in the future (garbage/
        clock-skew data, not a real observation yet)

  SOFT (-> ValidatedBar with status=FLAGGED; still stored -- these are
  "worth a second look," not "wrong"):
    #8  timestamp ordering -- arrived out of chronological order
        within its own batch
    #9  gap detection -- an unusually large gap since the previous bar
        in its series
    #10 extreme-move flagging -- an unusually large price move from
        the previous bar in its series

  Also handled here, not a separate check:
    #4  UTC normalization -- every accepted/rejected bar's timestamp is
        normalized to a UTC-aware datetime before this function returns

  Not this module's job:
    #11 source metadata -- already carried end-to-end as
        HistoricalBar.provider (see that model's docstring); nothing
        to add here.
    #12 quarantine -- this module classifies; app/storage/ is what
        actually persists RejectedBar rows to `quarantined_bars` (see
        historical_bar_repository.save_rejected_bars()). Likewise
        `ingested_at`/`validation_status`/`validation_errors` are
        storage-layer columns, set at insert time -- this module only
        produces the ValidationStatus/warnings/reasons that end up
        written there.

Deliberately NOT a machine-learning anomaly detector: every rule below
is a plain, explainable threshold a human reading this file can verify
by hand. That's the actual point for a v1 -- an auditable pipeline, not
a black box.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.models.market_data import HistoricalBar
from app.models.validation import RejectedBar, ValidatedBar, ValidationStatus

# A bar timestamped further into the future than this is treated as
# bad data (a miscoded timestamp, e.g. seconds mistaken for millis)
# rather than a real observation -- generous enough to absorb ordinary
# clock skew between this machine and a provider's server.
_FUTURE_TOLERANCE = timedelta(minutes=5)

# Nominal seconds-per-bar for this app's normalized timeframe
# vocabulary (see app/api/historical_data.py's ALLOWED_TIMEFRAMES --
# not imported from there to avoid an ingestion -> api dependency;
# this is its own small, independent copy). A timeframe string not
# listed here (a future provider-specific value) just means gap
# detection is skipped for that series -- an unrecognized granularity
# is honestly "no basis for a gap threshold," not a guess.
_TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "1d": 86400,
}

# A gap more than this many multiples of the nominal bar interval is
# flagged. 5x a daily bar's interval (~5 calendar days) comfortably
# absorbs an ordinary weekend/holiday without firing; several missing
# trading days in a row is exactly the kind of hole in the dataset
# worth surfacing.
_GAP_MULTIPLIER = 5

# A close-to-close move bigger than this fraction from the previous
# bar in its series is flagged. 20% is deliberately loose -- real
# stocks gap this much around earnings/news -- this is a "worth a
# second look" flag, not a claim the data is wrong.
_EXTREME_MOVE_THRESHOLD = 0.20


@dataclass
class BarValidationResult:
    """What validate_bars() decided about an entire batch -- mirrors
    SaveResult's "don't collapse different outcomes into one" reasoning
    (app/storage/historical_bar_repository.py): `accepted` (valid or
    flagged, both storable) and `rejected` (quarantined, never stored)
    are different enough outcomes that a caller needs both, separately,
    to report or persist correctly."""

    accepted: list[ValidatedBar]
    rejected: list[RejectedBar]


def validate_bars(bars: list[HistoricalBar]) -> BarValidationResult:
    """Runs every bar in `bars` through the hard structural checks and
    in-batch duplicate detection (order-independent, per bar), then
    groups whatever survives by series (provider+symbol+timeframe) to
    run the batch-shape checks -- arrival order, gaps, extreme moves --
    that only make sense compared against a bar's neighbors. Never
    raises: bad data from a provider is something to classify and
    report, never a reason to crash a save or an ingestion cycle.

    Accepted bars are returned in the same relative order as `bars`
    (rejected/duplicate entries simply removed) -- this function never
    reorders what a caller eventually stores.
    """
    survivors: list[HistoricalBar] = []
    rejected: list[RejectedBar] = []
    seen_keys: set[tuple[str, str, str, str]] = set()

    for raw_bar in bars:
        bar = _normalize_utc(raw_bar)
        reasons = _structural_violations(bar)

        key = (bar.provider, bar.symbol.upper(), bar.timeframe, bar.timestamp.isoformat())
        if key in seen_keys:
            reasons = [*reasons, "duplicate of an earlier bar in this batch (same provider/symbol/timeframe/timestamp)"]

        if reasons:
            rejected.append(RejectedBar(bar=bar, reasons=reasons))
            continue

        seen_keys.add(key)
        survivors.append(bar)

    warnings_by_id: dict[int, list[str]] = defaultdict(list)
    series: dict[tuple[str, str, str], list[HistoricalBar]] = defaultdict(list)
    for bar in survivors:
        series[(bar.provider, bar.symbol.upper(), bar.timeframe)].append(bar)

    for group in series.values():
        _flag_out_of_order_arrivals(group, warnings_by_id)
        _flag_gaps_and_extreme_moves(group, warnings_by_id)

    accepted = [
        ValidatedBar(
            bar=bar,
            status=ValidationStatus.FLAGGED if warnings_by_id[id(bar)] else ValidationStatus.VALID,
            warnings=warnings_by_id[id(bar)],
        )
        for bar in survivors
    ]

    return BarValidationResult(accepted=accepted, rejected=rejected)


def _normalize_utc(bar: HistoricalBar) -> HistoricalBar:
    """Check #4. A naive timestamp (no tzinfo) is assumed to already be
    UTC -- the same documented assumption
    historical_bar_repository.py's _to_storage_timestamp() falls back
    to -- an aware one is converted to UTC rather than stored in
    whatever offset it arrived with. Made explicit and auditable here,
    upstream of storage, rather than left as an implicit side effect of
    the insert."""
    timestamp = bar.timestamp
    normalized = timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp.astimezone(timezone.utc)
    return bar.model_copy(update={"timestamp": normalized})


def _structural_violations(bar: HistoricalBar) -> list[str]:
    """Checks #5, #6, and the future-timestamp bound -- every rule
    checked, not just the first failure, so a rejected bar's `reasons`
    shows the whole picture at once."""
    reasons: list[str] = []

    for label, value in (("open", bar.open), ("high", bar.high), ("low", bar.low), ("close", bar.close)):
        if value <= 0:
            reasons.append(f"{label} ({value}) is not a positive price")

    if bar.volume < 0:
        reasons.append(f"volume ({bar.volume}) is negative")

    if bar.high < bar.low:
        reasons.append(f"high ({bar.high}) is less than low ({bar.low})")
    else:
        # Only checked once high/low are themselves sane -- otherwise
        # this would just restate the high<low finding above in
        # confusing terms.
        if not (bar.low <= bar.open <= bar.high):
            reasons.append(f"open ({bar.open}) is outside [low, high] = [{bar.low}, {bar.high}]")
        if not (bar.low <= bar.close <= bar.high):
            reasons.append(f"close ({bar.close}) is outside [low, high] = [{bar.low}, {bar.high}]")

    if bar.timestamp > datetime.now(timezone.utc) + _FUTURE_TOLERANCE:
        reasons.append(f"timestamp ({bar.timestamp.isoformat()}) is more than {_FUTURE_TOLERANCE} in the future")

    return reasons


def _flag_out_of_order_arrivals(group: list[HistoricalBar], warnings_by_id: dict[int, list[str]]) -> None:
    """Check #8. `group` is in submission (batch/list) order, per its
    series -- flags a bar whose timestamp is earlier than the latest
    timestamp already seen earlier in the same submission, which is
    the concrete meaning of "this series didn't arrive in chronological
    order." Exact-duplicate timestamps within a series never reach
    here -- validate_bars() already rejected the second occurrence
    before grouping, so there's nothing to compare equal timestamps
    against."""
    latest_seen: datetime | None = None
    for bar in group:
        if latest_seen is not None and bar.timestamp < latest_seen:
            warnings_by_id[id(bar)].append(
                f"arrived out of chronological order within its batch (timestamp {bar.timestamp.isoformat()} "
                f"came after a later timestamp {latest_seen.isoformat()} earlier in the same submission)"
            )
        else:
            latest_seen = bar.timestamp


def _flag_gaps_and_extreme_moves(group: list[HistoricalBar], warnings_by_id: dict[int, list[str]]) -> None:
    """Checks #9 and #10. Unlike the arrival-order check above, these
    only make sense in actual chronological order, so `group` is
    re-sorted by timestamp here rather than reusing submission order."""
    chronological = sorted(group, key=lambda b: b.timestamp)
    for previous, current in zip(chronological, chronological[1:]):
        nominal_seconds = _TIMEFRAME_SECONDS.get(current.timeframe)
        if nominal_seconds:
            gap_seconds = (current.timestamp - previous.timestamp).total_seconds()
            if gap_seconds > nominal_seconds * _GAP_MULTIPLIER:
                gap_multiple = gap_seconds / nominal_seconds
                warnings_by_id[id(current)].append(
                    f"gap of {timedelta(seconds=gap_seconds)} since the previous bar in this series "
                    f"(~{gap_multiple:.1f}x the nominal {current.timeframe} interval)"
                )

        if previous.close > 0:
            move = abs(current.close / previous.close - 1)
            if move > _EXTREME_MOVE_THRESHOLD:
                warnings_by_id[id(current)].append(
                    f"close moved {move:.1%} from the previous bar in this series' close "
                    f"({previous.close} -> {current.close})"
                )
