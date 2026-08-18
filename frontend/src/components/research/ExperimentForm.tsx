import { useMemo, useState } from "react";
import { ApiError, createExperiment } from "../../api/client";
import type { Timeframe } from "../../types/marketData";
import type { ConditionOperator, Experiment, ExperimentCreateRequest, FeatureCondition } from "../../types/research";
import { ConditionBuilder } from "./ConditionBuilder";
import { OutcomeBuilder } from "./OutcomeBuilder";
import { dateRangeWarnings } from "./researchWarnings";
import { WarningsPanel } from "./WarningsPanel";

// Research v1's backend hard-restricts `symbol` to ALLOWED_SYMBOLS
// (backend/app/api/historical_data.py) -- unlike Feature Explorer's
// free-text symbol field, this is a closed dropdown so a request can
// never fail with "symbol not supported" after the user already filled
// out a whole form.
const RESEARCH_SYMBOLS = ["TSLA", "NVDA"];
const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "1h", "1d"];
const PROVIDERS = [
  { value: "alpaca", label: "Alpaca" },
  { value: "massive", label: "Massive" },
  { value: "csv", label: "CSV / manually saved" },
];

function daysAgoIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}
function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export interface ExperimentFormPrefill {
  name: string;
  hypothesis: string;
  symbol: string;
  start_date: string;
  end_date: string;
  timeframe: string;
  provider: string;
  conditions: FeatureCondition[];
  outcomeHorizon: number;
  outcomeOperator: ConditionOperator;
  outcomeThreshold: number;
}

/** Builds a form-shaped prefill from an existing Experiment -- used by
 * the "Duplicate" action in ExperimentList. Since the backend keeps an
 * experiment's parameters immutable after creation (by design, for
 * reproducibility), "duplicate" -> edit -> create-new is how this
 * workspace supports both "re-run the same experiment" (just POST
 * .../run again, no duplication needed) and "re-run against a
 * different dataset/date range" (this path). */
export function prefillFromExperiment(experiment: Experiment): ExperimentFormPrefill {
  return {
    name: `${experiment.name} (copy)`,
    hypothesis: experiment.hypothesis,
    symbol: experiment.symbol,
    start_date: experiment.start_date,
    end_date: experiment.end_date,
    timeframe: experiment.timeframe,
    provider: experiment.provider,
    conditions: experiment.conditions,
    outcomeHorizon: experiment.outcome.horizon_minutes,
    outcomeOperator: experiment.outcome.operator,
    outcomeThreshold: experiment.outcome.threshold,
  };
}

const BLANK_PREFILL: ExperimentFormPrefill = {
  name: "",
  hypothesis: "",
  symbol: RESEARCH_SYMBOLS[0],
  start_date: daysAgoIso(90),
  end_date: todayIso(),
  timeframe: "5m",
  provider: "alpaca",
  conditions: [{ feature_id: "price.return_30m", operator: "<=", value: -0.01 }],
  outcomeHorizon: 60,
  outcomeOperator: "<=",
  outcomeThreshold: -0.005,
};

export function ExperimentForm({
  prefill,
  sameSymbolExperimentCount,
  onCreated,
  onCancel,
}: {
  prefill?: ExperimentFormPrefill;
  sameSymbolExperimentCount: (symbol: string) => number;
  onCreated: (experiment: Experiment) => void;
  onCancel: () => void;
}) {
  const initial = prefill ?? BLANK_PREFILL;

  const [name, setName] = useState(initial.name);
  const [hypothesis, setHypothesis] = useState(initial.hypothesis);
  const [symbol, setSymbol] = useState(initial.symbol);
  const [startDate, setStartDate] = useState(initial.start_date);
  const [endDate, setEndDate] = useState(initial.end_date);
  const [timeframe, setTimeframe] = useState<Timeframe>(initial.timeframe as Timeframe);
  const [provider, setProvider] = useState(initial.provider);
  const [conditions, setConditions] = useState<FeatureCondition[]>(initial.conditions);
  const [outcome, setOutcome] = useState({
    metric: "forward_return" as const,
    horizon_minutes: initial.outcomeHorizon,
    operator: initial.outcomeOperator,
    threshold: initial.outcomeThreshold,
  });

  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<string[]>([]);

  const warnings = useMemo(() => dateRangeWarnings(startDate, endDate), [startDate, endDate]);
  const existingCount = sameSymbolExperimentCount(symbol);

  const canSubmit =
    name.trim() !== "" && hypothesis.trim() !== "" && conditions.length > 0 && endDate >= startDate && !submitting;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;

    setSubmitting(true);
    setErrorMessage(null);
    setFieldErrors([]);
    const request: ExperimentCreateRequest = {
      name: name.trim(),
      hypothesis: hypothesis.trim(),
      symbol,
      start_date: startDate,
      end_date: endDate,
      timeframe,
      provider,
      conditions,
      outcome,
    };
    try {
      const experiment = await createExperiment(request);
      onCreated(experiment);
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMessage(err.message);
        setFieldErrors(err.details);
      } else {
        setErrorMessage("Could not reach the backend. Is it running on http://localhost:8000?");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="section experiment-form" onSubmit={handleSubmit}>
      <h2 className="section-title">{prefill ? "Duplicate experiment" : "New experiment"}</h2>

      <label className="field">
        <span className="field-label">Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="TSLA Early Selling Continuation" />
      </label>

      <label className="field">
        <span className="field-label">Hypothesis</span>
        <textarea
          className="experiment-hypothesis-input"
          rows={2}
          value={hypothesis}
          onChange={(e) => setHypothesis(e.target.value)}
          placeholder="When TSLA declines >= 1% during the first 30 minutes, it declines another >= 0.5% during the next 60 minutes."
        />
      </label>

      <div className="experiment-form-row">
        <label className="field">
          <span className="field-label">Symbol (universe)</span>
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {RESEARCH_SYMBOLS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Timeframe</span>
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value as Timeframe)}>
            {TIMEFRAMES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Provider (dataset)</span>
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            {PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <button
        type="button"
        className="condition-add-btn"
        disabled
        title="Requires a backend extension -- v1 experiments run against exactly one symbol."
      >
        + Add symbol to universe
      </button>

      <div className="experiment-form-row">
        <label className="field">
          <span className="field-label">Start date</span>
          <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">End date</span>
          <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
        </label>
      </div>

      <WarningsPanel warnings={warnings} />

      <h3 className="experiment-form-subheading">Conditions (AND)</h3>
      <ConditionBuilder conditions={conditions} onChange={setConditions} />

      <h3 className="experiment-form-subheading">Outcome</h3>
      <OutcomeBuilder value={outcome} onChange={setOutcome} />

      {existingCount > 0 && (
        <p className="experiment-form-note">
          You already have {existingCount} experiment{existingCount === 1 ? "" : "s"} on {symbol}.
        </p>
      )}

      {errorMessage && (
        <div className="error-banner">
          {errorMessage}
          {fieldErrors.length > 0 && (
            <ul>
              {fieldErrors.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {conditions.length === 0 && <div className="error-banner">At least one condition is required.</div>}
      {endDate < startDate && <div className="error-banner">End date must not be before start date.</div>}

      <div className="experiment-form-actions">
        <button type="submit" disabled={!canSubmit}>
          {submitting ? "Saving…" : "Save experiment"}
        </button>
        <button type="button" className="experiment-form-cancel" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
