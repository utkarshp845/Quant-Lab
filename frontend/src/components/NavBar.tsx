/**
 * App-level navigation (added alongside Feature Explorer / Research
 * Workspace). Deliberately local `useState` in App.tsx, not a router
 * library -- this app has never had one (a single hardcoded
 * `<CalculatorPage />` before this), and the existing mode-toggle
 * pattern already used inside InputsPanel.tsx/CsvImportWorkflow.tsx
 * (`role="tablist"`/`role="tab"`, styled via `.mode-tab`) is exactly
 * this same shape, just scoped to the whole page instead of one panel
 * -- reused here rather than introducing react-router for one nav bar.
 *
 * "Calculator" isn't in the spec's named nav list (Data/Features/
 * Research/Backtesting/Paper Trading) but stays as its own item: it's
 * the app's existing, unmodified product, and dropping it or folding
 * it into "Data" would mean either breaking the existing entry point
 * or restructuring CalculatorPage.tsx (which bundles the calculator
 * AND the market-data/CSV-import panels together today) -- both are
 * bigger changes than this task's "only build/modify the Feature and
 * Research workspaces" scope. "Data" is included as its own disabled
 * placeholder for the same reason "Backtesting"/"Paper Trading" are:
 * a dedicated data-management page distinct from the calculator does
 * not exist yet.
 */

export type PageKey = "calculator" | "data" | "features" | "research" | "backtesting" | "paper-trading";

interface NavItem {
  key: PageKey;
  label: string;
  enabled: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { key: "calculator", label: "Calculator", enabled: true },
  { key: "data", label: "Data", enabled: false },
  { key: "features", label: "Features", enabled: true },
  { key: "research", label: "Research", enabled: true },
  { key: "backtesting", label: "Backtesting", enabled: false },
  { key: "paper-trading", label: "Paper Trading", enabled: false },
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
            disabled={!item.enabled}
            title={item.enabled ? undefined : "Coming soon"}
            onClick={() => item.enabled && onNavigate(item.key)}
          >
            {item.label}
            {!item.enabled && <span className="app-nav-soon">soon</span>}
          </button>
        ))}
      </div>
    </nav>
  );
}
