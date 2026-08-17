import { useState } from "react";
import type { Experiment } from "../../types/research";

const STATUS_LABELS: Record<Experiment["status"], string> = {
  draft: "Draft",
  running: "Running…",
  completed: "Completed",
  failed: "Failed",
};

export function ExperimentList({
  experiments,
  onView,
  onDuplicate,
  onRerun,
  onCompare,
}: {
  experiments: Experiment[];
  onView: (id: string) => void;
  onDuplicate: (experiment: Experiment) => void;
  onRerun: (id: string) => Promise<void>;
  onCompare: (ids: [string, string]) => void;
}) {
  const [rerunningId, setRerunningId] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);

  async function handleRerun(id: string) {
    setRerunningId(id);
    try {
      await onRerun(id);
    } finally {
      setRerunningId(null);
    }
  }

  function toggleSelected(id: string) {
    setSelected((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= 2) return [prev[1], id]; // keep at most 2, sliding window
      return [...prev, id];
    });
  }

  if (experiments.length === 0) {
    return (
      <section className="section">
        <p className="experiment-list-empty">
          No experiments saved yet. Create one to search historical data for a condition/outcome pattern.
        </p>
      </section>
    );
  }

  return (
    <section className="section">
      <div className="experiment-list-header">
        <h2 className="section-title">Saved experiments</h2>
        <button
          type="button"
          disabled={selected.length !== 2}
          onClick={() => selected.length === 2 && onCompare([selected[0], selected[1]])}
        >
          Compare selected ({selected.length}/2)
        </button>
      </div>
      <div className="table-wrap">
        <table className="payoff-table experiment-table">
          <thead>
            <tr>
              <th aria-label="Select for comparison" />
              <th>Name</th>
              <th>Symbol</th>
              <th>Date range</th>
              <th>Status</th>
              <th>Signals</th>
              <th>Success rate</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {experiments.map((exp) => (
              <tr key={exp.id}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.includes(exp.id)}
                    onChange={() => toggleSelected(exp.id)}
                    disabled={exp.status !== "completed"}
                    title={exp.status !== "completed" ? "Only completed experiments can be compared" : "Select to compare"}
                  />
                </td>
                <td>{exp.name}</td>
                <td>{exp.symbol}</td>
                <td>
                  {exp.start_date} → {exp.end_date}
                </td>
                <td>
                  <span className={`experiment-status experiment-status-${exp.status}`}>{STATUS_LABELS[exp.status]}</span>
                </td>
                <td>{exp.results ? exp.results.total_events : "—"}</td>
                <td>
                  {exp.results?.success_rate !== null && exp.results?.success_rate !== undefined
                    ? `${(exp.results.success_rate * 100).toFixed(1)}%`
                    : "—"}
                </td>
                <td className="experiment-table-actions">
                  <button type="button" onClick={() => onView(exp.id)}>
                    View
                  </button>
                  <button type="button" onClick={() => onDuplicate(exp)}>
                    Duplicate
                  </button>
                  <button type="button" onClick={() => handleRerun(exp.id)} disabled={rerunningId === exp.id}>
                    {rerunningId === exp.id ? "Running…" : "Rerun"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
