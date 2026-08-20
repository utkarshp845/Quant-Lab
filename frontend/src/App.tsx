import { useState } from "react";
import { NavBar, type PageKey } from "./components/NavBar";
import { CalculatorPage } from "./pages/CalculatorPage";
import { DataPage } from "./pages/DataPage";
import { FeatureExplorerPage } from "./pages/FeatureExplorerPage";
import { ResearchWorkspacePage } from "./pages/ResearchWorkspacePage";

/**
 * Research is the default landing page and primary destination (spec
 * section 4) -- the research pipeline (see ResearchWorkspacePage/
 * ResearchPipeline) is this app's mental model, not a fourth peer of
 * Data/Features/Calculator.
 */
export default function App() {
  const [page, setPage] = useState<PageKey>("research");

  // Market State Explorer's "Use this feature in Research" action
  // (spec section 13) -- lifted here since it crosses pages: set on
  // Features, navigated to Research, consumed (and cleared) once
  // ResearchWorkspacePage opens a new-experiment form prefilled with it.
  const [pendingFeatureId, setPendingFeatureId] = useState<string | null>(null);

  function useFeatureInResearch(featureId: string) {
    setPendingFeatureId(featureId);
    setPage("research");
  }

  return (
    <>
      <NavBar active={page} onNavigate={setPage} />
      {page === "research" && (
        <ResearchWorkspacePage pendingFeatureId={pendingFeatureId} onConsumePendingFeature={() => setPendingFeatureId(null)} />
      )}
      {page === "data" && <DataPage />}
      {page === "features" && <FeatureExplorerPage onUseInResearch={useFeatureInResearch} />}
      {page === "calculator" && <CalculatorPage />}
    </>
  );
}
