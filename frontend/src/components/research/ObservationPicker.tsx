import { useEffect, useState } from "react";
import { createObservation, listObservations } from "../../api/client";
import type { Observation } from "../../types/researchNotebook";
import { apiErrorMessage } from "../../utils/researchFormat";

/**
 * OBSERVE stage (spec section 6): "what actually happened" -- a
 * structured, falsifiable description of market behavior, independent
 * of any hypothesis it might later inspire. Lets a caller pick an
 * existing Observation for `symbol`, or quick-create a new one inline.
 * Selecting/creating sets `originating_observation_id` on the
 * experiment being built -- purely a link, never re-editable once the
 * experiment exists (matching Observation's own "no edit endpoint"
 * immutability).
 */
export function ObservationPicker({
  symbol,
  selectedId,
  onSelect,
}: {
  symbol: string;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const [observations, setObservations] = useState<Observation[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [description, setDescription] = useState("");
  const [observedStart, setObservedStart] = useState("");
  const [observedEnd, setObservedEnd] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listObservations(symbol)
      .then((list) => !cancelled && setObservations(list))
      .catch(() => !cancelled && setObservations([]));
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  async function handleCreate() {
    if (!description.trim() || !observedStart || !observedEnd) return;
    setSaving(true);
    setError(null);
    try {
      const obs = await createObservation({
        symbol,
        description: description.trim(),
        observed_start: new Date(observedStart).toISOString(),
        observed_end: new Date(observedEnd).toISOString(),
      });
      setObservations((prev) => [obs, ...(prev ?? [])]);
      onSelect(obs.id);
      setCreating(false);
      setDescription("");
    } catch (err) {
      setError(apiErrorMessage(err, "Could not save this observation."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="observation-picker">
      <p className="field-hint">
        What did you actually notice in the data, before forming a hypothesis? Optional, but keeps a
        hypothesis traceable back to a real, falsifiable observation instead of a vague impression.
      </p>
      {observations === null && <p className="research-gap-note">Loading observations…</p>}
      {observations !== null && observations.length > 0 && !creating && (
        <select
          value={selectedId ?? ""}
          onChange={(e) => onSelect(e.target.value === "" ? null : e.target.value)}
        >
          <option value="">— none linked —</option>
          {observations.map((o) => (
            <option key={o.id} value={o.id}>
              {o.description.slice(0, 80)}
              {o.description.length > 80 ? "…" : ""} ({o.observed_start.slice(0, 10)})
            </option>
          ))}
        </select>
      )}
      {!creating && (
        <button type="button" className="observation-picker-new-btn" onClick={() => setCreating(true)}>
          + New observation
        </button>
      )}
      {creating && (
        <div className="observation-picker-form">
          <label className="field">
            <span className="field-label">What happened</span>
            <textarea
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="TSLA gapped down 3% at the open on 2x average volume, then continued declining through the first 30 minutes."
            />
          </label>
          <div className="experiment-form-row">
            <label className="field">
              <span className="field-label">Observed start</span>
              <input type="datetime-local" value={observedStart} onChange={(e) => setObservedStart(e.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">Observed end</span>
              <input type="datetime-local" value={observedEnd} onChange={(e) => setObservedEnd(e.target.value)} />
            </label>
          </div>
          {error && <div className="error-banner">{error}</div>}
          <div className="observation-picker-actions">
            <button type="button" onClick={handleCreate} disabled={saving}>
              {saving ? "Saving…" : "Save observation"}
            </button>
            <button type="button" className="experiment-form-cancel" onClick={() => setCreating(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
