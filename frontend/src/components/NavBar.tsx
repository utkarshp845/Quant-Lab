/**
 * App-level navigation. Deliberately local `useState` in App.tsx, not
 * a router library -- this app has never had one, and the existing
 * mode-toggle pattern already used inside InputsPanel.tsx/
 * CsvImportWorkflow.tsx (`role="tablist"`/`role="tab"`, styled via
 * `.mode-tab`) is exactly this same shape, just scoped to the whole
 * page instead of one panel -- reused here rather than introducing
 * react-router for one nav bar.
 *
 * Redesign (research-centered workbench): the nav collapses to four
 * items -- RESEARCH (default landing page, the primary destination),
 * DATA, FEATURES, CALCULATOR -- per the spec's "do not fragment the
 * application unnecessarily" instruction. Backtesting/OOS/Statistical
 * Validation are no longer separate top-level tabs (the old NavBar had
 * "Backtesting"/"Paper Trading" permanently disabled, which had gone
 * stale -- Backtesting v1 has been fully implemented and API-exposed
 * since v0.1.25): they are reached FROM inside an experiment's
 * pipeline (see components/research/ResearchPipeline.tsx), the stage
 * they conceptually belong to, not a parallel navigation structure a
 * user has to separately remember to visit. Paper Trading/Live remain
 * an explicit, honest placeholder INSIDE the pipeline (nothing in this
 * codebase implements them yet), not a nav item promising a page that
 * doesn't exist.
 */

export type PageKey = "research" | "data" | "features" | "calculator";

interface NavItem {
  key: PageKey;
  label: string;
}

const NAV_ITEMS: NavItem[] = [
  { key: "research", label: "Research" },
  { key: "data", label: "Data" },
  { key: "features", label: "Features" },
  { key: "calculator", label: "Calculator" },
];

export function NavBar({ active, onNavigate }: { active: PageKey; onNavigate: (key: PageKey) => void }) {
  return (
    <nav className="app-nav" aria-label="Primary">
      <span className="app-nav-brand">Pandey Quant Lab</span>
      <div className="app-nav-items" role="tablist" aria-label="Workspace">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={active === item.key}
            className={active === item.key ? "app-nav-tab app-nav-tab-active" : "app-nav-tab"}
            onClick={() => onNavigate(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>
    </nav>
  );
}
