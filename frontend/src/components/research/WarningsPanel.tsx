import type { ResearchWarning } from "./researchWarnings";

export function WarningsPanel({ warnings }: { warnings: ResearchWarning[] }) {
  if (warnings.length === 0) return null;
  return (
    <div className="research-warnings">
      {warnings.map((w) => (
        <div key={w.id} className={`research-warning research-warning-${w.severity}`}>
          {w.message}
        </div>
      ))}
    </div>
  );
}
