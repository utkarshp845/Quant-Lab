/**
 * Frontend-only research-discipline heuristics (small sample / limited
 * history / in-sample / many-conditions / multiple-testing). These are
 * threshold checks over values already known to the browser (a chosen
 * date range, an already-fetched event count, how many experiments
 * exist) -- not a research calculation the backend should own, the
 * same way a form showing "this field looks too short" is UI
 * judgment, not business logic. Nothing here computes a statistic
 * about experiment results; see the Research Engine gap notice on
 * segmentation/percentiles for where that line actually gets drawn in
 * this workspace.
 */

export interface ResearchWarning {
  id: string;
  severity: "info" | "warning";
  message: string;
}

const SHORT_RANGE_DAYS = 14;
const IN_SAMPLE_RECENCY_DAYS = 30;
const SMALL_SAMPLE_THRESHOLD = 30;
const MULTIPLE_TESTING_THRESHOLD = 5;

export function dateRangeWarnings(startDate: string, endDate: string): ResearchWarning[] {
  const warnings: ResearchWarning[] = [];
  const start = new Date(`${startDate}T00:00:00Z`);
  const end = new Date(`${endDate}T00:00:00Z`);
  const rangeDays = Math.round((end.getTime() - start.getTime()) / 86_400_000);

  if (rangeDays >= 0 && rangeDays < SHORT_RANGE_DAYS) {
    warnings.push({
      id: "short-range",
      severity: "warning",
      message: `This date range spans only ${rangeDays} day(s) -- likely insufficient historical data for a reliable statistic.`,
    });
  }

  const daysSinceEnd = Math.round((Date.now() - end.getTime()) / 86_400_000);
  if (daysSinceEnd < IN_SAMPLE_RECENCY_DAYS) {
    warnings.push({
      id: "in-sample",
      severity: "info",
      message:
        "This range includes recent data you may have already observed while forming this hypothesis -- treat this as an in-sample check, not a genuine out-of-sample test.",
    });
  }

  return warnings;
}

export function sampleSizeWarnings(totalEvents: number): ResearchWarning[] {
  if (totalEvents === 0) {
    return [
      {
        id: "no-events",
        severity: "warning",
        message: "No qualifying signals were found in this date range -- there is nothing to draw a conclusion from.",
      },
    ];
  }
  if (totalEvents < SMALL_SAMPLE_THRESHOLD) {
    return [
      {
        id: "small-sample",
        severity: "warning",
        message: `Only ${totalEvents} qualifying signal(s) found -- statistics from a sample this small can easily be noise, not a real pattern.`,
      },
    ];
  }
  return [];
}

/** v1 experiments always have exactly one condition (see
 * ConditionBuilder's own docstring) -- this never fires today, but is
 * written against `conditionCount` (not hardcoded to 1) so it starts
 * working automatically the moment multi-condition support exists. */
export function conditionCountWarnings(conditionCount: number): ResearchWarning[] {
  if (conditionCount > 1) {
    return [
      {
        id: "many-conditions",
        severity: "warning",
        message: `This experiment combines ${conditionCount} conditions -- more conditions make a match rarer and more likely to reflect a coincidence in this specific dataset rather than a real pattern.`,
      },
    ];
  }
  return [];
}

export function multipleTestingWarning(experimentsOnSameSymbol: number): ResearchWarning[] {
  if (experimentsOnSameSymbol > MULTIPLE_TESTING_THRESHOLD) {
    return [
      {
        id: "multiple-testing",
        severity: "info",
        message: `You've run ${experimentsOnSameSymbol} experiments against this symbol. Testing many hypotheses against the same dataset raises the odds that at least one looks significant by chance alone -- treat any single "winning" result with extra skepticism.`,
      },
    ];
  }
  return [];
}
