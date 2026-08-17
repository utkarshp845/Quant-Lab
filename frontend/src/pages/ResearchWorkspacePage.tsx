import { useEffect, useState } from "react";
import { ApiError, getExperiment, getExperimentEvents, listExperiments, runExperiment } from "../api/client";
import { ExperimentCompare } from "../components/research/ExperimentCompare";
import { ExperimentForm, prefillFromExperiment, type ExperimentFormPrefill } from "../components/research/ExperimentForm";
import { ExperimentList } from "../components/research/ExperimentList";
import { ExperimentResultsView } from "../components/research/ExperimentResultsView";
import type { Experiment, ExperimentEvent } from "../types/research";

type View = "list" | "form" | "results" | "compare";

/**
 * The primary experimental workflow (per this feature's own framing --
 * Feature Explorer is an inspection tool, this is where research
 * actually happens). Orchestrates: list saved experiments -> create/
 * duplicate -> view/run -> results -> compare. Every calculation shown
 * anywhere in this tree came from app/api/research.py -- this page and
 * its children only fetch, display, and (for warnings) apply simple
 * threshold heuristics over values already returned.
 */
export function ResearchWorkspacePage() {
  const [view, setView] = useState<View>("list");
  const [experiments, setExperiments] = useState<Experiment[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const [formPrefill, setFormPrefill] = useState<ExperimentFormPrefill | undefined>(undefined);

  const [activeExperiment, setActiveExperiment] = useState<Experiment | null>(null);
  const [activeEvents, setActiveEvents] = useState<ExperimentEvent[] | null>(null);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const [compareExperiments, setCompareExperiments] = useState<[Experiment, Experiment] | null>(null);

  useEffect(() => {
    reloadExperiments();
  }, []);

  async function reloadExperiments() {
    try {
      const list = await listExperiments();
      setExperiments(list);
      setListError(null);
    } catch (err) {
      setListError(
        err instanceof ApiError ? err.message : "Could not reach the backend. Is it running on http://localhost:8000?",
      );
    }
  }

  function sameSymbolExperimentCount(symbol: string): number {
    return (experiments ?? []).filter((e) => e.symbol === symbol).length;
  }

  async function loadEventsFor(experiment: Experiment) {
    if (experiment.status !== "completed" || !experiment.results || experiment.results.total_events === 0) {
      setActiveEvents(null);
      return;
    }
    setEventsLoading(true);
    setEventsError(null);
    try {
      const result = await getExperimentEvents(experiment.id);
      setActiveEvents(result.events);
    } catch (err) {
      setActiveEvents(null);
      setEventsError(err instanceof ApiError ? err.message : "Could not load individual signal events.");
    } finally {
      setEventsLoading(false);
    }
  }

  function goToCreate() {
    setFormPrefill(undefined);
    setView("form");
  }

  function goToDuplicate(experiment: Experiment) {
    setFormPrefill(prefillFromExperiment(experiment));
    setView("form");
  }

  function handleCreated(experiment: Experiment) {
    setExperiments((prev) => [experiment, ...(prev ?? [])]);
    setActiveExperiment(experiment);
    setActiveEvents(null);
    setView("results");
  }

  async function goToView(id: string) {
    const existing = (experiments ?? []).find((e) => e.id === id);
    if (existing) {
      setActiveExperiment(existing);
      setView("results");
      await loadEventsFor(existing);
      return;
    }
    try {
      const fresh = await getExperiment(id);
      setActiveExperiment(fresh);
      setView("results");
      await loadEventsFor(fresh);
    } catch (err) {
      setListError(err instanceof ApiError ? err.message : "Could not load that experiment.");
    }
  }

  async function handleRun() {
    if (!activeExperiment) return;
    setRunning(true);
    setRunError(null);
    try {
      const updated = await runExperiment(activeExperiment.id);
      setActiveExperiment(updated);
      setExperiments((prev) => (prev ?? []).map((e) => (e.id === updated.id ? updated : e)));
      await loadEventsFor(updated);
    } catch (err) {
      setRunError(err instanceof ApiError ? err.message : "Could not reach the backend. Is it running on http://localhost:8000?");
    } finally {
      setRunning(false);
    }
  }

  async function handleRerunFromList(id: string) {
    const updated = await runExperiment(id);
    setExperiments((prev) => (prev ?? []).map((e) => (e.id === updated.id ? updated : e)));
  }

  async function handleCompare([idA, idB]: [string, string]) {
    const findOrFetch = async (id: string) => (experiments ?? []).find((e) => e.id === id) ?? (await getExperiment(id));
    try {
      const [a, b] = await Promise.all([findOrFetch(idA), findOrFetch(idB)]);
      setCompareExperiments([a, b]);
      setView("compare");
    } catch (err) {
      setListError(err instanceof ApiError ? err.message : "Could not load both experiments to compare.");
    }
  }

  return (
    <div className="page research-workspace">
      <header className="page-header">
        <h1>Research</h1>
        <p className="tagline">
          Define a falsifiable condition/outcome hypothesis, run it against the normalized historical dataset, and
          see every qualifying signal plus aggregate statistics. Deterministic, reproducible, never modifies
          historical data. Not backtesting, not a trading signal.
        </p>
      </header>

      <div className="research-gap-banner">
        <strong>Scoped to what the Research Engine supports today:</strong> one symbol, one condition
        (trailing-return only), one forward-return outcome per experiment. Multi-symbol universes, AND/OR
        condition groups over any feature, percentile statistics, threshold-probability, and segmentation are shown
        in this UI where they're part of the workflow but are visibly disabled -- each requires a backend
        extension not built yet, per this workspace's own "no duplicate backend calculations" rule.
      </div>

      {view === "list" && (
        <>
          <div className="experiment-list-header">
            <button type="button" onClick={goToCreate}>
              + New experiment
            </button>
          </div>
          {listError && <div className="error-banner">{listError}</div>}
          {experiments === null && !listError && <p>Loading experiments…</p>}
          {experiments !== null && (
            <ExperimentList
              experiments={experiments}
              onView={goToView}
              onDuplicate={goToDuplicate}
              onRerun={handleRerunFromList}
              onCompare={handleCompare}
            />
          )}
        </>
      )}

      {view === "form" && (
        <ExperimentForm
          prefill={formPrefill}
          sameSymbolExperimentCount={sameSymbolExperimentCount}
          onCreated={handleCreated}
          onCancel={() => setView("list")}
        />
      )}

      {view === "results" && activeExperiment && (
        <>
          <div className="experiment-list-header">
            <button type="button" onClick={() => setView("list")}>
              ← Back to list
            </button>
          </div>
          <ExperimentResultsView
            experiment={activeExperiment}
            events={activeEvents}
            eventsLoading={eventsLoading}
            eventsError={eventsError}
            running={running}
            runError={runError}
            onRun={handleRun}
            sameSymbolExperimentCount={sameSymbolExperimentCount(activeExperiment.symbol)}
          />
        </>
      )}

      {view === "compare" && compareExperiments && (
        <ExperimentCompare left={compareExperiments[0]} right={compareExperiments[1]} onBack={() => setView("list")} />
      )}
    </div>
  );
}
