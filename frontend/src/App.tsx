import { useState } from "react";
import { ComingSoonPanel } from "./components/ComingSoonPanel";
import { NavBar, type PageKey } from "./components/NavBar";
import { CalculatorPage } from "./pages/CalculatorPage";
import { FeatureExplorerPage } from "./pages/FeatureExplorerPage";
import { ResearchWorkspacePage } from "./pages/ResearchWorkspacePage";

export default function App() {
  const [page, setPage] = useState<PageKey>("calculator");

  return (
    <>
      <NavBar active={page} onNavigate={setPage} />
      {page === "calculator" && <CalculatorPage />}
      {page === "features" && <FeatureExplorerPage />}
      {page === "research" && <ResearchWorkspacePage />}
      {page === "data" && (
        <div className="page">
          <ComingSoonPanel
            title="Data"
            reason="A dedicated data-management page is not built yet -- CSV import, live quotes, streaming, and historical-bar fetch/save/compare are all still on the Calculator page for now."
          />
        </div>
      )}
      {page === "backtesting" && (
        <div className="page">
          <ComingSoonPanel
            title="Backtesting"
            reason="Not implemented. Research v1 measures whether a condition/outcome pattern held historically -- it does not simulate a position's entry/exit, P&L, or capital over time."
          />
        </div>
      )}
      {page === "paper-trading" && (
        <div className="page">
          <ComingSoonPanel
            title="Paper Trading"
            reason="Not implemented. No simulated account, no order execution, at any point in this project."
          />
        </div>
      )}
    </>
  );
}
