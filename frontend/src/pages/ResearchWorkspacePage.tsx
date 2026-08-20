import { useEffect, useState } from "react";
import { ApiError, freezeExperiment, getExperiment, getExperimentEvents, listExperiments, runExperiment } from "../api/client";
import { DesignGroupPanel } from "../components/research/DesignGroupPanel";
import { ExperimentCompare } from "../components/research/ExperimentCompare";
import {
  ExperimentForm,
  prefillAsNewVersion,
  prefillFromExperiment,
  prefillWithFeature,
  type ExperimentFormPrefill,
} from "../components/research/ExperimentForm";
import { ExperimentList } from "../components/research/ExperimentList";
import { ExperimentResultsView } from "../components/research/ExperimentResultsView";
import { ResearchPipeline } from "../components/research/ResearchPipeline";
import type { Experiment, ExperimentEvent } from "../types/research";

type View = "list" | "form" | "results" | "compare" | "designGroup";

/**
 * The primary experimental workflow (per this feature's own framing --
 * Feature Explorer is an inspection tool, this is where research
 * actually happens). Orchestrates: list saved experiments -> create/
 * duplicate -> view/run -> results -> compare. Every calculation shown
 * anywhere in this tree came from app/api/research.py -- this page and
 * its children only fetch, display, and (for warnings) apply simple
 * threshold heuristics over values already returned.
 */
export function ResearchWorkspacePage({
  pendingFeatureId,
  onConsumePendingFeature,
}: {
  /** Set by Market State Explorer's "Use this feature in Research"
   * action (App.tsx) -- when present, opens a new-experiment form
   * prefilled with a starter condition referencing it. */
  pendingFeatureId?: string | null;
  onConsumePendingFeature?: () => void;
} = {}) {
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

  const [designGroupId, setDesignGroupId] = useState<string | null>(null);
  const [freezing, setFreezing] = useState(false);
  const [freezeError, setFreezeError] = useState<string | null>(null);

  // Bumped whenever the active experiment's server-side state might
  // have changed (run, freeze, backtest, ...) so ResearchPipeline
  // re-fetches pipeline-status instead of showing a stale stage list.
  const [pipelineRefreshKey, setPipelineRefreshKey] = useState(0);

  useEffect(() => {
    reloadExperiments();
  }, []);

  useEffect(() => {
    if (!pendingFeatureId) return;
    setFormPrefill(prefillWithFeature(pendingFeatureId));
    setView("form");
    onConsumePendingFeature?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingFeatureId]);

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
      setPipelineRefreshKey((k) => k + 1);
    } catch (err) {
      setRunError(err instanceof ApiError ? err.message : "Could not reach the backend. Is it running on http://localhost:8000?");
    } finally {
      setRunning(false);
    }
  }

  async function handleFreeze() {
    if (!activeExperiment) return;
    setFreezing(true);
    setFreezeError(null);
    try {
      const updated = await freezeExperiment(activeExperiment.id);
      setActiveExperiment(updated);
      setExperiments((prev) => (prev ?? []).map((e) => (e.id === updated.id ? updated : e)));
      setPipelineRefreshKey((k) => k + 1);
    } catch (err) {
      setFreezeError(err instanceof ApiError ? err.message : "Could not freeze this experiment.");
    } finally {
      setFreezing(false);
    }
  }

  function handleNewVersion() {
    if (!activeExperiment) return;
    setFormPrefill(prefillAsNewVersion(activeExperiment, ""));
    setView("form");
  }

  function handleExperimentUpdated(updated: Experiment) {
    setActiveExperiment(updated);
    setExperiments((prev) => (prev ?? []).map((e) => (e.id === updated.id ? updated : e)));
    setPipelineRefreshKey((k) => k + 1);
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
          The lab's home workspace: observe → hypothesize → design → define → lock → detect →
          measure → compare → validate → conclude → backtest → OOS. Every stage traces back to real
          market data; nothing here modifies historical data or fabricates a result.
        </p>
      </header>

      {view === "list" && (
        <div className="research-gap-banner">
          <strong>Scoped to what the Research Engine supports today:</strong> one symbol, any number
          of conditions ANDed together (each referencing a real Feature Engine value -- see the
          condition builder's dropdown), one forward-return outcome per experiment. Multi-symbol
          universes, OR/nested condition groups, percentile statistics, threshold-probability, and
          segmentation are shown in this UI where they're part of the workflow but are visibly
          disabled -- each requires a backend extension not built yet, per this workspace's own "no
          duplicate backend calculations" rule.
        </div>
      )}

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
          <ResearchPipeline experimentId={activeExperiment.id} refreshKey={pipelineRefreshKey} />
          <ExperimentResultsView
            experiment={activeExperiment}
            events={activeEvents}
            eventsLoading={eventsLoading}
            eventsError={eventsError}
            running={running}
            runError={runError}
            onRun={handleRun}
            sameSymbolExperimentCount={sameSymbolExperimentCount(activeExperiment.symbol)}
            freezing={freezing}
            freezeError={freezeError}
            onFreeze={handleFreeze}
            onViewDesignGroup={(id) => {
              setDesignGroupId(id);
              setView("designGroup");
            }}
            onViewVersion={goToView}
            onNewVersion={handleNewVersion}
            onExperimentUpdated={handleExperimentUpdated}
          />
        </>
      )}

      {view === "compare" && compareExperiments && (
        <ExperimentCompare left={compareExperiments[0]} right={compareExperiments[1]} onBack={() => setView("list")} />
      )}

      {view === "designGroup" && designGroupId && (
        <DesignGroupPanel designGroupId={designGroupId} onBack={() => setView(activeExperiment ? "results" : "list")} />
      )}
    </div>
  );
}
