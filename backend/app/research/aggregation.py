"""Aggregate statistics for a completed Research v1 experiment (app/
research/) -- turns a flat list of ExperimentEvent into the summary
numbers app/models/research.py::ExperimentResults exposes. Uses only
Python's stdlib `statistics` module (mean/median/stdev) -- no new
dependency, the same "lean on the standard library before reaching for
scipy/numpy" judgment app/calculations/stats.py already applies to its
own normal_cdf().
"""

import statistics as _statistics

from app.models.research import ExperimentEvent, ExperimentResults

# statistics.stdev (sample standard deviation) is mathematically
# undefined for fewer than two observations -- Python's own statistics
# module raises StatisticsError below this. Rather than let that
# exception escape (or silently substitute 0.0/NaN, which the spec's
# "handle it explicitly... rather than returning misleading values"
# requirement rules out), this constant names the real constraint so
# aggregate_results() can check it up front and report None instead.
MIN_OBSERVATIONS_FOR_STDEV = 2


def aggregate_results(events: list[ExperimentEvent]) -> ExperimentResults:
    """Computes ExperimentResults from `events` -- the events already
    produced by app/research/engine.py::run_experiment(), never a
    database read of its own (this function is pure). Zero events is a
    legitimate outcome (a hypothesis that never fired in the requested
    window), not an error: every statistic is None in that case rather
    than a divide-by-zero or a fabricated 0.0.
    """
    total = len(events)
    if total == 0:
        return ExperimentResults(
            total_events=0,
            successful_events=0,
            failed_events=0,
            success_rate=None,
            average_outcome=None,
            median_outcome=None,
            min_outcome=None,
            max_outcome=None,
            std_dev_outcome=None,
        )

    outcome_values = [event.outcome_value for event in events]
    successful = sum(1 for event in events if event.success)

    return ExperimentResults(
        total_events=total,
        successful_events=successful,
        failed_events=total - successful,
        success_rate=successful / total,
        average_outcome=_statistics.mean(outcome_values),
        median_outcome=_statistics.median(outcome_values),
        min_outcome=min(outcome_values),
        max_outcome=max(outcome_values),
        std_dev_outcome=_statistics.stdev(outcome_values) if total >= MIN_OBSERVATIONS_FOR_STDEV else None,
    )
